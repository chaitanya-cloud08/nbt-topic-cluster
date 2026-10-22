"""Find candidate topics: names and specific words that several recent stories are about.

One shared word is not a topic: "डॉक्टर", "कोर्ट" or "सिंह" turn up in stories that have nothing else in common.
A word names a topic when it is written in both the Hindi headline and the English URL (names are) and the
stories using it also share other specific words with each other ("रुबियो" stories say "मार्को", "जामनगर")."""
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
    terms: list[str]                 # e.g. ["hi:टाइगर", "hi:श्रॉफ", "en:tiger", "en:tigershroff"]
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


def _ordered_terms(terms: list[str], postings: dict[str, set[str]]) -> list[str]:
    """Hindi words first (they become the rule-based label), then by how many stories use them."""
    return sorted(terms, key=lambda t: (not t.startswith("hi:"), -len(postings[t]), t))


def _same_phrase(a: list[str], b: list[str]) -> bool:
    """True when every English word of b is a piece of a's phrase: "yadav" next to "akhilesh" + "akhileshyadav",
    "mobile" inside "mobilephone". ("shrofffiring" + "firing" is not just a piece of "tiger shroff".)"""
    words_a = {t[3:] for t in a if t.startswith("en:")}
    words_b = {t[3:] for t in b if t.startswith("en:")}

    def piece(y: str) -> bool:
        return any(x + y in words_a or y + x in words_a
                   or (len(y) >= 3 and len(x) > len(y) and (x.startswith(y) or x.endswith(y)))
                   or (len(x) >= 3 and len(y) > len(x) and (y.startswith(x) or y.endswith(x)))
                   for x in words_a)
    return bool(words_b) and all(piece(y) for y in words_b)


