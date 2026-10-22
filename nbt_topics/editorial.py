"""The two AI steps (verify membership, write the title) and the guardrails around them.

Every AI output is checked; anything that fails a check falls back to a rule-based result,
so a bad model reply can never reach the app.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from .candidates import Candidate
from .llm import LLM, LLMError
from .models import Story
from .text import HINDI_STOP, hindi_words, latin_words, normalise, stem_en, years_in

log = logging.getLogger(__name__)

TIER_MODE = {"soft": "ai", "medium": "ai_entities", "hard": "headline"}


@dataclass
class TitleResult:
    title: str
    subtitle: str
    source: str          # "ai", "rules" or "headline"
    note: str = ""


def _ordered(members: list[Story]) -> list[Story]:
    return sorted(members, key=lambda s: s.published, reverse=True)


# ---------- verify ----------

VERIFY_SYSTEM = """You check story groupings for Navbharat Times, a Hindi news publisher.
You get a list of stories that share some words. Decide which of them are about ONE specific topic,
event, person or recurring theme. Sharing a common word is not enough: the stories must be things a
reader following that topic would want together.
Reply with JSON only: {"keep": ["<id>", ...], "reason": "<one short English sentence>"}
Return "keep": [] if they do not form one topic."""


def verify(candidate: Candidate, pool: dict[str, Story], llm: LLM | None, max_candidates: int,
           min_stories: int) -> tuple[set[str], str]:
    """Returns (kept member ids, note). Without an LLM, membership is kept as found."""
    if llm is None:
        return set(candidate.members), "rules"
    stories = _ordered([pool[i] for i in candidate.members])[:max_candidates]
    shared = ", ".join(t.split(":", 1)[1] for t in candidate.terms[:4])
    lines = [f'{s.id} | {s.hl} | {s.syn[:220]}' for s in stories]
    user = f"Shared words: {shared}\n\nStories (id | headline | synopsis):\n" + "\n".join(lines)
    try:
        out = llm.complete_json(VERIFY_SYSTEM, user, max_tokens=400)
    except LLMError as err:
        log.warning("verify failed for %s: %s; keeping rule-based membership", candidate.terms, err)
        return set(candidate.members), f"rules (verify error: {err})"
    keep = {str(i) for i in out.get("keep", [])} & candidate.members
    if len(keep) < min_stories:
        return set(), f"ai rejected: {out.get('reason', '')}"
    return keep, f"ai: {out.get('reason', '')}"


# ---------- titles ----------

TITLE_SYSTEM = """You write the label for a topic carousel in the Navbharat Times app (Hindi).
Rules:
- "title": 2 to {title_max} words, in Hindi (names and numbers as they appear in the headlines).
- "subtitle": at most {subtitle_max} words, neutral, says what the stories cover.
- Use only words, names and numbers that appear in the given headlines or synopses. Add no facts.
- Neutral and descriptive. No opinions, no judgment words, no clickbait, no quotes, no emoji.
{mode_rule}
Reply with JSON only: {{"title": "...", "subtitle": "..."}}"""

MODE_RULES = {
    "ai": "- The title names the topic, event or theme (e.g. a festival, a series, a recurring advice format).",
    "ai_entities": "- The title may contain ONLY names of people, teams, places, organisations, events, films or "
                   "products from the headlines (e.g. 'विराट कोहली', 'IPL 2026'). No verbs, no description.",
}


def source_vocab(members: list[Story]) -> set[str]:
    vocab: set[str] = set()
    for s in members:
        for text in (s.hl, s.syn):
            vocab.update(hindi_words(text))
            vocab.update(stem_en(w) for w in latin_words(text))
            vocab.update(re.findall(r"\d+", text))
        vocab.update(years_in(s.slug.replace("-", " ")))
    return vocab


def check_title(title: str, subtitle: str, members: list[Story], guard: dict) -> str | None:
    """Returns a reason string if the AI title breaks a rule, else None."""
    t_words = title.split()
    if not title.strip() or not (1 <= len(t_words) <= int(guard.get("title_max_words", 6))):
        return f"title length {len(t_words)} words"
    if len(subtitle.split()) > int(guard.get("subtitle_max_words", 16)):
        return "subtitle too long"
    if re.search(r"[\"“”'‘’!?|#@]|[\U0001F300-\U0001FAFF]", title + subtitle):
        return "quotes, punctuation or emoji"
    vocab = source_vocab(members)
    text = f"{title} {subtitle}"
    for w in hindi_words(text):
        if w in HINDI_STOP or len(w) < 2:
            continue
        if w not in vocab:
            return f"word not in source: {w}"
    for w in latin_words(text):
        if stem_en(w) not in vocab:
            return f"word not in source: {w}"
    for n in re.findall(r"\d+", text):
        if n not in vocab:
            return f"number not in source: {n}"
    judgment = {_hindi_stem(normalise(w)) for w in guard.get("judgment_words", [])}
    hl_counts = Counter(_hindi_stem(w) for s in members for w in set(hindi_words(s.hl)))
    for w in hindi_words(text):
        stem = _hindi_stem(w)
        if stem in judgment and hl_counts[stem] < len(members) / 2:
            return f"judgment word: {w}"
    return None


_MATRAS = "ािीुूृेैोौंँः्"


def _hindi_stem(word: str) -> str:
    """Strip trailing vowel signs so inflections meet: हादसा / हादसे / हादसों -> हादस."""
    return word.rstrip(_MATRAS)


def is_sensitive(members: list[Story], guard: dict) -> str | None:
    """Any sensitive word (or an inflected form of it) in a member headline or synopsis.
    Errs on the cautious side: a false match only means the safer verbatim-headline title."""
    words = [normalise(w).lower() for w in guard.get("sensitive_words", []) if w.strip()]
    for s in members:
        text = normalise(f"{s.hl} {s.syn}").lower()
        hi = hindi_words(text)
        en = set(latin_words(text))
        for w in words:
            if w.isascii():
                if w in en or any(t.startswith(w) for t in en):
                    return w
                continue
            stem = _hindi_stem(w)
            for t in hi:
                if t == w or (len(stem) >= 3 and t.startswith(stem)):
                    return w
    return None


def rules_title(candidate: Candidate, members: list[Story]) -> TitleResult | None:
    """Label from the Hindi word that most member headlines share; None if there is no good one."""
    n = len(members)
    coverage = Counter(w for s in members for w in set(hindi_words(s.hl)) if w not in HINDI_STOP and len(w) >= 3)
    preferred = [t.split(":", 1)[1] for t in candidate.terms if t.startswith("hi:")]
    labels = [w for w in preferred if coverage[w] >= 0.6 * n]
    if not labels:
        labels = [w for w, c in coverage.most_common(3) if c >= 0.6 * n]
    if not labels:
        return None
    title = labels[0]
    if len(labels) > 1:
        a, b = labels[:2]
        # Two words of one name, written next to each other in the headlines: "रितु बराला", not "बराला · रितु".
        adjacent = Counter(f"{x} {y}" for s in members for x, y in zip(hindi_words(s.hl), hindi_words(s.hl)[1:])
                           if {x, y} == {a, b})
        phrase, count = adjacent.most_common(1)[0] if adjacent else ("", 0)
        title = phrase if count >= n / 2 else f"{a} · {b}"
    years = Counter(y for s in members for y in years_in(s.slug.replace("-", " ")))
    if years:
        y, c = years.most_common(1)[0]
        if c >= n / 2 and y not in title:
            title = f"{title} {y}"
    return TitleResult(title=title, subtitle="", source="rules")


def headline_title(members: list[Story], note: str) -> TitleResult:
    lead = _ordered(members)[0]
    return TitleResult(title=lead.hl, subtitle="", source="headline", note=note)


def make_title(candidate: Candidate, members: list[Story], tier: str, llm: LLM | None, guard: dict) -> TitleResult:
    mode = TIER_MODE.get(tier, "headline")
    sensitive = is_sensitive(members, guard)
    if sensitive:
        return headline_title(members, f"sensitive word '{sensitive}'")
    if mode == "headline":
        return headline_title(members, f"{tier} tier")
    fallback = rules_title(candidate, members) or headline_title(members, "no shared label")
    if llm is None:
        return fallback
    system = TITLE_SYSTEM.format(title_max=guard.get("title_max_words", 6),
                                 subtitle_max=guard.get("subtitle_max_words", 16),
                                 mode_rule=MODE_RULES[mode])
    lines = [f"- {s.hl} || {s.syn[:200]}" for s in _ordered(members)]
    try:
        out = llm.complete_json(system, "Stories (headline || synopsis):\n" + "\n".join(lines), max_tokens=300)
    except LLMError as err:
        fallback.note = f"ai error: {err}"
        return fallback
    title = normalise(str(out.get("title", ""))).strip()
    subtitle = normalise(str(out.get("subtitle", ""))).strip()
    problem = check_title(title, subtitle, members, guard)
    if problem:
        fallback.note = f"ai title rejected ({problem}): {title!r}"
        return fallback
    return TitleResult(title=title, subtitle=subtitle, source="ai")
