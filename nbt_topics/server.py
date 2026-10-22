"""HTTP API and frontend.

API (read, public, CORS-enabled):
  GET  /topicclusters?scope=home          clusters for the app home
  GET  /topicclusters?scope=sections      sections that currently have clusters
  GET  /topicclusters?msid=<section msid> clusters for a section page
  GET  /topicclusters?story=<story id>    clusters a story belongs to (articleshow module)
  GET  /topicclusters?cluster=<id>        one cluster
  GET  /health
Desk (header X-Admin-Token):
  GET  /admin/clusters                    every live cluster, killed ones included
  POST /admin/kill?id=<cluster id>        hide a cluster now
  POST /admin/unkill?id=<cluster id>
Frontend:
  /ui/                    demo app screens (home, section, article, topic page)
  /ui/desk                desk page with the kill switch
  /ui/topic-carousel.js   the embeddable <nbt-topic-carousel> widget
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .config import Config
from .output import build_outputs, cluster_json
from .store import Store
from .taxonomy import Taxonomy

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_ROUTES = {"/ui/": "index.html", "/ui/index.html": "index.html", "/ui/desk": "desk.html",
                 "/ui/desk.html": "desk.html", "/ui/topic-carousel.js": "topic-carousel.js"}


class FileCache:
    """Reads output JSON, re-reading a file only when the pipeline has rewritten it."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self._cache: dict[Path, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, rel: str) -> dict | None:
        path = self.out_dir / rel
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        with self._lock:
            hit = self._cache.get(path)
            if hit and hit[0] == mtime:
                return hit[1]
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cache[path] = (mtime, data)
            return data


def make_handler(config: Config, taxonomy: Taxonomy, store_path: Path, files: FileCache):
    scfg = config.section("server")
    admin_token = os.environ.get(scfg.get("admin_token_env", "TOPICS_ADMIN_TOKEN"), "")
    cors = scfg.get("cors_origin", "*")
    tz = ZoneInfo(config.section("pool").get("timezone", "Asia/Kolkata"))
    min_n = int(config.section("clustering").get("min_stories", 3))

    class Handler(BaseHTTPRequestHandler):
        server_version = "nbt-topic-clusters/1.0"

        def _headers(self, code: int, ctype: str, length: int, cache: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache)
            if cors:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Access-Control-Allow-Headers", "X-Admin-Token, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def _send(self, code: int, body: dict, cache: str = "public, max-age=60") -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self._headers(code, "application/json; charset=utf-8", len(data), cache)
            self.wfile.write(data)

        def _static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR not in path.parents or not path.is_file():
                return self._send(404, {"error": "not found"})
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._headers(200, ctype, len(data), "public, max-age=300")
            self.wfile.write(data)

        def _admin_ok(self) -> bool:
            if not admin_token or self.headers.get("X-Admin-Token") != admin_token:
                self._send(403, {"error": "admin token missing or wrong"}, cache="no-store")
                return False
            return True

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def do_OPTIONS(self):
            self._headers(204, "text/plain", 0, "max-age=600")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in ("/", "/ui"):
                self.send_response(302)
                self.send_header("Location", "/ui/")
                self.end_headers()
                return
            if url.path in STATIC_ROUTES:
                return self._static(STATIC_ROUTES[url.path])
            if url.path == "/health":
                home = files.get("home.json")
                return self._send(200, {"ok": True, "generatedAt": home.get("generatedAt") if home else None},
                                  cache="no-store")
            if url.path == "/admin/clusters":
                if not self._admin_ok():
                    return
                store = Store(store_path)
                rows = [c for c in store.clusters() if len(c.members) >= min_n]
                stories = store.stories({i for c in rows for i in c.members})
                now = datetime.now(tz)
                out = []
                for c in rows:
                    j = cluster_json(c, stories, config, now)
                    j.update(killed=c.killed, score=round(c.score, 3), titleNote=c.title_note,
                             createdAt=c.created_at, updatedAt=c.updated_at,
                             sections=[{"msid": m, "name": next((s.name_hi for s in taxonomy.sections
                                                                  if s.msid == m), "")} for m in c.sections])
                    out.append(j)
                return self._send(200, {"clusters": out}, cache="no-store")
            if url.path != "/topicclusters":
                return self._send(404, {"error": "not found"})
            empty = {"tn": "topicClusterList", "clusters": []}
            if q.get("scope") == "home":
                return self._send(200, files.get("home.json") or empty)
            if q.get("scope") == "sections":
                return self._send(200, files.get("sections_index.json") or {"sections": []})
            if "msid" in q:
                msid = q["msid"]
                if not msid.isdigit():
                    return self._send(400, {"error": "msid must be numeric"})
                return self._send(200, files.get(f"sections/{msid}.json") or dict(empty, msid=msid))
            all_clusters = (files.get("clusters.json") or {"clusters": {}})["clusters"]
            if "cluster" in q:
                c = all_clusters.get(q["cluster"])
                return self._send(200 if c else 404, c or {"error": "no such cluster"})
            if "story" in q:
                index = files.get("story_index.json") or {"stories": {}}
                ids = index["stories"].get(q["story"], [])
                return self._send(200, {"tn": "topicClusterList", "scope": "story", "story": q["story"],
                                        "clusters": [all_clusters[i] for i in ids if i in all_clusters]})
            return self._send(400, {"error": "use scope=home|sections, msid=, story= or cluster="})

        def do_POST(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path not in ("/admin/kill", "/admin/unkill"):
                return self._send(404, {"error": "not found"})
            if not self._admin_ok():
                return
            store = Store(store_path)  # sqlite connections are per-thread
            ok = store.set_killed(q.get("id", ""), url.path.endswith("/kill"))
            if not ok:
                return self._send(404, {"error": "no such cluster"}, cache="no-store")
            summary = build_outputs(store, taxonomy, config, datetime.now(tz))
            return self._send(200, {"ok": True, "id": q.get("id"), "outputs": summary}, cache="no-store")

    return Handler


def serve(config: Config, taxonomy: Taxonomy) -> None:
    scfg = config.section("server")
    out_dir = config.path(config.section("output").get("dir", "out"))
    store_path = config.path(config.section("store").get("path", "state/clusters.db"))
    handler = make_handler(config, taxonomy, store_path, FileCache(out_dir))
    httpd = ThreadingHTTPServer((scfg.get("host", "0.0.0.0"), int(scfg.get("port", 8080))), handler)
    log.info("serving on %s:%s (frontend at /ui/)", scfg.get("host"), scfg.get("port"))
    httpd.serve_forever()
