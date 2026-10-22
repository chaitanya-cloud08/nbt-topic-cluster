"""Crawl articlelist feeds for every section into one de-duplicated story pool."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import Config
from .models import Story
from .taxonomy import Section

log = logging.getLogger(__name__)


class FeedSource(Protocol):
    def fetch(self, msid: str, page: int, max_age_minutes: float) -> dict | None: ...


class HttpFeedSource:
    """Fetches articlelist pages over HTTP, with retries and an on-disk cache per tier refresh interval."""

    def __init__(self, config: Config):
        f = config.section("feed")
        self.base_url = f["base_url"]
        self.params = dict(f.get("params", {}))
        self.page_param = f.get("page_param", "curpg")
        self.timeout = float(f.get("timeout_s", 10))
        self.retries = int(f.get("retries", 2))
        self.user_agent = f.get("user_agent", "nbt-topic-clusters/1.0")
        self.cache_dir = config.path(f.get("cache_dir", "state/feed_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def url(self, msid: str, page: int) -> str:
        params = dict(self.params, msid=msid)
        if page > 1:
            params[self.page_param] = page
        return f"{self.base_url}?{urllib.parse.urlencode(params)}"

    def fetch(self, msid: str, page: int, max_age_minutes: float) -> dict | None:
        cache = self.cache_dir / f"{msid}_p{page}.json"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_minutes * 60:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        url = self.url(msid, page)
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                data = json.loads(body)
                cache.write_text(body, encoding="utf-8")
                return data
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as err:
                last_err = err
                if isinstance(err, urllib.error.HTTPError) and err.code == 404:
                    break
                time.sleep(0.5 * (2 ** attempt))
        log.warning("feed %s page %s failed: %s", msid, page, last_err)
        if cache.exists():  # serve stale rather than nothing
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None


class ReplayFeedSource:
    """Reads saved feed responses from a folder: <msid>.json (page 1) or <msid>_p<N>.json."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def fetch(self, msid: str, page: int, max_age_minutes: float) -> dict | None:
        candidates = [self.folder / f"{msid}_p{page}.json"]
        if page == 1:
            candidates.append(self.folder / f"{msid}.json")
        for p in candidates:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None


@dataclass
class CrawlReport:
    sections_requested: int = 0
    sections_with_data: int = 0
    pages_fetched: int = 0
    items_seen: int = 0
    stories_in_window: int = 0
    failures: list[str] = field(default_factory=list)
    paging_ignored: list[str] = field(default_factory=list)


def crawl(sections: list[Section], source: FeedSource, config: Config, now: datetime,
          l1_of: dict[str, str]) -> tuple[dict[str, Story], CrawlReport]:
    """Fetch every section's recent stories and merge them into one pool keyed by story id."""
    pool_cfg = config.section("pool")
    tz = ZoneInfo(pool_cfg.get("timezone", "Asia/Kolkata"))
    window_start = now - timedelta(hours=float(pool_cfg.get("window_hours", 72)))
    max_pages = int(config.section("feed").get("max_pages", 3))
    workers = int(config.section("feed").get("concurrency", 8))
    report = CrawlReport(sections_requested=len(sections))

    def crawl_one(section: Section) -> tuple[Section, list[Story], int, int, str | None, bool]:
        refresh = float(config.tier(section.tier).get("refresh_minutes", 30))
        found: list[Story] = []
        pages = items = 0
        first_ids: list[str] | None = None
        paging_ignored = False
        for page in range(1, max_pages + 1):
            data = source.fetch(section.msid, page, refresh)
            if not data:
                return section, found, pages, items, (None if pages else f"{section.msid} {section.path}"), paging_ignored
            pages += 1
            raw = data.get("items") or []
            ids = [str(i.get("id", "")) for i in raw[:5]]
            if page > 1 and ids == first_ids:
                paging_ignored = True
                break
            first_ids = first_ids or ids
            oldest_in_page = None
            for item in raw:
                items += 1
                story = Story.from_item(item, tz)
                if story is None:
                    continue
                oldest_in_page = story.published if oldest_in_page is None else min(oldest_in_page, story.published)
                if window_start <= story.published <= now + timedelta(minutes=5):
                    found.append(story)
            pg = data.get("pg") or {}
            total_pages = int(pg.get("tp", page) or page)
            if page >= total_pages or (oldest_in_page and oldest_in_page < window_start):
                break
        return section, found, pages, items, None, paging_ignored

    pool: dict[str, Story] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for section, stories, pages, items, failure, paging_ignored in ex.map(crawl_one, sections):
            report.pages_fetched += pages
            report.items_seen += items
            if failure:
                report.failures.append(failure)
            if paging_ignored:
                report.paging_ignored.append(section.msid)
            if stories:
                report.sections_with_data += 1
            for st in stories:
                existing = pool.get(st.id)
                if existing is None:
                    pool[st.id] = st
                    existing = st
                existing.section_msids.add(section.msid)
                existing.l1s.add(l1_of.get(section.msid, section.l1))
                existing.tiers.add(section.tier)
    report.stories_in_window = len(pool)
    if report.paging_ignored:
        log.warning("page param '%s' seems ignored for %d sections; only page 1 used there",
                    config.section("feed").get("page_param"), len(report.paging_ignored))
    return pool, report
