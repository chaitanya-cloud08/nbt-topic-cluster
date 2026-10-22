"""SQLite state: clusters keep stable ids across runs, so titles don't change under readers' thumbs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .models import Story

SCHEMA = """
CREATE TABLE IF NOT EXISTS clusters (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  terms TEXT NOT NULL,
  title TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  title_source TEXT NOT NULL,
  title_note TEXT NOT NULL DEFAULT '',
  tier TEXT NOT NULL,
  status TEXT NOT NULL,
  killed INTEGER NOT NULL DEFAULT 0,
  score REAL NOT NULL DEFAULT 0,
  sections TEXT NOT NULL DEFAULT '[]',
  title_member_count INTEGER NOT NULL DEFAULT 0,
  title_lead_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  newest_story_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  cluster_id TEXT NOT NULL,
  story_id TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (cluster_id, story_id)
);
CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  published TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  report TEXT
);
CREATE INDEX IF NOT EXISTS idx_members_story ON members(story_id);
"""


@dataclass
class ClusterRow:
    id: str
    key: str
    terms: list[str]
    title: str
    subtitle: str
    title_source: str
    title_note: str
    tier: str
    status: str
    killed: bool
    score: float
    sections: list[str]
    title_member_count: int
    title_lead_id: str
    created_at: str
    updated_at: str
    newest_story_at: str
    members: set[str] = field(default_factory=set)


def new_cluster_id(terms: list[str], now: datetime) -> str:
    h = hashlib.sha1(("|".join(sorted(terms)) + now.isoformat()).encode("utf-8")).hexdigest()[:10]
    return f"tc_{now:%Y%m%d}_{h}"


def story_to_json(s: Story) -> str:
    d = asdict(s)
    d["published"] = s.published.isoformat()
    d["updated"] = s.updated.isoformat()
    for k in ("section_msids", "l1s", "tiers"):
        d[k] = sorted(d[k])
    return json.dumps(d, ensure_ascii=False)


def story_from_json(text: str) -> Story:
    d = json.loads(text)
    d["published"] = datetime.fromisoformat(d["published"])
    d["updated"] = datetime.fromisoformat(d["updated"])
    for k in ("section_msids", "l1s", "tiers"):
        d[k] = set(d[k])
    return Story(**d)


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    # stories
    def save_stories(self, stories: list[Story]) -> None:
        self.db.executemany(
            "INSERT INTO stories(id, data, published) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, published=excluded.published",
            [(s.id, story_to_json(s), s.published.isoformat()) for s in stories])

    def stories(self, ids: set[str]) -> dict[str, Story]:
        if not ids:
            return {}
        out = {}
        q = ",".join("?" * len(ids))
        for row in self.db.execute(f"SELECT id, data FROM stories WHERE id IN ({q})", list(ids)):
            out[row["id"]] = story_from_json(row["data"])
        return out

    def prune_stories(self, before: datetime) -> None:
        self.db.execute("DELETE FROM stories WHERE published < ? AND id NOT IN "
                        "(SELECT m.story_id FROM members m JOIN clusters c ON c.id=m.cluster_id "
                        "WHERE c.status != 'archived')", (before.isoformat(),))

    # clusters
    def _row(self, r: sqlite3.Row) -> ClusterRow:
        c = ClusterRow(
            id=r["id"], key=r["key"], terms=json.loads(r["terms"]), title=r["title"], subtitle=r["subtitle"],
            title_source=r["title_source"], title_note=r["title_note"], tier=r["tier"], status=r["status"],
            killed=bool(r["killed"]), score=r["score"], sections=json.loads(r["sections"]),
            title_member_count=r["title_member_count"], title_lead_id=r["title_lead_id"],
            created_at=r["created_at"], updated_at=r["updated_at"], newest_story_at=r["newest_story_at"])
        c.members = {m["story_id"] for m in self.db.execute(
            "SELECT story_id FROM members WHERE cluster_id=?", (c.id,))}
        return c

    def clusters(self, include_archived: bool = False) -> list[ClusterRow]:
        sql = "SELECT * FROM clusters" + ("" if include_archived else " WHERE status != 'archived'")
        return [self._row(r) for r in self.db.execute(sql + " ORDER BY score DESC")]

    def cluster(self, cid: str) -> ClusterRow | None:
        r = self.db.execute("SELECT * FROM clusters WHERE id=?", (cid,)).fetchone()
        return self._row(r) if r else None

    def upsert_cluster(self, c: ClusterRow, now: datetime) -> None:
        self.db.execute(
            """INSERT INTO clusters(id,key,terms,title,subtitle,title_source,title_note,tier,status,killed,score,
               sections,title_member_count,title_lead_id,created_at,updated_at,newest_story_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET key=excluded.key, terms=excluded.terms, title=excluded.title,
               subtitle=excluded.subtitle, title_source=excluded.title_source, title_note=excluded.title_note,
               tier=excluded.tier, status=excluded.status, score=excluded.score, sections=excluded.sections,
               title_member_count=excluded.title_member_count, title_lead_id=excluded.title_lead_id,
               updated_at=excluded.updated_at, newest_story_at=excluded.newest_story_at""",
            (c.id, c.key, json.dumps(c.terms, ensure_ascii=False), c.title, c.subtitle, c.title_source,
             c.title_note, c.tier, c.status, int(c.killed), c.score, json.dumps(c.sections), c.title_member_count,
             c.title_lead_id, c.created_at, c.updated_at, c.newest_story_at))
        existing = {m["story_id"] for m in self.db.execute(
            "SELECT story_id FROM members WHERE cluster_id=?", (c.id,))}
        for sid in c.members - existing:
            self.db.execute("INSERT INTO members(cluster_id, story_id, added_at) VALUES(?,?,?)",
                            (c.id, sid, now.isoformat()))
        for sid in existing - c.members:
            self.db.execute("DELETE FROM members WHERE cluster_id=? AND story_id=?", (c.id, sid))

    def set_status(self, cid: str, status: str) -> None:
        self.db.execute("UPDATE clusters SET status=? WHERE id=?", (status, cid))

    def set_killed(self, cid: str, killed: bool) -> bool:
        cur = self.db.execute("UPDATE clusters SET killed=? WHERE id=?", (int(killed), cid))
        self.db.commit()
        return cur.rowcount > 0

    def member_added_at(self, cid: str) -> dict[str, str]:
        return {r["story_id"]: r["added_at"] for r in self.db.execute(
            "SELECT story_id, added_at FROM members WHERE cluster_id=?", (cid,))}

    # runs
    def start_run(self, now: datetime) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at) VALUES(?)", (now.isoformat(),))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, finished: datetime, report: dict) -> None:
        self.db.execute("UPDATE runs SET finished_at=?, report=? WHERE id=?",
                        (finished.isoformat(), json.dumps(report, ensure_ascii=False, default=str), run_id))
        self.db.commit()
