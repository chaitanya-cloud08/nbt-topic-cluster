"""Find candidate topics: terms (or pairs of terms) shared by several recent stories."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations

from .models import Story
from .text import TermExtractor


@dataclass
class Candidate:
    terms: list[str]                 # e.g. ["en:doctor", "hi:डॉक्टर"]
    members: set[str]                # story ids
    score: float = 0.0
    sections_spanned: int = 1
    kind: str = "single"             # "single" or "pair"
    rejected: str | None = None      # reason, when dropped
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return "|".join(sorted(self.terms[:2]))


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_candidates(pool: dict[str, Story], extractor: TermExtractor, cfg: dict,
                    now: datetime) -> tuple[list[Candidate], list[Candidate], dict[str, set[str]]]:
    """Returns (accepted, rejected, story_terms)."""
    min_n = int(cfg.get("min_stories", 3))
    max_single = int(cfg.get("max_single_df", 12))
    max_size = int(cfg.get("max_cluster_size", 30))
    merge_j = float(cfg.get("merge_jaccard", 0.8))
    overlap_reject = float(cfg.get("overlap_reject", 0.5))
    half_life = float(cfg.get("half_life_hours", 24))
    spread_bonus = float(cfg.get("section_spread_bonus", 0.25))
    mirror_min = int(cfg.get("mirror_min_subsection", 6))
    mirror_cov = float(cfg.get("mirror_coverage", 0.9))

    story_terms = {sid: extractor.story_terms(s.slug, s.hl) for sid, s in pool.items()}
    postings: dict[str, set[str]] = defaultdict(set)
    for sid, terms in story_terms.items():
        for t in terms:
            postings[t].add(sid)
    frequent = {t: ids for t, ids in postings.items() if len(ids) >= min_n}

    raw: list[Candidate] = []
    # Single terms that are specific enough.
    for t, ids in frequent.items():
        if len(ids) <= max_single:
            raw.append(Candidate(terms=[t], members=set(ids)))
    # Broad terms ("delhi", "modi") only count when paired with another term in the same stories.
    broad = {t for t, ids in frequent.items() if len(ids) > max_single}
    if broad:
        pair_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        for sid, terms in story_terms.items():
            fts = sorted(t for t in terms if t in frequent)
            for a, b in combinations(fts, 2):
                if a in broad or b in broad:
                    pair_ids[(a, b)].add(sid)
        for (a, b), ids in pair_ids.items():
            if min_n <= len(ids) <= max_size:
                raw.append(Candidate(terms=[a, b], members=set(ids), kind="pair"))

    # Merge terms that describe the same stories ("en:doctor" + "hi:डॉक्टर").
    raw.sort(key=lambda c: (-len(c.members), c.terms))
    groups: list[Candidate] = []
    for c in raw:
        for g in groups:
            if jaccard(g.members, c.members) >= merge_j:
                g.terms.extend(t for t in c.terms if t not in g.terms)
                g.members |= c.members
                break
        else:
            groups.append(Candidate(terms=list(c.terms), members=set(c.members), kind=c.kind))

    # Sub-section sizes, for the mirror check.
    subsec_size = Counter(s.subsecmsid for s in pool.values() if s.subsecmsid)

    def score(c: Candidate) -> None:
        spread = len({pool[i].subsecmsid or pool[i].seolocation.split("/")[0] for i in c.members})
        c.sections_spanned = spread
        w = 0.0
        for i in c.members:
            age_h = max(0.0, (now - pool[i].published).total_seconds() / 3600)
            w += math.pow(0.5, age_h / half_life)
        c.score = w * (1 + spread_bonus * (spread - 1))

    accepted: list[Candidate] = []
    rejected: list[Candidate] = []
    for g in groups:
        score(g)
    for g in sorted(groups, key=lambda c: -c.score):
        if len(g.members) > max_size:
            g.rejected = f"too broad ({len(g.members)} stories)"
        else:
            subsecs = {pool[i].subsecmsid for i in g.members}
            if len(subsecs) == 1:
                sub = next(iter(subsecs))
                size = subsec_size.get(sub, 0)
                if size >= mirror_min and len(g.members) >= mirror_cov * size:
                    g.rejected = f"mirrors sub-section {pool[next(iter(g.members))].subsecname}"
            if not g.rejected:
                for a in accepted:
                    shared = len(g.members & a.members)
                    if shared / min(len(g.members), len(a.members)) > overlap_reject:
                        g.rejected = f"overlaps a stronger topic ({' + '.join(a.terms[:2])})"
                        break
        (rejected if g.rejected else accepted).append(g)
    return accepted, rejected, story_terms
