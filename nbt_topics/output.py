"""Turn stored clusters into the JSON the app reads: app home, each section page, and articleshow lookups."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .models import Story
from .store import ClusterRow, Store
from .taxonomy import Taxonomy


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # atomic: readers never see a half-written file


def cluster_json(c: ClusterRow, stories: dict[str, Story], config: Config, now: datetime) -> dict:
    tmpl = config.section("feed").get("article_url", "")
    img_tmpl = config.section("feed").get("image_url", "")
    new_h = float(config.section("lifecycle").get("new_badge_hours", 3))
    members = sorted((stories[i] for i in c.members if i in stories), key=lambda s: s.published, reverse=True)
    items = [{
        "id": s.id,
        "hl": s.hl,
        "syn": (s.syn[:260].rsplit(" ", 1)[0] + "…") if len(s.syn) > 260 else s.syn,
        "imageid": s.imageid,
        "imgsize": s.imgsize,
        "img": img_tmpl.format(imageid=s.imageid, imgsize=s.imgsize or "0") if (img_tmpl and s.imageid) else "",
        "subsecname": s.subsecname,
        "cmstype": s.cmstype,
        "lu": s.lu_text,
        "published": s.published.isoformat(),
        "wu": tmpl.format(seolocation=s.seolocation, id=s.id) if tmpl else "",
        "isNew": (now - s.published) <= timedelta(hours=new_h),
    } for s in members]
    return {
        "tn": "topicCluster",
        "clusterId": c.id,
        "title": c.title,
        "subtitle": c.subtitle,
        "titleSource": c.title_source,
        "tier": c.tier,
        "status": c.status,
        "count": len(items),
        "upd": members[0].lu_text if members else "",
        "heroImageId": members[0].imageid if members else "",
        "sectionsSpanned": sorted({s.subsecname for s in members if s.subsecname}),
        "items": items,
    }


def build_outputs(store: Store, taxonomy: Taxonomy, config: Config, now: datetime) -> dict:
    ccfg = config.section("clustering")
    out_dir = config.path(config.section("output").get("dir", "out"))
    min_n = int(ccfg.get("min_stories", 3))
    home_n = int(ccfg.get("home_count", 5))
    section_n = int(ccfg.get("section_count", 3))

    live = [c for c in store.clusters() if not c.killed and c.status in ("active", "cooling")
            and len(c.members) >= min_n]
    stories = store.stories({i for c in live for i in c.members})
    rendered = {c.id: cluster_json(c, stories, config, now) for c in live}
    live = [c for c in live if rendered[c.id]["count"] >= min_n]
    live.sort(key=lambda c: -c.score)
    generated = now.isoformat()

    home = [rendered[c.id] for c in live if c.status == "active"][:home_n]
    _write(out_dir / "home.json", {"tn": "topicClusterList", "scope": "home", "generatedAt": generated,
                                   "clusters": home})

    by_section: dict[str, list[dict]] = {}
    for c in live:
        for msid in c.sections:
            by_section.setdefault(msid, []).append(rendered[c.id])
    names = {s.msid: s.name_hi for s in taxonomy.sections}
    section_dir = out_dir / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    keep_files = set()
    for msid, lst in by_section.items():
        keep_files.add(f"{msid}.json")
        _write(section_dir / f"{msid}.json", {"tn": "topicClusterList", "scope": "section", "msid": msid,
                                              "sectionName": names.get(msid, ""), "generatedAt": generated,
                                              "clusters": lst[:section_n]})
    for f in section_dir.glob("*.json"):
        if f.name not in keep_files:
            f.unlink()  # a section whose clusters all expired shows nothing
    levels = {s.msid: s.level for s in taxonomy.sections}
    _write(out_dir / "sections_index.json", {"generatedAt": generated, "sections": [
        {"msid": m, "sectionName": names.get(m, ""), "level": levels.get(m, 0), "count": min(len(lst), section_n)}
        for m, lst in sorted(by_section.items(), key=lambda kv: (levels.get(kv[0], 9), names.get(kv[0], "")))]})

    story_index: dict[str, list[str]] = {}
    for c in live:
        for sid in c.members:
            story_index.setdefault(sid, []).append(c.id)
    _write(out_dir / "story_index.json", {"generatedAt": generated, "stories": story_index})
    _write(out_dir / "clusters.json", {"generatedAt": generated, "clusters": rendered})
    return {"home": len(home), "sections": len(by_section), "stories_indexed": len(story_index),
            "live_clusters": len(live)}
