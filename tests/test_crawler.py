import os
import sys
import tempfile
import unittest
from collections import defaultdict
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import Crawler, _RenderedResponse


class FakeHTTPResponse:
    """Stands in for the object urlopen() normally returns."""

    def __init__(self, body: bytes, status=200, headers=None, url=None):
        self.body = body
        self.status = status
        self.headers = headers if headers is not None else {}
        self.url = url

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def close(self):
        pass


def make_crawler(domain="http://example.com", **kwargs):
    crawler = Crawler(domain=domain, **kwargs)
    # Crawled_or_crawling/excluded/marked/response_code are class-level mutable
    # defaults that Crawler.__init__ never re-assigns per instance, so without
    # this every test would share (and pollute) the same containers.
    crawler.crawled_or_crawling = set()
    crawler.excluded = set()
    crawler.marked = defaultdict(list)
    crawler.response_code = defaultdict(int)
    # __init__ seeds urls_to_crawl with the domain itself; tests want a clean slate.
    crawler.urls_to_crawl = set()
    return crawler


def crawl(crawler, url):
    # __crawl is name-mangled since it's a dunder-prefixed method.
    return crawler._Crawler__crawl(url)


class FindLinksTests(unittest.TestCase):
    def setUp(self):
        self.crawler = make_crawler()

    def test_basic_relative_and_absolute_links(self):
        html = b'<a href="/page1">one</a><a href="http://example.com/page2">two</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertIn("http://example.com/page1", self.crawler.urls_to_crawl)
        self.assertIn("http://example.com/page2", self.crawler.urls_to_crawl)

    def test_mailto_tel_and_javascript_skipped(self):
        html = (
            b'<a href="mailto:test@example.com">mail</a>'
            b'<a href="tel:+123456">tel</a>'
            b'<a href="javascript:void(0)">js</a>'
        )
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertEqual(self.crawler.urls_to_crawl, set())

    def test_other_domain_link_skipped(self):
        html = b'<a href="http://other.com/page">other</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertEqual(self.crawler.urls_to_crawl, set())

    def test_bare_domain_root_link_skipped(self):
        # A link that resolves to just the domain root (no path/query) is
        # never queued -- it's assumed to already be the crawl's start point.
        html = b'<a href="http://example.com/">home</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertEqual(self.crawler.urls_to_crawl, set())

    def test_already_crawled_link_not_requeued(self):
        self.crawler.crawled_or_crawling.add("http://example.com/page1")
        html = b'<a href="/page1">one</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertEqual(self.crawler.urls_to_crawl, set())

    def test_exclude_flag_filters_link(self):
        self.crawler.exclude = ["action=edit"]
        html = b'<a href="/page?action=edit">edit</a><a href="/page?action=view">view</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertNotIn("http://example.com/page?action=edit", self.crawler.urls_to_crawl)
        self.assertIn("http://example.com/page?action=view", self.crawler.urls_to_crawl)
        self.assertIn("http://example.com/page?action=edit", self.crawler.excluded)

    def test_skipext_flag_filters_link(self):
        self.crawler.skipext = ["pdf"]
        html = b'<a href="/doc.pdf">doc</a><a href="/page.html">page</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertNotIn("http://example.com/doc.pdf", self.crawler.urls_to_crawl)
        self.assertIn("http://example.com/page.html", self.crawler.urls_to_crawl)

    def test_drop_flag_strips_pattern_from_link(self):
        self.crawler.drop = [r"\?sessionid=[0-9]+"]
        html = b'<a href="/page?sessionid=12345">page</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertIn("http://example.com/page", self.crawler.urls_to_crawl)

    def test_anchor_fragment_removed(self):
        html = b'<a href="/page#section">page</a>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/")
        self.assertIn("http://example.com/page", self.crawler.urls_to_crawl)

    def test_iframe_links_allowed_cross_domain(self):
        html = b'<iframe src="http://other.com/embed"></iframe>'
        self.crawler.find_links(html, urlparse("http://example.com/"), "http://example.com/", iframes=True)
        self.assertIn("http://other.com/embed", self.crawler.urls_to_crawl)


class CleanLinkTests(unittest.TestCase):
    def setUp(self):
        self.crawler = make_crawler()

    def test_resolves_dot_dot_segments(self):
        self.assertEqual(
            self.crawler.clean_link("http://example.com/a/b/../c"),
            "http://example.com/a/c",
        )

    def test_resolves_dot_segments(self):
        self.assertEqual(
            self.crawler.clean_link("http://example.com/a/./b"),
            "http://example.com/a/b",
        )

    def test_leaves_plain_path_untouched(self):
        self.assertEqual(
            self.crawler.clean_link("http://example.com/a/b"),
            "http://example.com/a/b",
        )


