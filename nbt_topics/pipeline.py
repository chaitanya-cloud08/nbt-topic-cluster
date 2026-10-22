"""One run: crawl -> candidates -> verify -> match to existing clusters -> title -> lifecycle -> save."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta

from .candidates import Candidate, find_candidates, jaccard
from .config import TIER_ORDER, Config
from .editorial import make_title, verify
from .feed import FeedSource, crawl
from .llm import LLM
from .models import Story
from .store import ClusterRow, Store, new_cluster_id
from .taxonomy import Taxonomy
from .text import TermExtractor

log = logging.getLogger(__name__)

MATCH_JACCARD = 0.34
RETITLE_GROWTH = 1.3


def strictest_tier(stories: list[Story]) -> str:
    tiers = {t for s in stories for t in s.tiers} or {"medium"}
    return max(tiers, key=lambda t: TIER_ORDER.get(t, 1))


def placement(members: list[Story], taxonomy: Taxonomy, share: float) -> list[str]:
    """Section msids (any level, plus their L1) where at least `share` of the cluster's stories were found."""
    by_msid = taxonomy.by_msid()
    counts: Counter[str] = Counter()
    for s in members:
        seen: set[str] = set()
        for msid in s.section_msids:
            seen.add(msid)
            sec = by_msid.get(msid)
            if sec is not None:
                l1 = taxonomy.l1_msid(sec)
                if l1:
                    seen.add(l1)
        counts.update(seen)
    need = share * len(members)
    return sorted(m for m, c in counts.items() if c >= need)


def lifecycle(newest: datetime, tier: str, config: Config, now: datetime) -> str:
    active_h = float(config.tier(tier).get("active_hours", 24))
    mult = float(config.section("lifecycle").get("cooling_multiplier", 2.0))
    age_h = (now - newest).total_seconds() / 3600
    if age_h <= active_h:
        return "active"
    if age_h <= active_h * mult:
        return "cooling"
    return "archived"


def run_once(config: Config, taxonomy: Taxonomy, source: FeedSource, store: Store, llm: LLM | None,
             now: datetime, only_msids: set[str] | None = None) -> dict:
    run_id = store.start_run(now)
    ccfg = config.section("clustering")
    guard = config.section("guardrails")
    llm_cfg = config.section("llm")
    min_n = int(ccfg.get("min_stories", 3))

    sections = [s for s in taxonomy.sections if s.msid not in config.disabled_msids
                and (only_msids is None or s.msid in only_msids)]
    l1_of = {s.msid: s.l1 for s in taxonomy.sections}
    pool, crawl_report = crawl(sections, source, config, now, l1_of)
    store.save_stories(list(pool.values()))

    extractor = TermExtractor(taxonomy.section_words())
    accepted, rejected, _ = find_candidates(pool, extractor, ccfg, now)

    existing = store.clusters(include_archived=False)
    matched_ids: set[str] = set()
    events: list[dict] = []
    llm_calls_before = llm.calls if llm else 0

    for cand in accepted:
        # 1. Is this an existing cluster?
        best, best_j = None, 0.0
        for ex in existing:
            if ex.id in matched_ids:
                continue
            j = 1.0 if ex.key == cand.key else jaccard(ex.members, cand.members)
            if j > best_j:
                best, best_j = ex, j
        if best is not None and best_j < MATCH_JACCARD:
            best = None

        # 2. Verify only when there is something new to verify.
        new_ids = cand.members - (best.members if best else set())
        if best is not None and not new_ids:
            members = set(best.members)
            verify_note = "unchanged"
        else:
            kept, verify_note = verify(cand, pool, llm, int(llm_cfg.get("max_candidates", 40)), min_n)
            if not kept:
                cand.rejected = verify_note
                rejected.append(cand)
                if best is not None:
                    matched_ids.add(best.id)  # keep the existing cluster as it was
                continue
            members = kept | (best.members if best else set())

        stories = store.stories(members)
        stories.update({i: pool[i] for i in members if i in pool})
        member_stories = [stories[i] for i in members if i in stories]
        if len(member_stories) < min_n:
            continue
        tier = strictest_tier(member_stories)
        newest = max(s.published for s in member_stories)
        lead = max(member_stories, key=lambda s: s.published)

        if best is None:
            row = ClusterRow(id=new_cluster_id(cand.terms, now), key=cand.key, terms=cand.terms, title="",
                             subtitle="", title_source="", title_note="", tier=tier, status="active",
                             killed=False, score=cand.score, sections=[], title_member_count=0,
                             title_lead_id="", created_at=now.isoformat(), updated_at=now.isoformat(),
                             newest_story_at=newest.isoformat())
            action = "created"
        else:
            row = best
            matched_ids.add(best.id)
            action = "updated" if new_ids else "unchanged"

        row.members = {s.id for s in member_stories}
        row.terms = list(dict.fromkeys(row.terms + cand.terms))[:8]
        row.score = cand.score
        row.newest_story_at = newest.isoformat()
        row.sections = placement(member_stories, taxonomy, float(ccfg.get("section_share", 0.6)))

        needs_title = (not row.title or tier != row.tier
                       or len(row.members) >= RETITLE_GROWTH * max(1, row.title_member_count)
                       or (row.title_source == "headline" and lead.id != row.title_lead_id))
        row.tier = tier
        if needs_title:
            t = make_title(cand, member_stories, tier, llm, guard)
            row.title, row.subtitle, row.title_source, row.title_note = t.title, t.subtitle, t.source, t.note
            row.title_member_count = len(row.members)
            row.title_lead_id = lead.id
        row.status = lifecycle(newest, tier, config, now)
        row.updated_at = now.isoformat()
        with store.tx():
            store.upsert_cluster(row, now)
        events.append({"cluster": row.id, "action": action, "title": row.title, "title_source": row.title_source,
                       "title_note": row.title_note, "verify": verify_note, "stories": len(row.members),
                       "tier": tier, "status": row.status, "killed": row.killed, "score": round(row.score, 3)})

    # Clusters with no new candidate this run: only age them.
    with store.tx():
        for ex in existing:
            if ex.id in matched_ids:
                continue
            status = lifecycle(datetime.fromisoformat(ex.newest_story_at), ex.tier, config, now)
            if status != ex.status:
                store.set_status(ex.id, status)
                events.append({"cluster": ex.id, "action": f"status -> {status}", "title": ex.title})
        window_h = float(config.section("pool").get("window_hours", 72))
        store.prune_stories(now - timedelta(hours=window_h * 2))

    report = {
        "run_id": run_id,
        "now": now.isoformat(),
        "crawl": asdict(crawl_report),
        "candidates_accepted": len(accepted),
        "rejected": [{"terms": c.terms[:3], "stories": len(c.members), "reason": c.rejected} for c in rejected],
        "clusters": events,
        "llm_calls": (llm.calls - llm_calls_before) if llm else 0,
    }
    store.finish_run(run_id, datetime.now(now.tzinfo), report)
    return report
