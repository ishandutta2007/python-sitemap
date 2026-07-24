# Python-Sitemap

Simple script to crawl websites and create a sitemap.xml of all public link in it.

Warning : This script only works with ***Python3***

## Simple usage

	>>> python main.py --domain http://blog.lesite.us --output sitemap.xml

## Advanced usage

Read a config file to set parameters:
***You can overide (or add for list) any parameters define in the config.json***

	>>> python main.py --config config/config.json

#### Enable debug:

  ```
	$ python main.py --domain https://blog.lesite.us --output sitemap.xml --debug
  ```

#### Enable verbose output:

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --verbose
  ```

#### Disable sorting output:

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --no-sort
  ```


#### Enable Image Sitemap

More informations here https://support.google.com/webmasters/answer/178636?hl=en

When enabled, each image's `title` (falling back to `alt` if there is no `title`) is
added as `<image:title>`, and if the image sits inside a `<figure>` with a `<figcaption>`,
that caption is added as `<image:caption>`.

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --images
  ```

#### Enable Video Sitemap

More informations here https://developers.google.com/search/docs/crawling-indexing/sitemaps/video-sitemaps

When enabled, each `<video>` tag's source (either its own `src` attribute or a
nested `<source>` tag's `src`) is added as `<video:content_loc>`. The video's
`poster` attribute (if any) is added as `<video:thumbnail_loc>`, its `title`
attribute as `<video:title>`, and if the video sits inside a `<figure>` with a
`<figcaption>`, that caption is added as `<video:description>`.

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --videos
  ```

#### Hreflang alternate links

More informations here https://developers.google.com/search/docs/specialty/international/localized-versions#sitemap

***By default, each `<link rel="alternate" hreflang="..." href="...">` tag found in a
page's `<head>` is added to that page's sitemap entry as an `<xhtml:link>` alternate,
so search engines can discover the page's localized versions. Pass `--no-hreflang` to
disable this:***

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --no-hreflang
  ```

#### Allow fetching content from Iframes

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --fetch-iframes
  ```

#### Enable report for print summary of the crawl:

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --report
  ```

#### Skip url (by extension) (skip pdf AND xml url):

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --skipext pdf --skipext xml
  ```

#### Drop a part of an url via regexp :

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --drop "id=[0-9]{5}"
  ```

#### Exclude url by filter a part of it :

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --exclude "action=edit"
  ```

#### Read the robots.txt to ignore some url:

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --parserobots
  ```

#### Use specific user-agent for robots.txt:

  ```
  $ python main.py --domain https://blog.lesite.us --output sitemap.xml --parserobots --user-agent Googlebot
  ```

#### Human readable XML

```
$ python3 main.py --domain https://blog.lesite.us --images --parserobots | xmllint --format -
```

#### Multithreaded

```
$ python3 main.py --domain https://blog.lesite.us --num-workers 4
```

#### with basic auth
***You need to configure `username` and `password` in your `config.py` before***
```
$ python3 main.py --domain https://blog.lesite.us --auth
```

#### Output sitemap index file
***Sitemaps with over 50,000 URLs should be split into an index file that points to sitemap files that each contain 50,000 URLs or fewer.  Outputting as an index requires specifying an output file.  An index will only be output if a crawl has more than 50,000 URLs:***
```
$ python3 main.py --domain https://blog.lesite.us --as-index --output sitemap.xml
```

#### Noindex / nofollow meta tags

***By default, pages containing `<meta name="robots" content="noindex">` are excluded from the output sitemap (a `nofollow` directive on a page stops links found on that page from being followed, but doesn't exclude the page itself). Pass `--no-respect-noindex` to include noindex pages in the sitemap anyway:***
```
$ python3 main.py --domain https://blog.lesite.us --output sitemap.xml --no-respect-noindex
```

#### Canonical tags

***By default, pages whose `<link rel="canonical" href="...">` tag points to a different URL are excluded from the output sitemap, since the canonical page is the one that should be indexed. Pass `--no-respect-canonical` to include canonicalized pages in the sitemap anyway:***
```
$ python3 main.py --domain https://blog.lesite.us --output sitemap.xml --no-respect-canonical
```

#### Resume an interrupted crawl
***Large crawls can take a long time and may get interrupted (Ctrl+C, a crash, a network drop). Pass `--resume` to periodically save crawl progress into `--output` itself, including on interrupt, instead of a separate file. Re-running the exact same command afterwards continues from that file instead of starting over:***
```
$ python3 main.py --domain https://blog.lesite.us --output sitemap.xml --resume
```

#### Client-side rendered pages (SPAs: React/Vue/Svelte…)

***By default the crawler only reads the raw HTML returned by the server, so links that are only added by client-side JavaScript are invisible to it. Pass `--render-js` to instead load each page in headless Chromium (via [Playwright](https://playwright.dev/python/)) and parse the fully-rendered HTML. This is slower and forces `--num-workers` to 1 (Playwright isn't safe to drive from multiple worker threads), so only enable it for sites that actually need it:***
```
$ python3 main.py --domain https://blog.lesite.us --output sitemap.xml --render-js
```
`--render-js` requires Playwright and a Chromium build, which are **not** part of the base install. Either:
- run via the `Dockerfile.playwright` image (see Docker usage below), which already has both, or
- install them locally: `pip install playwright && playwright install chromium`

## Docker usage

#### Build the Docker image:

  ```
  $ docker build -t python-sitemap:latest .
  ```

#### Run with default domain :

  ```
  $ docker run -it python-sitemap
  ```

#### Run with custom domain :

  ```
  $ docker run -it python-sitemap --domain https://www.graylog.fr
  ```

#### Run with config file and output :
***You need to configure config.json file before***

  ```
  $ docker run -it -v `pwd`/config/:/config/ -v `pwd`:/home/python-sitemap/ python-sitemap --config config/config.json
  ```

#### Run with `--render-js` (Playwright + Chromium preinstalled)

The default image doesn't include Playwright/Chromium to keep it small. Use `Dockerfile.playwright` instead, which is based on the official Playwright image and has both preinstalled:

  ```
  $ docker build -f Dockerfile.playwright -t python-sitemap-playwright:latest .
  $ docker run -it python-sitemap-playwright --domain https://www.graylog.fr --render-js
  ```