class MetaRobotsTests(unittest.TestCase):
    def setUp(self):
        self.crawler = make_crawler()

    def test_noindex_directive_detected(self):
        html = b'<meta name="robots" content="noindex, nofollow">'
        directives = self.crawler.get_meta_robots_directives(html)
        self.assertEqual(directives, {"noindex", "nofollow"})

    def test_no_robots_meta_tag_returns_empty_set(self):
        html = b'<meta charset="utf-8">'
        self.assertEqual(self.crawler.get_meta_robots_directives(html), set())


class CanonicalAndHreflangTests(unittest.TestCase):
    def setUp(self):
        self.crawler = make_crawler()

    def test_canonical_url_resolved_absolute(self):
        html = b'<link rel="canonical" href="/canonical-page">'
        self.assertEqual(
            self.crawler.get_canonical_url(html, "http://example.com/page"),
            "http://example.com/canonical-page",
        )

    def test_no_canonical_tag_returns_none(self):
        html = b'<link rel="stylesheet" href="/style.css">'
        self.assertIsNone(self.crawler.get_canonical_url(html, "http://example.com/page"))

    def test_hreflang_links_parsed(self):
        html = (
            b'<link rel="alternate" hreflang="fr" href="/fr/page">'
            b'<link rel="alternate" hreflang="en" href="/en/page">'
        )
        links = dict(self.crawler.get_hreflang_links(html, "http://example.com/page"))
        self.assertEqual(links["fr"], "http://example.com/fr/page")
        self.assertEqual(links["en"], "http://example.com/en/page")


class CrawlBehaviorTests(unittest.TestCase):
    """Exercises __crawl() end-to-end with urlopen mocked out."""

    def test_network_error_is_counted_and_logged_not_silently_dropped(self):
        crawler = make_crawler()
        with mock.patch("crawler.urlopen", side_effect=URLError("SSL: CERTIFICATE_VERIFY_FAILED")):
            with mock.patch("logging.error") as mock_log_error:
                crawl(crawler, "http://example.com/")
                self.assertTrue(mock_log_error.called)
        self.assertEqual(sum(crawler.network_errors.values()), 1)
        self.assertEqual(crawler.url_strings_to_output, [])

    def test_missing_date_headers_does_not_drop_the_page(self):
        # Regression test: a response with neither Last-Modified nor Date
        # headers used to raise KeyError and silently drop the whole page.
        crawler = make_crawler()
        response = FakeHTTPResponse(b"<html></html>", headers={}, url="http://example.com/")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        self.assertEqual(len(crawler.url_strings_to_output), 1)
        self.assertIn("<loc>http://example.com/</loc>", crawler.url_strings_to_output[0])
        self.assertNotIn("<lastmod>", crawler.url_strings_to_output[0])

    def test_valid_last_modified_header_produces_lastmod(self):
        crawler = make_crawler()
        response = FakeHTTPResponse(
            b"<html></html>",
            headers={"last-modified": "Wed, 01 Jan 2020 00:00:00 GMT"},
            url="http://example.com/",
        )
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        self.assertIn("<lastmod>2020-01-01T00:00:00+00:00</lastmod>", crawler.url_strings_to_output[0])

    def test_noindex_excludes_page_but_still_follows_its_links(self):
        crawler = make_crawler()
        html = b'<meta name="robots" content="noindex"><a href="/other">other</a>'
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        self.assertEqual(crawler.url_strings_to_output, [])
        self.assertIn("http://example.com/other", crawler.urls_to_crawl)

    def test_nofollow_keeps_page_but_stops_following_links(self):
        crawler = make_crawler()
        html = b'<meta name="robots" content="nofollow"><a href="/other">other</a>'
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        self.assertEqual(len(crawler.url_strings_to_output), 1)
        self.assertNotIn("http://example.com/other", crawler.urls_to_crawl)

    def test_canonical_elsewhere_excludes_page_from_sitemap(self):
        crawler = make_crawler()
        html = b'<link rel="canonical" href="/real-page">'
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/duplicate")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/duplicate")
        self.assertEqual(crawler.url_strings_to_output, [])

    def test_no_respect_canonical_includes_page_anyway(self):
        crawler = make_crawler(respect_canonical=False)
        html = b'<link rel="canonical" href="/real-page">'
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/duplicate")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/duplicate")
        self.assertEqual(len(crawler.url_strings_to_output), 1)

    def test_image_sitemap_with_figcaption(self):
        crawler = make_crawler(images=True)
        html = (
            b'<figure><img src="/img/pic.png" alt="a pic"><figcaption>Caption</figcaption></figure>'
        )
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        output = crawler.url_strings_to_output[0]
        self.assertIn("<image:loc>http://example.com/img/pic.png</image:loc>", output)
        self.assertIn("<image:title>a pic</image:title>", output)
        self.assertIn("<image:caption>Caption</image:caption>", output)

    def test_video_sitemap_with_poster(self):
        crawler = make_crawler(videos=True)
        html = b'<video poster="/poster.jpg"><source src="/movie.mp4"></video>'
        response = FakeHTTPResponse(html, headers={}, url="http://example.com/")
        with mock.patch("crawler.urlopen", return_value=response):
            crawl(crawler, "http://example.com/")
        output = crawler.url_strings_to_output[0]
        self.assertIn("<video:content_loc>http://example.com/movie.mp4</video:content_loc>", output)
        self.assertIn("<video:thumbnail_loc>http://example.com/poster.jpg</video:thumbnail_loc>", output)


