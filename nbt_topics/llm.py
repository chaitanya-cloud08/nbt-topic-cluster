"""Minimal LLM clients (no SDK dependency): Anthropic Messages API or any OpenAI-compatible endpoint."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider: str, model: str, api_key: str, base_url: str = "", timeout: float = 30):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        if provider == "anthropic":
            self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        elif provider == "openai":
            self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        else:
            raise ValueError(f"unknown provider {provider}")
        self.calls = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "LLM | None":
        provider = (cfg.get("provider") or "none").lower()
        if provider == "none":
            return None
        key = os.environ.get(cfg.get("api_key_env", ""), "")
        if not key:
            raise LLMError(f"llm.provider is '{provider}' but env var {cfg.get('api_key_env')} is empty")
        return cls(provider, cfg["model"], key, cfg.get("base_url", ""), float(cfg.get("timeout_s", 30)))

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                if err.code in (429, 500, 502, 503, 529) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"HTTP {err.code}: {err.read()[:300]!r}") from err
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(str(err)) from err
        raise LLMError("unreachable")

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        self.calls += 1
        if self.provider == "anthropic":
            out = self._post(f"{self.base_url}/v1/messages", {
                "model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "system": system, "messages": [{"role": "user", "content": user}],
            }, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        out = self._post(f"{self.base_url}/v1/chat/completions", {
            "model": self.model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, {"Authorization": f"Bearer {self.api_key}"})
        return out["choices"][0]["message"]["content"]

    def complete_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        text = self.complete(system, user, max_tokens)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError(f"no JSON in reply: {text[:200]!r}")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as err:
            raise LLMError(f"bad JSON: {err}") from err
