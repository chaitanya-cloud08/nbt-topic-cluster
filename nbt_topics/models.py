"""Story model and parsing of articlelist items."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def parse_feed_time(value: str, tz: ZoneInfo) -> datetime | None:
    """Articlelist times look like 'Oct 22, 2026, 11:33 AM' (IST)."""
    if not value:
        return None
    for fmt in ("%b %d, %Y, %I:%M %p", "%b %d, %Y, %I:%M:%S %p", "%d %b %Y, %I:%M:%S %p"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    if value.isdigit() and len(value) >= 12:  # epoch millis, as in articleshow feeds
        return datetime.fromtimestamp(int(value) / 1000, tz)
    return None


@dataclass
class Story:
    id: str
    hl: str
    syn: str
    seolocation: str
    subsecname: str
    subsecmsid: str
    imageid: str
    imgsize: str
    cmstype: str
    published: datetime
    updated: datetime
    lu_text: str
    section_msids: set[str] = field(default_factory=set)  # feeds this story was found in
    l1s: set[str] = field(default_factory=set)
    tiers: set[str] = field(default_factory=set)

    @property
    def slug(self) -> str:
        return self.seolocation.rsplit("/", 1)[-1]

    @classmethod
    def from_item(cls, item: dict, tz: ZoneInfo) -> "Story | None":
        sid = str(item.get("id") or item.get("msid") or "").strip()
        hl = strip_html(item.get("hl", ""))
        if not sid or not hl:
            return None
        if item.get("tn") not in (None, "news", "photo", "video", "photostory"):
            return None  # ads, widgets, promos
        published = parse_feed_time(item.get("dl", ""), tz) or parse_feed_time(item.get("lu", ""), tz)
        updated = parse_feed_time(item.get("lu", ""), tz) or published
        if published is None:
            return None
        return cls(
            id=sid,
            hl=hl,
            syn=strip_html(item.get("syn", "")),
            seolocation=str(item.get("seolocation", "")),
            subsecname=str(item.get("subsecname", "")),
            subsecmsid=str(item.get("subsecmsid", "")),
            imageid=str(item.get("imageid", "")),
            imgsize=str(item.get("imgsize", "")),
            cmstype=str(item.get("cmstype", "")),
            published=published,
            updated=updated,
            lu_text=str(item.get("lu", "")),
        )