class RobotsAndExcludeTests(unittest.TestCase):
    def test_exclude_url_matches_substring(self):
        crawler = make_crawler(exclude=["/private"])
        self.assertFalse(crawler.exclude_url("http://example.com/private/page"))
        self.assertTrue(crawler.exclude_url("http://example.com/public/page"))

    def test_can_fetch_defaults_to_true_when_parserobots_disabled(self):
        crawler = make_crawler(parserobots=False)
        self.assertTrue(crawler.can_fetch("http://example.com/anything"))

    def test_can_fetch_respects_robots_txt_when_enabled(self):
        crawler = make_crawler(parserobots=True)
        crawler.rp = mock.Mock()
        crawler.rp.can_fetch.return_value = False
        self.assertFalse(crawler.can_fetch("http://example.com/disallowed"))


class RenderJsTests(unittest.TestCase):
    def test_render_js_forces_single_worker(self):
        crawler = make_crawler(render_js=True, num_workers=4)
        self.assertEqual(crawler.num_workers, 1)

    def test_start_browser_without_playwright_installed_exits(self):
        crawler = make_crawler(render_js=True)
        with mock.patch.dict(sys.modules, {"playwright.sync_api": None}):
            with self.assertRaises(SystemExit):
                crawler.start_browser()

    def test_fetch_rendered_returns_urllib_compatible_shim(self):
        crawler = make_crawler(render_js=True)
        fake_page = mock.Mock()
        fake_page.content.return_value = "<html>rendered</html>"
        fake_page.url = "http://example.com/final"
        fake_nav_response = mock.Mock(status=200, headers={"content-type": "text/html"})
        fake_page.goto.return_value = fake_nav_response
        crawler.browser = mock.Mock()
        crawler.browser.new_page.return_value = fake_page

        response = crawler._fetch_rendered("http://example.com/")

        self.assertIsInstance(response, _RenderedResponse)
        self.assertEqual(response.read(), b"<html>rendered</html>")
        self.assertEqual(response.getcode(), 200)
        self.assertEqual(response.geturl(), "http://example.com/final")
        self.assertIn("content-type", response.headers)
        fake_page.close.assert_called_once()


class SitemapOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
        self.tmpfile.close()
        self.addCleanup(os.remove, self.tmpfile.name)

    def test_write_single_sitemap(self):
        crawler = make_crawler(output=self.tmpfile.name)
        crawler.url_strings_to_output = ["<url><loc>http://example.com/a</loc></url>"]
        crawler.write_single_sitemap()
        with open(self.tmpfile.name) as f:
            content = f.read()
        self.assertIn("<urlset", content)
        self.assertIn("<url><loc>http://example.com/a</loc></url>", content)
        self.assertIn("</urlset>", content)

    def test_write_index_and_sitemap_files_splits_by_max_urls(self):
        crawler = make_crawler(output=self.tmpfile.name, as_index=True)
        crawler.MAX_URLS_PER_SITEMAP = 2
        crawler.url_strings_to_output = [
            "<url><loc>http://example.com/%d</loc></url>" % i for i in range(5)
        ]
        base, ext = os.path.splitext(self.tmpfile.name)
        expected_files = [f"{base}-{i}{ext}" for i in range(3)]
        try:
            crawler.write_index_and_sitemap_files()
            with open(self.tmpfile.name) as f:
                index_content = f.read()
            self.assertIn("<sitemapindex", index_content)
            for filename in expected_files:
                self.assertIn(filename, index_content)
                self.assertTrue(os.path.exists(filename))
            with open(expected_files[0]) as f:
                first_file_content = f.read()
            self.assertEqual(first_file_content.count("<url>"), 2)
            with open(expected_files[-1]) as f:
                last_file_content = f.read()
            self.assertEqual(last_file_content.count("<url>"), 1)
        finally:
            for filename in expected_files:
                if os.path.exists(filename):
                    os.remove(filename)

    def test_write_progress_and_resume_round_trip(self):
        crawler = make_crawler(output=self.tmpfile.name, resume=True)
        crawler.url_strings_to_output = ["<url><loc>http://example.com/done</loc></url>"]
        crawler.urls_to_crawl = {"http://example.com/pending"}
        crawler.in_flight = set()
        crawler.write_progress_to_output()

        resumed = make_crawler(output=self.tmpfile.name, resume=True)
        resumed.url_strings_to_output = []
        resumed.urls_to_crawl = set()
        resumed.load_progress_from_output()

        self.assertEqual(len(resumed.url_strings_to_output), 1)
        self.assertIn("http://example.com/done", resumed.crawled_or_crawling)
        self.assertIn("http://example.com/pending", resumed.urls_to_crawl)


if __name__ == "__main__":
    unittest.main()
