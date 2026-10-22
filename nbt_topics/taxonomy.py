"""Parse the NBT section taxonomy (TSV with L1 / L2 / L3 / Regional Name / MSID columns)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config


@dataclass
class Section:
    msid: str
    l1: str
    l2: str
    l3: str
    name_hi: str
    tier: str            # soft / medium / hard

    @property
    def level(self) -> int:
        return 3 if self.l3 else 2 if self.l2 else 1

    @property
    def path(self) -> str:
        return "/".join(p for p in (self.l1, self.l2, self.l3) if p)


@dataclass
class Taxonomy:
    sections: list[Section]
    skipped: list[str] = field(default_factory=list)    # rows without a usable msid
    duplicates: list[str] = field(default_factory=list)  # msids listed more than once (first kept)
    excluded: list[str] = field(default_factory=list)    # sections in excluded L1s

    def by_msid(self) -> dict[str, Section]:
        return {s.msid: s for s in self.sections}

    def l1_msid(self, section: Section) -> str | None:
        for s in self.sections:
            if s.level == 1 and s.l1 == section.l1:
                return s.msid
        return None

    def section_words(self, geo_l1: tuple[str, ...] = ("state", "metro", "world")) -> set[str]:
        """Slug tokens and Hindi name words of category sections, used as stop-words: a topic named after a
        section ("recipe", "relationship") is not a topic. Geographic sections (states, cities, countries) are
        kept out, so "delhi" or "बिहार" can still be part of a topic."""
        words: set[str] = set()
        for s in self.sections:
            if s.l1.lower() in geo_l1:
                continue
            for part in (s.l1, s.l2, s.l3):
                for tok in part.lower().replace("_", "-").replace(" ", "-").split("-"):
                    if tok:
                        words.add(tok)
            for tok in s.name_hi.replace("&", " ").replace("-", " ").split():
                words.add(tok)
        return words


def _clean(v: str) -> str:
    return v.strip()


def load_taxonomy(path: str | Path, config: Config) -> Taxonomy:
    rows = list(csv.reader(open(path, encoding="utf-8", errors="replace"), delimiter="\t"))
    tax = Taxonomy(sections=[])
    seen: set[str] = set()
    cur_l1 = cur_l2 = ""
    for row in rows:
        row = (row + [""] * 5)[:6]
        if not any(c.strip() for c in row):
            continue
        l1, l2, l3, name_hi, msid = (_clean(c) for c in row[:5])
        if l1 == "L1" and msid.upper() == "MSID":
            continue  # header
        # A row's L1/L2 cells set the context for the rows below it.
        if l1:
            cur_l1, cur_l2 = l1, l2
        elif l2:
            cur_l2 = l2
        if l1:
            s_l1, s_l2, s_l3 = l1, l2, l3
        elif l2:
            s_l1, s_l2, s_l3 = cur_l1, l2, l3
        else:
            s_l1, s_l2, s_l3 = cur_l1, cur_l2, l3
        label = "/".join(p for p in (s_l1, s_l2, s_l3) if p) or name_hi
        if not msid.isdigit():
            tax.skipped.append(f"{label} ({name_hi}): msid '{msid or 'blank'}'")
            continue
        if msid in seen:
            tax.duplicates.append(f"{msid} ({label})")
            continue
        seen.add(msid)
        tier = config.tier_for_l1(s_l1)
        if tier is None:
            tax.excluded.append(f"{msid} ({label})")
            continue
        tax.sections.append(Section(msid=msid, l1=s_l1, l2=s_l2, l3=s_l3, name_hi=name_hi, tier=tier))
    return tax
