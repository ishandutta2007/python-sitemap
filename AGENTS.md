# AGENTS.md

Guidance for working on python-sitemap.

## Architecture

Two files carry all the logic:

- `main.py` — `argparse` CLI. Parses flags (and optionally a `--config` JSON file, whose values are merged over the flags), builds a `crawler.Crawler(**dict_arg)` with the parsed keyword arguments, calls `crawl.run()`, then `crawl.make_report()` if `--report` was passed.
- `crawler.py` — the `Crawler` class, which does everything: fetching, parsing, filtering, and writing the sitemap.
- `config.py` — XML header/footer templates, the crawler's `User-Agent` string, and `--auth` basic-auth credentials. Not related to `--config`.

There is deliberately **no `requirements.txt`**. The default crawl path (`main.py` with no `--render-js`) only uses the Python standard library (`urllib`, `re`, `asyncio`, …) — keep it that way. The one opt-in exception is described below.

## The crawl pipeline (`Crawler.__crawl` / `Crawler._Crawler__crawl`)

For each URL popped off `self.urls_to_crawl`:

1. Fetch it — `urlopen(request)` normally, or `self._fetch_rendered(current_url)` when `--render-js` is set (see below). Anything ending in `not_parseable_resources` (images, archives, pdf, etc.) is skipped without fetching.
2. Read the body and status code. A missing/unparseable `Last-Modified`/`Date` header is **not** fatal — it only means no `<lastmod>` is emitted; the page is still processed. (This used to raise `KeyError` and silently drop the entire page — see the git history around the #57 fix if touching this again.)
3. Parse out of the raw bytes with the regexes defined at the top of the class — there is no DOM parser or BeautifulSoup, just precompiled `bytes` patterns (`linkregex`, `imageregex`, `videoregex`, `metaregex`/`linktagregex` for meta-robots/canonical/hreflang, etc.). Keep new extraction in the same byte-regex style for consistency.
4. Apply `<meta name="robots">` (`noindex`/`nofollow`) and `<link rel="canonical">` handling: `noindex`/canonicalized-elsewhere pages are excluded from the sitemap but their links are still followed (unless `nofollow` is also present).
5. Call `find_links` to queue same-domain links onto `self.urls_to_crawl`, applying `--exclude`/`--skipext`/`--drop`/robots.txt filtering. `--fetch-iframes` re-runs `find_links` against `<iframe>` tags, allowing cross-domain queueing.

Fetch failures split into two buckets: HTTPErrors (have `.code`, e.g. 404/403) are tallied per status code in `self.response_code`. Everything else (SSL errors, DNS/timeouts, connection resets — no `.code`) is logged at `ERROR` level (visible without `--debug`) and tallied in `self.network_errors`, since those used to vanish silently and were the root cause of "0 URLs found" reports.

## `--render-js` (optional JS rendering)

`--render-js` renders each page in headless Chromium via [Playwright](https://playwright.dev/python/) instead of a raw `urlopen()`, so links injected by client-side JS (React/Vue/Svelte SPAs) are visible to the regex parser. Design constraints, if you touch this:

- Playwright is imported lazily inside `Crawler.start_browser()`, not at module load time, so the base install stays dependency-free. If the import fails, log an actionable message and `exit(255)` — don't let it become an unhandled traceback.
- One browser instance is shared for the whole crawl (`Crawler.start_browser`/`stop_browser`, called from `run()`). `_fetch_rendered()` opens/closes a `page` per URL.
- Playwright's sync API isn't safe to drive from multiple threads, so `Crawler.__init__` forces `num_workers` to 1 whenever `render_js` is set.
- `_fetch_rendered()` returns a `_RenderedResponse` shim exposing `read()/getcode()/headers/geturl()/close()` — the same interface `__crawl()` already uses on a real urllib response — so the rest of `__crawl()` doesn't fork based on which fetch path ran.
- `Dockerfile.playwright` (separate from the default `Dockerfile`) is based on the official `mcr.microsoft.com/playwright/python` image, which ships Playwright + Chromium preinstalled — this is the easiest way to actually run `--render-js` without a local `pip install playwright && playwright install chromium`.

## `--resume`

Progress is stashed back into the `--output` file itself (no separate checkpoint file): finished `<url>` entries as normal, plus the still-pending frontier (`urls_to_crawl | in_flight`) as an XML comment (`<!--PENDING-URLS:...-->`) just before `</urlset>`. `write_progress_to_output`/`load_progress_from_output` read and write this format; `dedupe_url_strings_to_output` collapses duplicate `<url>` entries a resumed crawl can produce for pages that were mid-fetch when progress was last saved.

## Tests

`tests/test_crawler.py` is a stdlib `unittest` suite — no test framework dependency either. Run it with:

```
python -m unittest discover tests
```

It mocks `urlopen`/Playwright rather than hitting real network or a real browser, so it runs anywhere with just Python 3. When adding crawler behavior, prefer adding a test here over ad-hoc manual verification.

## Conventions worth preserving

- No new hard dependencies for the default install path. If a feature needs one, gate it behind an opt-in flag with a lazy import (see `--render-js`) and/or a separate Dockerfile, rather than adding it to a `requirements.txt` that everyone pays for.
- `Crawler.__init__` treats several class-level containers (`crawled_or_crawling`, `excluded`, `marked`, `response_code`) as instance state without re-assigning them per instance — a pre-existing quirk, not something introduced here. Tests work around it explicitly (see `make_crawler()` in `tests/test_crawler.py`); be aware of it if you see cross-instance state bleed.
