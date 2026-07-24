import asyncio
import base64
import concurrent.futures
import logging
import math
import mimetypes
import os
import re
import signal
import sys
from collections import defaultdict
from copy import copy
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import config


class IllegalArgumentError(ValueError):
	pass

class Crawler:

	MAX_URLS_PER_SITEMAP = 50000
	PROGRESS_SAVE_INTERVAL = 50 # Save progress to the output file every N crawled pages

	# Variables
	parserobots = False
	output 	= None
	report 	= False

	config 	= None
	domain	= ""

	exclude = []
	skipext = []
	drop    = []

	debug	= False
	auth = False

	urls_to_crawl = set([])
	url_strings_to_output = []
	crawled_or_crawling = set([])
	excluded = set([])

	marked = defaultdict(list)

	not_parseable_resources = (".epub", ".mobi", ".docx", ".doc", ".opf", ".7z", ".ibooks", ".cbr", ".avi", ".mkv", ".mp4", ".jpg", ".jpeg", ".png", ".gif" ,".pdf", ".iso", ".rar", ".tar", ".tgz", ".zip", ".dmg", ".exe")

	# TODO also search for window.location={.*?}
	linkregex = re.compile(b'<a [^>]*href=[\'|"](.*?)[\'"][^>]*?>')
	imageregex = re.compile (b'<img ([^>]*)>')
	imagesrcregex = re.compile(b'(?<!-)\\bsrc=[\'"](.*?)[\'"]', re.IGNORECASE)
	imagealtregex = re.compile(b'(?<!-)\\balt=[\'"](.*?)[\'"]', re.IGNORECASE)
	imagetitleregex = re.compile(b'(?<!-)\\btitle=[\'"](.*?)[\'"]', re.IGNORECASE)
	figureregex = re.compile(b'<figure[^>]*>(.*?)</figure>', re.IGNORECASE | re.DOTALL)
	figcaptionregex = re.compile(b'<figcaption[^>]*>(.*?)</figcaption>', re.IGNORECASE | re.DOTALL)
	videoregex = re.compile(b'<video([^>]*)>(.*?)</video>', re.IGNORECASE | re.DOTALL)
	videosourceregex = re.compile(b'<source([^>]*)>', re.IGNORECASE)
	videoposterregex = re.compile(b'(?<!-)\\bposter=[\'"](.*?)[\'"]', re.IGNORECASE)
	tagstripregex = re.compile(b'<[^>]+>')
	iframeregex = re.compile (b'<iframe [^>]*src=[\'|"](.*?)[\'"].*?>')
	baseregex = re.compile (b'<base [^>]*href=[\'|"](.*?)[\'"].*?>')
	metaregex = re.compile(b'<meta\\s+[^>]*>', re.IGNORECASE)
	metanameregex = re.compile(b'name=[\'"](.*?)[\'"]', re.IGNORECASE)
	metacontentregex = re.compile(b'content=[\'"](.*?)[\'"]', re.IGNORECASE)
	linktagregex = re.compile(b'<link\\s+[^>]*>', re.IGNORECASE)
	linkrelregex = re.compile(b'rel=[\'"](.*?)[\'"]', re.IGNORECASE)
	linkhrefregex = re.compile(b'href=[\'"](.*?)[\'"]', re.IGNORECASE)
	linkhreflangregex = re.compile(b'hreflang=[\'"](.*?)[\'"]', re.IGNORECASE)

	rp = None
	response_code=defaultdict(int)
	nb_url=1 # Number of url.
	nb_rp=0 # Number of url blocked by the robots.txt
	nb_exclude=0 # Number of url excluded by extension or word

	target_domain = ""
	scheme		  = ""

	def __init__(self, num_workers=1, parserobots=False, output=None,
				 report=False ,domain="", exclude=[], skipext=[], drop=[],
				 debug=False, verbose=False, images=False, videos=False, auth=False, as_index=False,
				 fetch_iframes=False, sort_alphabetically=True, user_agent='*', resume=False,
				 respect_noindex=True, respect_canonical=True, hreflang=True):
		self.num_workers   = num_workers
		self.parserobots   = parserobots
		self.user_agent    = user_agent
		self.output 	   = output
		self.report 	   = report
		self.domain 	   = domain
		self.exclude 	   = exclude
		self.skipext 	   = skipext
		self.drop		   = drop
		self.debug		   = debug
		self.verbose       = verbose
		self.images        = images
		self.videos        = videos
		self.fetch_iframes = fetch_iframes
		self.auth          = auth
		self.as_index      = as_index
		self.sort_alphabetically = sort_alphabetically
		self.resume        = resume
		self.respect_noindex = respect_noindex
		self.respect_canonical = respect_canonical
		self.hreflang      = hreflang

		if self.debug:
			log_level = logging.DEBUG
		elif self.verbose:
			log_level = logging.INFO
		else:
			log_level = logging.ERROR

		logging.basicConfig(level=log_level)

		self.urls_to_crawl = {self.clean_link(domain)}
		self.url_strings_to_output = []
		self.num_crawled = 0
		# Urls that have been popped off urls_to_crawl but haven't finished
		# __crawl() yet. Needed so progress saved mid-crawl can re-queue them
		# instead of losing them (they're not in urls_to_crawl anymore, but
		# aren't fully processed either).
		self.in_flight = set()

		if num_workers <= 0:
			raise IllegalArgumentError("Number or workers must be positive")

		try:
			url_parsed = urlparse(domain)
			self.target_domain = url_parsed.netloc
			self.scheme = url_parsed.scheme
		except:
			logging.error("Invalid domain")
			raise IllegalArgumentError("Invalid domain")

		if self.output:
			try:
				# Resuming reads previously crawled urls out of this same file
				# below, so don't truncate it here; every later write (progress
				# saves and the final sitemap) opens the file in 'w' mode itself.
				open_mode = 'a' if self.resume else 'w'
				with open(self.output, open_mode):
					pass
			except:
				logging.error ("Output file not available.")
				exit(255)
		elif self.as_index:
			logging.error("When specifying an index file as an output option, you must include an output file name")
			exit(255)
		elif self.resume:
			logging.error("When resuming a crawl, you must include an output file name")
			exit(255)

	def run(self):
		if self.resume:
			self.load_progress_from_output()
			signal.signal(signal.SIGINT, self._handle_interrupt)
			signal.signal(signal.SIGTERM, self._handle_interrupt)

		if self.parserobots:
			self.check_robots()

		logging.info("Start the crawling process")

		if self.num_workers == 1:
			while len(self.urls_to_crawl) != 0:
				current_url = self.urls_to_crawl.pop()
				self.crawled_or_crawling.add(current_url)
				self.in_flight.add(current_url)
				self.__crawl(current_url)
				self.in_flight.discard(current_url)
				if self.resume and self.num_crawled % self.PROGRESS_SAVE_INTERVAL == 0:
					self.write_progress_to_output()
		else:
			event_loop = asyncio.get_event_loop()
			try:
				while len(self.urls_to_crawl) != 0:
					executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.num_workers)
					event_loop.run_until_complete(self.crawl_all_pending_urls(executor))
					if self.resume:
						self.write_progress_to_output()
			finally:
				event_loop.close()

		logging.info("Crawling has reached end of all found links")

		if self.resume:
			self.dedupe_url_strings_to_output()

		if self.sort_alphabetically:
			self.url_strings_to_output.sort()

		self.write_sitemap_output()



	async def crawl_all_pending_urls(self, executor):
		event_loop = asyncio.get_event_loop()

		crawl_tasks = []
		# Since the tasks created by `run_in_executor` begin executing immediately,
		# `self.urls_to_crawl` will start to get updated, potentially before the below
		# for loop finishes.  This creates a race condition and if `self.urls_to_crawl`
		# is updated (by `self.__crawl`) before the for loop finishes, it'll raise an
		# error
		urls_to_crawl = copy(self.urls_to_crawl)
		self.urls_to_crawl.clear()
		for url in urls_to_crawl:
			self.crawled_or_crawling.add(url)
			self.in_flight.add(url)
			task = event_loop.run_in_executor(executor, self.__crawl, url)
			task.add_done_callback(lambda t, u=url: self.in_flight.discard(u))
			crawl_tasks.append(task)

		logging.debug('waiting on all crawl tasks to complete')
		await asyncio.wait(crawl_tasks)
		logging.debug('all crawl tasks have completed nicely')
		return



	def __crawl(self, current_url):
		url = urlparse(current_url)
		logging.info(f"Crawling #{self.num_crawled}: {url.geturl()}")
		self.num_crawled += 1

		request = Request(current_url, headers={"User-Agent": config.crawler_user_agent})

		if self.auth:
			base64string = base64.b64encode(bytes(f'{config.username}:{config.password}', 'ascii'))
			request.add_header("Authorization", "Basic %s" % base64string.decode('utf-8'))

		# Ignore resources listed in the not_parseable_resources
		# Its avoid dowloading file like pdf… etc
		if not url.path.endswith(self.not_parseable_resources):
			try:
				response = urlopen(request)
			except Exception as e:
				if hasattr(e,'code'):
					self.response_code[e.code] += 1

					# Gestion des urls marked pour le reporting
					if self.report:
						self.marked[e.code].append(current_url)

				logging.debug (f"{e} ==> {current_url}")
				return
		else:
			logging.debug(f"Ignore {current_url} content might be not parseable.")
			response = None

		# Read the response
		if response is not None:
			try:
				msg = response.read()
				self.response_code[response.getcode()] += 1

				response.close()

				# Get the last modify date
				if 'last-modified' in response.headers:
					date = response.headers['Last-Modified']
				else:
					date = response.headers['Date']

				date = datetime.strptime(date, '%a, %d %b %Y %H:%M:%S %Z')

			except Exception as e:
				logging.debug (f"{e} ===> {current_url}")
				return
		else:
			# Response is None, content not downloaded, just continu and add
			# the link to the sitemap
			msg = "".encode( )
			date = None

		# Meta robots directives (noindex/nofollow) found on the page, if enabled
		meta_robots_directives = set()
		if self.respect_noindex:
			meta_robots_directives = self.get_meta_robots_directives(msg)

		# Image sitemap enabled ?
		image_list = ""
		if self.images:
			# Search for <figure> blocks so a <figcaption> can be matched back
			# to the <img> it describes, keyed by the image's raw src attribute.
			image_captions = {}
			for figure_content in self.figureregex.findall(msg):
				figcaption_match = self.figcaptionregex.search(figure_content)
				img_match = self.imageregex.search(figure_content)
				if not figcaption_match or not img_match:
					continue
				src_match = self.imagesrcregex.search(img_match.group(1))
				if not src_match:
					continue
				caption = self.tagstripregex.sub(b'', figcaption_match.group(1))
				caption = caption.decode("utf-8", errors="ignore").strip()
				if caption:
					image_captions[src_match.group(1)] = caption

			# Search for images in the current page.
			images = self.imageregex.findall(msg)
			for image_attrs in list(set(images)):
				src_match = self.imagesrcregex.search(image_attrs)
				if not src_match:
					continue

				raw_src = src_match.group(1)
				image_link = self.resolve_resource_url(raw_src, url)
				if image_link is None:
					continue

				# Ignore image if path is in the exclude_url list
				if not self.exclude_url(image_link):
					continue

				# Ignore other domain images
				image_link_parsed = urlparse(image_link)
				if image_link_parsed.netloc != self.target_domain:
					continue

				# Take the title from the title attribute, falling back to alt
				title_match = self.imagetitleregex.search(image_attrs)
				alt_match = self.imagealtregex.search(image_attrs)
				title = ""
				if title_match and title_match.group(1).strip():
					title = title_match.group(1).decode("utf-8", errors="ignore").strip()
				elif alt_match and alt_match.group(1).strip():
					title = alt_match.group(1).decode("utf-8", errors="ignore").strip()

				caption = image_captions.get(raw_src, "")

				# Test if images as been already seen and not present in the
				# robot file
				if self.can_fetch(image_link):
					logging.debug(f"Found image : {image_link}")
					image_title = f"<image:title>{self.htmlspecialchars(title)}</image:title>" if title else ""
					image_caption = f"<image:caption>{self.htmlspecialchars(caption)}</image:caption>" if caption else ""
					image_list = f"{image_list}<image:image><image:loc>{self.htmlspecialchars(image_link)}</image:loc>{image_title}{image_caption}</image:image>"

		# Video sitemap enabled ?
		video_list = ""
		if self.videos:
			# Search for <figure> blocks so a <figcaption> can be matched back
			# to the <video> it describes, keyed by the video's raw src attribute.
			video_captions = {}
			for figure_content in self.figureregex.findall(msg):
				figcaption_match = self.figcaptionregex.search(figure_content)
				video_match = self.videoregex.search(figure_content)
				if not figcaption_match or not video_match:
					continue
				raw_src = self.get_video_src(video_match.group(1), video_match.group(2))
				if not raw_src:
					continue
				caption = self.tagstripregex.sub(b'', figcaption_match.group(1))
				caption = caption.decode("utf-8", errors="ignore").strip()
				if caption:
					video_captions[raw_src] = caption

			# Search for videos in the current page.
			videos = self.videoregex.findall(msg)
			for video_attrs, video_content in list(set(videos)):
				raw_src = self.get_video_src(video_attrs, video_content)
				if not raw_src:
					continue

				video_link = self.resolve_resource_url(raw_src, url)
				if video_link is None:
					continue

				# Ignore video if path is in the exclude_url list
				if not self.exclude_url(video_link):
					continue

				# Ignore other domain videos
				video_link_parsed = urlparse(video_link)
				if video_link_parsed.netloc != self.target_domain:
					continue

				# Thumbnail comes from the poster attribute, if present
				thumbnail_link = ""
				poster_match = self.videoposterregex.search(video_attrs)
				if poster_match and poster_match.group(1).strip():
					resolved_thumbnail = self.resolve_resource_url(poster_match.group(1), url)
					if resolved_thumbnail is not None:
						thumbnail_link = resolved_thumbnail

				title_match = self.imagetitleregex.search(video_attrs)
				title = ""
				if title_match and title_match.group(1).strip():
					title = title_match.group(1).decode("utf-8", errors="ignore").strip()

				description = video_captions.get(raw_src, "")

				# Test if videos as been already seen and not present in the
				# robot file
				if self.can_fetch(video_link):
					logging.debug(f"Found video : {video_link}")
					video_thumbnail = f"<video:thumbnail_loc>{self.htmlspecialchars(thumbnail_link)}</video:thumbnail_loc>" if thumbnail_link else ""
					video_title = f"<video:title>{self.htmlspecialchars(title)}</video:title>" if title else ""
					video_description = f"<video:description>{self.htmlspecialchars(description)}</video:description>" if description else ""
					video_list = f"{video_list}<video:video><video:content_loc>{self.htmlspecialchars(video_link)}</video:content_loc>{video_thumbnail}{video_title}{video_description}</video:video>"

		# Note: that if there was a redirect, `final_url` may be different than
		#       `current_url`, and avoid not parseable content
		final_url = response.geturl() if response is not None else current_url

		# Hreflang sitemap enabled ?
		hreflang_list = ""
		if self.hreflang:
			for hreflang, alternate_link in self.get_hreflang_links(msg, final_url):
				hreflang_list = f'{hreflang_list}<xhtml:link rel="alternate" hreflang="{self.htmlspecialchars(hreflang)}" href="{self.htmlspecialchars(alternate_link)}"/>'

		# A canonical tag pointing to a different URL means this page is not the
		# canonical version, so it shouldn't be indexed under its own URL
		canonical_url = self.get_canonical_url(msg, final_url) if self.respect_canonical else None
		is_canonicalized_elsewhere = canonical_url is not None and self.clean_link(canonical_url) != self.clean_link(final_url)

		# Only add to sitemap URLs with same domain as the site being indexed
		if url.netloc == self.target_domain:
			if "noindex" in meta_robots_directives:
				logging.debug(f"Skipping {current_url} from sitemap, noindex meta tag found.")
			elif is_canonicalized_elsewhere:
				logging.debug(f"Skipping {current_url} from sitemap, canonical tag points to {canonical_url}.")
			else:
				# Last mod fetched ?
				lastmod = ""
				if date:
					lastmod = "<lastmod>"+date.strftime('%Y-%m-%dT%H:%M:%S+00:00')+"</lastmod>"
				url_string = "<url><loc>"+self.htmlspecialchars(final_url)+"</loc>" + lastmod + image_list + video_list + hreflang_list + "</url>"
				self.url_strings_to_output.append(url_string)
		# If the URL has a different domain than the site being indexed, this was reached through an iframe
  		# In this case: if the base tag matches the site being indexed, then all relative URLs should be crawled.
		else:
			base_urls = self.baseregex.findall(msg)

			if len(base_urls) == 0:
				return
			
			base_url = base_urls[0].decode("utf-8", errors="ignore")
			parsed_base_url = urlparse(base_url)
			if parsed_base_url.netloc != self.target_domain:
				return
			
			current_url = base_url
			url = parsed_base_url
		
		# A nofollow meta robots directive means links found on this page should
		# not be followed, but doesn't affect pages reached through other links
		if "nofollow" in meta_robots_directives:
			logging.debug(f"Not following links on {current_url}, nofollow meta tag found.")
			return

		# Found links
		self.find_links(msg, url, current_url)

		if self.fetch_iframes:
			self.find_links(msg, url, current_url, iframes=True)


	def get_meta_robots_directives(self, msg):
		# Collects the comma-separated values of <meta name="robots" content="..."> tags,
		# e.g. {"noindex", "nofollow"} for <meta name="robots" content="noindex, nofollow">
		directives = set()
		for meta_tag in self.metaregex.findall(msg):
			name_match = self.metanameregex.search(meta_tag)
			if not name_match or name_match.group(1).decode("utf-8", errors="ignore").strip().lower() != "robots":
				continue
			content_match = self.metacontentregex.search(meta_tag)
			if not content_match:
				continue
			content = content_match.group(1).decode("utf-8", errors="ignore").lower()
			directives.update(directive.strip() for directive in content.split(","))
		return directives

	def get_canonical_url(self, msg, base_url):
		# Returns the absolute URL from <link rel="canonical" href="..."> on the
		# page, if any, resolved against base_url in case href is relative.
		for link_tag in self.linktagregex.findall(msg):
			rel_match = self.linkrelregex.search(link_tag)
			if not rel_match or rel_match.group(1).decode("utf-8", errors="ignore").strip().lower() != "canonical":
				continue
			href_match = self.linkhrefregex.search(link_tag)
			if not href_match:
				continue
			href = href_match.group(1).decode("utf-8", errors="ignore").strip()
			if not href:
				continue
			return urljoin(base_url, href)
		return None

	def get_hreflang_links(self, msg, base_url):
		# Returns a list of (hreflang, absolute_url) tuples read from
		# <link rel="alternate" hreflang="..." href="..."> tags on the page,
		# with href resolved against base_url in case it's relative.
		hreflang_links = []
		for link_tag in self.linktagregex.findall(msg):
			rel_match = self.linkrelregex.search(link_tag)
			if not rel_match or rel_match.group(1).decode("utf-8", errors="ignore").strip().lower() != "alternate":
				continue
			hreflang_match = self.linkhreflangregex.search(link_tag)
			if not hreflang_match:
				continue
			hreflang = hreflang_match.group(1).decode("utf-8", errors="ignore").strip()
			if not hreflang:
				continue
			href_match = self.linkhrefregex.search(link_tag)
			if not href_match:
				continue
			href = href_match.group(1).decode("utf-8", errors="ignore").strip()
			if not href:
				continue
			hreflang_links.append((hreflang, urljoin(base_url, href)))
		return hreflang_links

	def find_links(self, msg, url, current_url, iframes: bool = False):
		if iframes:
			links = self.iframeregex.findall(msg)
		else:
			links = self.linkregex.findall(msg)

		for link in links:
			link = link.decode("utf-8", errors="ignore")
			logging.debug(f"Found : {link}")

			if link.startswith('/'):
				link = url.scheme + '://' + url[1] + link
			elif link.startswith('#'):
				link = url.scheme + '://' + url[1] + url[2] + link
			elif link.startswith(("mailto", "tel")):
				continue
			elif not link.startswith(('http', "https")):
				link = self.clean_link(urljoin(current_url, link))

			# Remove the anchor part if needed
			if "#" in link:
				link = link[:link.index('#')]

			# Drop attributes if needed
			for to_drop in self.drop:
				link = re.sub(to_drop, '', link)

			# Parse the url to get domain and file extension
			parsed_link = urlparse(link)
			domain_link = parsed_link.netloc
			target_extension = os.path.splitext(parsed_link.path)[1][1:]

			if link in self.crawled_or_crawling:
				continue
			if link in self.urls_to_crawl:
				continue
			if link in self.excluded:
				continue
			if domain_link != self.target_domain and not iframes:
				continue
			if parsed_link.path in ["", "/"] and parsed_link.query == '':
				continue
			if "javascript" in link:
				continue
			if self.is_image(parsed_link.path):
				continue
			if parsed_link.path.startswith("data:"):
				continue

			# Count one more URL
			self.nb_url+=1

			# Check if the navigation is allowed by the robots.txt
			if not self.can_fetch(link):
				self.exclude_link(link)
				self.nb_rp+=1
				continue

			# Check if the current file extension is allowed or not.
			if target_extension in self.skipext:
				self.exclude_link(link)
				self.nb_exclude+=1
				continue

			# Check if the current url doesn't contain an excluded word
			if not self.exclude_url(link):
				self.exclude_link(link)
				self.nb_exclude+=1
				continue

			self.urls_to_crawl.add(link)

	def _handle_interrupt(self, signum, frame):
		logging.error("Interrupted, saving progress to the output file before exit...")
		self.write_progress_to_output()
		sys.exit(1)

	def write_progress_to_output(self):
		# Snapshot of everything crawled so far, written straight to --output
		# so it doubles as the resume source next time --resume is used. No
		# separate checkpoint file to keep in sync or clean up.
		#
		# The plain <url> entries alone aren't enough to resume correctly:
		# they only record pages that finished crawling, not the pending
		# frontier (urls_to_crawl/in_flight) still waiting to be fetched.
		# Without that frontier, a resumed crawl would only find brand new
		# links reachable from pages it hasn't visited yet, since every link
		# to an already-crawled page is (rightly) skipped as a duplicate --
		# so anything only reachable through a page that was mid-fetch (or
		# still queued) at save time would silently never get crawled. So
		# the frontier is stashed as an XML comment, which real sitemap
		# consumers ignore, right before the closing tag.
		if not self.output:
			return
		pending_urls = self.urls_to_crawl | self.in_flight
		try:
			with open(self.output, 'w') as output_file:
				print(config.xml_header, file=output_file)
				for url_string in self.url_strings_to_output:
					print(url_string, file=output_file)
				if pending_urls:
					print("<!--PENDING-URLS:" + "\n".join(pending_urls) + "-->", file=output_file)
				print(config.xml_footer, file=output_file)
			logging.debug(f"Saved progress to {self.output}")
		except Exception as e:
			logging.error(f"Could not save progress to {self.output}: {e}")

	def load_progress_from_output(self):
		if not os.path.exists(self.output):
			return
		try:
			with open(self.output, 'r') as output_file:
				content = output_file.read()
		except Exception as e:
			logging.error(f"Could not read {self.output} to resume, starting fresh: {e}")
			return

		url_blocks = re.findall('<url>.*?</url>', content, re.DOTALL)
		for url_block in url_blocks:
			loc_match = re.search('<loc>(.*?)</loc>', url_block)
			if not loc_match:
				continue
			self.url_strings_to_output.append(url_block)
			self.crawled_or_crawling.add(self.html_unescape(loc_match.group(1)))

		pending_match = re.search(r'<!--PENDING-URLS:(.*?)-->', content, re.DOTALL)
		if pending_match:
			pending_urls = {url for url in pending_match.group(1).split("\n") if url}
			self.urls_to_crawl |= pending_urls
			self.crawled_or_crawling -= pending_urls

		if not url_blocks and not pending_match:
			return

		logging.info(
			f"Resumed {len(self.url_strings_to_output)} previously crawled url(s) "
			f"and {len(self.urls_to_crawl)} pending url(s) from {self.output}"
		)

	def dedupe_url_strings_to_output(self):
		# Resuming re-crawls urls that were in_flight when progress was last
		# saved (their own outgoing links may not have been discovered yet),
		# which can add a second <url> entry for a page already written to
		# url_strings_to_output. Keep the most recent entry for each loc.
		locs_seen = {}
		order = []
		for url_string in self.url_strings_to_output:
			loc_match = re.search('<loc>(.*?)</loc>', url_string)
			key = loc_match.group(1) if loc_match else url_string
			if key not in locs_seen:
				order.append(key)
			locs_seen[key] = url_string
		self.url_strings_to_output = [locs_seen[key] for key in order]

	def write_sitemap_output(self):
		are_multiple_sitemap_files_required = \
			len(self.url_strings_to_output) > self.MAX_URLS_PER_SITEMAP

		# When there are more than 50,000 URLs, the sitemap specification says we have
		# to split the sitemap into multiple files using an index file that points to the
		# location of each sitemap file.  For now, we require the caller to explicitly
		# specify they want to create an index, even if there are more than 50,000 URLs,
		# to maintain backward compatibility.
		#
		# See specification here:
		# https://support.google.com/webmasters/answer/183668?hl=en
		if are_multiple_sitemap_files_required and self.as_index:
			self.write_index_and_sitemap_files()
		else:
			self.write_single_sitemap()

	def write_single_sitemap(self):
		with open(self.output, 'w') as output_file:
			self.write_sitemap_file(output_file, self.url_strings_to_output)

	def write_index_and_sitemap_files(self):
		sitemap_index_filename, sitemap_index_extension = os.path.splitext(self.output)

		num_sitemap_files = math.ceil(len(self.url_strings_to_output) / self.MAX_URLS_PER_SITEMAP)
		sitemap_filenames = []
		for i in range(0, num_sitemap_files):
			# name the individual sitemap files based on the name of the index file
			sitemap_filename = sitemap_index_filename + '-' + str(i) + sitemap_index_extension
			sitemap_filenames.append(sitemap_filename)

		self.write_sitemap_index(sitemap_filenames)

		for i, sitemap_filename in enumerate(sitemap_filenames):
			self.write_subset_of_urls_to_sitemap(sitemap_filename, i * self.MAX_URLS_PER_SITEMAP)

	def write_sitemap_index(self, sitemap_filenames):
		with open(self.output, 'w') as sitemap_index_file:
			print(config.sitemapindex_header, file=sitemap_index_file)
			for sitemap_filename in sitemap_filenames:
				sitemap_url = urlunsplit([self.scheme, self.target_domain, sitemap_filename, '', ''])
				print("<sitemap><loc>" + sitemap_url + "</loc>""</sitemap>", file=sitemap_index_file)
			print(config.sitemapindex_footer, file=sitemap_index_file)

	def write_subset_of_urls_to_sitemap(self, filename, index):
		# Writes a maximum of self.MAX_URLS_PER_SITEMAP urls to a sitemap file
		#
		# filename: name of the file to write the sitemap to
		# index:    zero-based index from which to start writing url strings contained in
		#           self.url_strings_to_output
		try:
			with open(filename, 'w') as sitemap_file:
				start_index = index
				end_index = (index + self.MAX_URLS_PER_SITEMAP)
				sitemap_url_strings = self.url_strings_to_output[start_index:end_index]
				self.write_sitemap_file(sitemap_file, sitemap_url_strings)
		except:
			logging.error("Could not open sitemap file that is part of index.")
			exit(255)

	@staticmethod
	def write_sitemap_file(file, url_strings):
		print(config.xml_header, file=file)

		for url_string in url_strings:
			print (url_string, file=file)

		print (config.xml_footer, file=file)

	def clean_link(self, link):
		parts = list(urlsplit(link))
		parts[2] = self.resolve_url_path(parts[2])
		return urlunsplit(parts)

	def resolve_url_path(self, path):
		# From https://stackoverflow.com/questions/4317242/python-how-to-resolve-urls-containing/40536115#40536115
		segments = path.split('/')
		segments = [segment + '/' for segment in segments[:-1]] + [segments[-1]]
		resolved = []
		for segment in segments:
			if segment in ('../', '..'):
				if resolved[1:]:
					resolved.pop()
			elif segment not in ('./', '.'):
				resolved.append(segment)
		return ''.join(resolved)

	@staticmethod
	def is_image(path):
		mt, me = mimetypes.guess_type(path)
		return mt is not None and mt.startswith("image/")

	def resolve_resource_url(self, raw_link, url):
		# Resolves a raw src/poster attribute value (bytes) found on the page
		# into an absolute URL string, or None if it's a data: URI.
		link = raw_link.decode("utf-8", errors="ignore")

		if link.startswith("data:"):
			return None

		# If path start with // get the current url scheme
		if link.startswith("//"):
			return url.scheme + ":" + link
		# Append domain if not present
		elif not link.startswith(("http", "https")):
			if not link.startswith("/"):
				link = f"/{link}"
			return f'{self.domain.strip("/")}{link.replace("./", "/")}'

		return link

	def get_video_src(self, video_attrs, video_content):
		# A <video> tag's source can either be a src attribute directly on the
		# tag, or a src attribute on a nested <source> tag.
		src_match = self.imagesrcregex.search(video_attrs)
		if src_match:
			return src_match.group(1)

		source_match = self.videosourceregex.search(video_content)
		if source_match:
			src_match = self.imagesrcregex.search(source_match.group(1))
			if src_match:
				return src_match.group(1)

		return None

	def exclude_link(self,link):
		if link not in self.excluded:
			self.excluded.add(link)

	def check_robots(self):
		robots_url = urljoin(self.domain, "robots.txt")
		self.rp = RobotFileParser()
		self.rp.set_url(robots_url)

		# Fetch robots.txt with the same User-Agent used for crawling.
		# RobotFileParser.read() uses urlopen() without a custom header, so
		# it sends the default "Python-urllib/x.y" User-Agent, which some
		# sites block (403) as a generic bot signature. That causes
		# RobotFileParser to set disallow_all, blocking every discovered
		# link even though the site's robots.txt actually allows crawling.
		try:
			request = Request(robots_url, headers={"User-Agent": config.crawler_user_agent})
			response = urlopen(request)
			raw = response.read()
			self.rp.parse(raw.decode("utf-8").splitlines())
		except HTTPError as err:
			if err.code in (401, 403):
				self.rp.disallow_all = True
			elif 400 <= err.code < 500:
				self.rp.allow_all = True
		except Exception as e:
			logging.debug(f"Could not fetch robots.txt: {e}")

	def can_fetch(self, link):
		try:
			if self.parserobots:
				if self.rp.can_fetch(self.user_agent, link):
					return True
				else:
					logging.debug ("Crawling of {0} disabled by robots.txt".format(link))
					return False

			if not self.parserobots:
				return True

			return True
		except:
			# On error continue!
			logging.debug ("Error during parsing robots.txt")
			return True

	def exclude_url(self, link):
		for ex in self.exclude:
			if ex in link:
				return False
		return True

	@staticmethod
	def htmlspecialchars(text):
		return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

	@staticmethod
	def html_unescape(text):
		return text.replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"').replace("&amp;", "&")

	def make_report(self):
		print ("Number of found URL : {0}".format(self.nb_url))
		print ("Number of links crawled : {0}".format(self.num_crawled))
		if self.parserobots:
			print ("Number of link block by robots.txt : {0}".format(self.nb_rp))
		if self.skipext or self.exclude:
			print ("Number of link exclude : {0}".format(self.nb_exclude))

		for code in self.response_code:
			print ("Nb Code HTTP {0} : {1}".format(code, self.response_code[code]))

		for code in self.marked:
			print ("Link with status {0}:".format(code))
			for uri in self.marked[code]:
				print ("\t- {0}".format(uri))