def find_candidates(pool: dict[str, Story], extractor: TermExtractor, cfg: dict,
                    now: datetime) -> tuple[list[Candidate], list[Candidate], dict[str, set[str]]]:
    """Returns (accepted, rejected, story_terms)."""
    min_n = int(cfg.get("min_stories", 3))
    max_size = int(cfg.get("max_cluster_size", 30))
    merge_j = float(cfg.get("merge_jaccard", 0.8))
    concept_j = float(cfg.get("concept_jaccard", 0.6))
    concept_contain = float(cfg.get("concept_containment", 0.7))
    anchor_min = float(cfg.get("anchor_cohesion", 0.4))
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

    # 1. Concepts: terms that name the same thing in the same stories ("en:tiger" + "hi:टाइगर",
    #    "en:karwachauth" + "en:karwa" + "hi:करवाचौथ"), so one idea is never counted twice.
    concepts: list[tuple[list[str], set[str]]] = []
    for t, ids in sorted(frequent.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        for terms, cids in concepts:
            if jaccard(cids, ids) >= concept_j:
                terms.append(t)
                cids |= ids
                break
        else:
            concepts.append(([t], set(ids)))
    concepts = [(_ordered_terms(terms, postings), ids) for terms, ids in concepts]
    story_concepts: dict[str, list[int]] = defaultdict(list)
    for ci, (_, ids) in enumerate(concepts):
        for sid in ids:
            story_concepts[sid].append(ci)

    # 2. Which concepts can name a topic. A topic word is used in stories about one thing, so those stories also
    #    share other specific words ("रुबियो" stories say "मार्को", "जामनगर"); a generic word ("मौत", "कोर्ट",
    #    "डॉक्टर") turns up in stories that share nothing else. Measured twice, so that sharing another generic
    #    word does not count.
    concept_sets = {sid: set(cis) for sid, cis in story_concepts.items()}

    def echo(a: int, b: int) -> bool:
        """b only repeats a: a piece of the same phrase ("mobile" in "mobilephone"), or the other language's word
        for it in most of the same stories ("en:son" in the slugs of "बेटे" headlines)."""
        (ta, ia), (tb, ib) = concepts[a], concepts[b]
        if _same_phrase(ta, tb):
            return True
        langs = ({t[:3] for t in ta}, {t[:3] for t in tb})
        return langs in (({"hi:"}, {"en:"}), ({"en:"}, {"hi:"})) and len(ia & ib) >= concept_contain * min(len(ia), len(ib))

    def cohesion(ci: int, counted: set[int]) -> float:
        ids = sorted(concepts[ci][1])
        pairs = linked = 0
        for x, y in combinations(ids, 2):
            pairs += 1
            if any(c != ci and c in counted and not echo(ci, c) for c in concept_sets[x] & concept_sets[y]):
                linked += 1
        return linked / pairs if pairs else 0.0

    # Words in more stories than one topic can hold ("दिल्ली", "मौत") never vouch for another word.
    specific = {ci for ci, (_, ids) in enumerate(concepts) if len(ids) <= max_size}
    first = {ci: cohesion(ci, specific) for ci in specific}
    counted = {ci for ci, c in first.items() if c >= anchor_min / 2}
    second = {ci: cohesion(ci, counted) for ci in range(len(concepts))}
    # A name is written in the Hindi headline and the English URL alike ("टाइगर" / "tiger"); a word that only
    # shows up in one of them ("मारी", "injured", "pay") is a common word, not a topic.
    both = {ci for ci, (terms, _) in enumerate(concepts)
            if any(t.startswith("hi:") for t in terms) and any(t.startswith("en:") for t in terms)}
    anchors = {ci for ci, c in second.items() if c >= anchor_min and ci in both}

    def candidate(cis: list[int], ids: set[str], kind: str) -> Candidate | None:
        """Drops stories that share nothing with the rest beyond the topic's own words."""
        own = set(cis)
        vouch = {c for c in counted - own if not any(echo(o, c) for o in own)}
        keep = {x for x in ids if any(concept_sets[x] & concept_sets[y] & vouch for y in ids if y != x)}
        if len(keep) < min_n:
            return None
        terms = [concepts[c][0][0] for c in cis] + [t for c in cis for t in concepts[c][0][1:]]
        return Candidate(terms=terms, members=keep, kind=kind,
                         extra={"cohesion": round(min(second[c] for c in cis), 2)})

    # 3. A topic is an anchor concept with the stories that use it; an anchor too big to be one topic
    #    ("टाइगर श्रॉफ" over a whole week) is split by the other concepts next to it.
    raw: list[Candidate] = []
    for ci in anchors:
        ids = concepts[ci][1]
        if len(ids) <= max_size:
            c = candidate([ci], ids, "single")
            if c:
                raw.append(c)
            continue
        by_other: dict[int, set[str]] = defaultdict(set)
        for sid in ids:
            for other in concept_sets[sid] - {ci}:
                if other in counted or other in anchors:
                    by_other[other].add(sid)
        for other, sub in by_other.items():
            if min_n <= len(sub) <= max_size and not echo(ci, other):
                c = candidate([ci, other], sub, "pair")
                if c:
                    raw.append(c)

    # 4. Candidates covering (almost) the same stories are one topic ("आजम" and "रामपुर जेल").
    raw.sort(key=lambda c: (-len(c.members), c.terms))
    groups: list[Candidate] = []
    for c in raw:
        for g in groups:
            if jaccard(g.members, c.members) >= merge_j:
                g.terms.extend(t for t in c.terms if t not in g.terms)
                g.members |= c.members
                g.extra["cohesion"] = max(g.extra["cohesion"], c.extra["cohesion"])
                break
        else:
            groups.append(Candidate(terms=list(c.terms), members=set(c.members), kind=c.kind, extra=dict(c.extra)))

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
    # Where candidates overlap, the more coherent one wins ("श्रद्धा दुबे" over "नागपुर", which also has RSS stories).
    for g in sorted(groups, key=lambda c: -c.score * c.extra["cohesion"]):
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
