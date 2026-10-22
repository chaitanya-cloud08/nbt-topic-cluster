"""Configuration loading (config.toml)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

TIER_ORDER = {"soft": 0, "medium": 1, "hard": 2}


class Config:
    def __init__(self, data: dict[str, Any], base_dir: Path):
        self.data = data
        self.base_dir = base_dir
        tm = data.get("tier_map", {})
        self._tier_of: dict[str, str] = {}
        for tier in ("soft", "medium", "hard"):
            for slug in tm.get(tier, []):
                self._tier_of[slug.strip().lower()] = tier
        self._excluded = {s.strip().lower() for s in tm.get("excluded", [])}
        self.default_tier = tm.get("default_tier", "medium")

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).resolve()
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls(data, path.parent)

    def section(self, name: str) -> dict[str, Any]:
        return self.data.get(name, {})

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p

    def tier_for_l1(self, l1_slug: str) -> str | None:
        """Tier for an L1 section slug; None means the section is excluded."""
        key = l1_slug.strip().lower()
        if key in self._excluded:
            return None
        return self._tier_of.get(key, self.default_tier)

    def tier(self, name: str) -> dict[str, Any]:
        return self.data.get("tiers", {}).get(name, {})

    @property
    def disabled_msids(self) -> set[str]:
        return {str(m) for m in self.section("sections").get("disabled_msids", [])}
