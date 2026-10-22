"""End-to-end and unit tests. The feed fixture is the real NBT lifestyle articlelist (msid 2354729, 22 Oct 2026)."""
from __future__ import annotations

import copy
import json
import threading
import tomllib
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nbt_topics.config import Config
from nbt_topics.editorial import check_title, make_title
from nbt_topics.feed import ReplayFeedSource, crawl
from nbt_topics.models import parse_feed_time
from nbt_topics.output import build_outputs
from nbt_topics.pipeline import run_once
from nbt_topics.store import Store
from nbt_topics.taxonomy import load_taxonomy

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 22, 12, 10, tzinfo=TZ)
LIFESTYLE = "2354729"
KARWA = {"135108978", "135086995", "135084040"}
DOCTOR = {"135107855", "135086995", "135085207", "135082360"}


def make_config(tmp: Path, **overrides) -> Config:
    data = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data["store"]["path"] = str(tmp / "clusters.db")
    data["output"]["dir"] = str(tmp / "out")
    data["feed"]["cache_dir"] = str(tmp / "cache")
    for dotted, value in overrides.items():
        node = data
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return Config(data, tmp)


class FakeLLM:
    """Stands in for the model: replies are scripted per call type, calls are counted."""

    def __init__(self, verify_drop: set[str] | None = None, title: dict | None = None):
        self.calls = 0
        self.verify_drop = verify_drop or set()
        self.title = title or {"title": "करवाचौथ 2026", "subtitle": "पायल और आलता के डिजाइन, प्रेग्नेंसी में व्रत पर डॉक्टर की राय"}
        self.prompts: list[str] = []

    def complete_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        self.calls += 1
        self.prompts.append(system)
        if "check story groupings" in system:
            ids = [line.split(" | ")[0] for line in user.splitlines() if " | " in line]
            return {"keep": [i for i in ids if i not in self.verify_drop], "reason": "same topic"}
        return self.title


@pytest.fixture
def env(tmp_path):
    config = make_config(tmp_path)
    taxonomy = load_taxonomy(ROOT / "data" / "sections.tsv", config)
    store = Store(tmp_path / "clusters.db")
    return config, taxonomy, store, tmp_path


def run(env, llm=None, now=NOW, source=None):
    config, taxonomy, store, _ = env
    report = run_once(config, taxonomy, source or ReplayFeedSource(FIXTURES), store, llm, now, {LIFESTYLE})
    report["outputs"] = build_outputs(store, taxonomy, config, now)
    return report


# ---------- taxonomy & parsing ----------

def test_taxonomy_parses_real_file(env):
    _, taxonomy, _, _ = env
    by = taxonomy.by_msid()
    assert len(taxonomy.sections) == 384
    assert len(taxonomy.skipped) == 16
    assert by[LIFESTYLE].tier == "soft" and by[LIFESTYLE].level == 1
    assert by["5647266"].l1 == "lifestyle" and by["5647266"].l2 == "family"
    assert by["1564454"].tier == "hard"
    assert "2279793" in by and by["2279793"].tier == "medium"          # entertainment
    assert "12545518" not in by                                          # jokes excluded
    assert taxonomy.l1_msid(by["5647266"]) == LIFESTYLE


def test_feed_time_parsing():
    assert parse_feed_time("Oct 22, 2026, 11:33 AM", TZ) == datetime(2026, 10, 22, 11, 33, tzinfo=TZ)
    assert parse_feed_time("1792563878000", TZ) is not None
    assert parse_feed_time("garbage", TZ) is None


# ---------- clustering on the real feed ----------

def test_real_feed_gives_karwachauth_and_doctor_clusters(env):
    report = run(env)
    _, _, store, tmp = env
    clusters = {frozenset(c.members): c for c in store.clusters()}
    assert frozenset(KARWA) in clusters
    assert frozenset(DOCTOR) in clusters
    assert len(clusters) == 2                       # recipes / relationship are sections, not topics
    karwa = clusters[frozenset(KARWA)]
    assert karwa.title == "करवाचौथ 2026" and karwa.title_source == "rules"
    assert karwa.score > clusters[frozenset(DOCTOR)].score   # fresher + spans two sub-sections
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert [c["count"] for c in home["clusters"]] == [3, 4]
    item = home["clusters"][0]["items"][0]
    assert item["id"] == "135108978" and item["isNew"] is True
    assert item["wu"].endswith("/lifestyle/fashion/trendy-and-latest-silver-anklet-design-ideas-for-karwachauth-2026-chandi-ki-payal/articleshow/135108978.cms")
    index = json.loads((tmp / "out" / "story_index.json").read_text(encoding="utf-8"))["stories"]
    assert len(index["135086995"]) == 2             # the pregnancy story is in both topics
    assert (tmp / "out" / "sections" / f"{LIFESTYLE}.json").exists()
    assert report["llm_calls"] == 0


def test_second_run_is_stable(env):
    run(env)
    _, _, store, _ = env
    first = {c.id: c.title for c in store.clusters()}
    report = run(env, now=NOW + timedelta(minutes=15))
    assert {c.id: c.title for c in store.clusters()} == first
    assert {e["action"] for e in report["clusters"]} == {"unchanged"}


def test_new_story_joins_existing_cluster(env, tmp_path):
    run(env)
    _, _, store, _ = env
    karwa_id = next(c.id for c in store.clusters() if c.members == KARWA)
    feed = json.loads((FIXTURES / f"{LIFESTYLE}.json").read_text(encoding="utf-8"))
    new = copy.deepcopy(next(i for i in feed["items"] if i["id"] == "135084040"))
    new.update(id="135199999", hl="करवाचौथ पर मेहंदी के आसान डिजाइन, हाथों में लगेंगे चार चांद",
               seolocation="lifestyle/fashion/easy-mehndi-design-for-karwachauth-2026",
               dl="Oct 22, 2026, 01:05 PM", lu="Oct 22, 2026, 01:05 PM")
    feed["items"].insert(0, new)
    replay = tmp_path / "replay2"
    replay.mkdir()
    (replay / f"{LIFESTYLE}.json").write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    report = run(env, now=NOW + timedelta(hours=1), source=ReplayFeedSource(replay))
    karwa = store.cluster(karwa_id)
    assert "135199999" in karwa.members and len(karwa.members) == 4
    assert any(e["cluster"] == karwa_id and e["action"] == "updated" for e in report["clusters"])


def test_lifecycle_archives_old_clusters(env):
    run(env)
    _, _, store, tmp = env
    run(env, now=NOW + timedelta(days=5))
    assert store.clusters() == []                    # all archived
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert home["clusters"] == []


def test_kill_switch(env):
    run(env)
    config, taxonomy, store, tmp = env
    karwa_id = next(c.id for c in store.clusters() if c.members == KARWA)
    store.set_killed(karwa_id, True)
    build_outputs(store, taxonomy, config, NOW)
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert karwa_id not in [c["clusterId"] for c in home["clusters"]]
    run(env, now=NOW + timedelta(minutes=15))        # stays killed through later runs
    assert store.cluster(karwa_id).killed
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert karwa_id not in [c["clusterId"] for c in home["clusters"]]


def test_disabled_section_is_not_crawled(tmp_path):
    config = make_config(tmp_path, **{"sections.disabled_msids": [LIFESTYLE]})
    taxonomy = load_taxonomy(ROOT / "data" / "sections.tsv", config)
    report = run((config, taxonomy, Store(tmp_path / "c.db"), tmp_path))
    assert report["crawl"]["stories_in_window"] == 0 and report["clusters"] == []


def test_paging_ignored_is_detected(env, tmp_path):
    config, taxonomy, _, _ = env
    replay = tmp_path / "paged"
    replay.mkdir()
    body = (FIXTURES / f"{LIFESTYLE}.json").read_text(encoding="utf-8")
    data = json.loads(body)
    data["pg"]["tp"] = "3"
    data["items"] = [dict(i, dl="Oct 22, 2026, 11:00 AM") for i in data["items"]]  # keep page "in window"
    for page in (1, 2):
        (replay / f"{LIFESTYLE}_p{page}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    secs = [s for s in taxonomy.sections if s.msid == LIFESTYLE]
    _, report = crawl(secs, ReplayFeedSource(replay), config, NOW, {LIFESTYLE: "lifestyle"})
    assert report.paging_ignored == [LIFESTYLE]


# ---------- AI path and guardrails ----------

def test_ai_verify_and_title(env):
    llm = FakeLLM(verify_drop={"135082360"})
    run(env, llm=llm)
    _, _, store, _ = env
    by_members = {frozenset(c.members): c for c in store.clusters()}
    assert frozenset(DOCTOR - {"135082360"}) in by_members    # the model removed one story
    karwa = by_members[frozenset(KARWA)]
    assert karwa.title_source == "ai"
    assert karwa.subtitle.startswith("पायल और आलता")


def test_ai_title_with_invented_word_falls_back(env):
    llm = FakeLLM(title={"title": "करवाचौथ का महा उत्सव", "subtitle": ""})
    run(env, llm=llm)
    _, _, store, _ = env
    karwa = next(c for c in store.clusters() if c.members == KARWA)
    assert karwa.title_source == "rules" and karwa.title == "करवाचौथ 2026"
    assert "word not in source" in karwa.title_note


def test_ai_is_not_called_again_for_unchanged_clusters(env):
    llm = FakeLLM()
    run(env, llm=llm)
    calls = llm.calls
    run(env, llm=llm, now=NOW + timedelta(minutes=15))
    assert llm.calls == calls


def test_hard_tier_uses_headline_verbatim(tmp_path):
    config = make_config(tmp_path, **{"tier_map.soft": [], "tier_map.hard": ["lifestyle"]})
    taxonomy = load_taxonomy(ROOT / "data" / "sections.tsv", config)
    store = Store(tmp_path / "c.db")
    llm = FakeLLM()
    run((config, taxonomy, store, tmp_path), llm=llm)
    karwa = next(c for c in store.clusters() if c.members == KARWA)
    assert karwa.title_source == "headline"
    assert karwa.title.startswith("10- 15 ग्राम चांदी में बनेंगी ट्रेंडी पायल")
    assert not any("You write the label" in p for p in llm.prompts)   # AI never wrote hard-news copy


def test_check_title_rules():
    from nbt_topics.models import Story
    guard = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))["guardrails"]
    s = Story(id="1", hl="करवाचौथ पर पायल के डिजाइन", syn="करवाचौथ की शॉपिंग", seolocation="x/karwachauth-2026",
              subsecname="", subsecmsid="", imageid="", imgsize="", cmstype="", published=NOW, updated=NOW,
              lu_text="")
    assert check_title("करवाचौथ 2026", "पायल के डिजाइन", [s], guard) is None
    assert "not in source" in check_title("करवाचौथ धमाल", "", [s], guard)
    assert "number" in check_title("करवाचौथ 2027", "", [s], guard)
    assert "length" in check_title("करवाचौथ पर पायल के डिजाइन करवाचौथ पायल", "", [s], guard)
    assert check_title("करवाचौथ 🎉", "", [s], guard) is not None
    s2 = Story(**{**s.__dict__, "hl": "करवाचौथ पर हमला"})
    s3 = Story(**{**s.__dict__, "hl": "करवाचौथ पर पायल"})
    s4 = Story(**{**s.__dict__, "hl": "करवाचौथ पर डिजाइन"})
    assert "judgment" in check_title("करवाचौथ हमला", "", [s2, s3, s4], guard)
    s5 = Story(**{**s.__dict__, "hl": "करवाचौथ पर हमले"})
    assert "judgment" in check_title("करवाचौथ हमले", "", [s5, s3, s4], guard)   # inflected form


def test_sensitive_story_forces_headline(env):
    from nbt_topics.candidates import Candidate
    from nbt_topics.models import Story
    guard = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))["guardrails"]
    base = dict(syn="", seolocation="india/x", subsecname="", subsecmsid="", imageid="", imgsize="", cmstype="",
                updated=NOW, lu_text="")
    stories = [Story(id=str(i), hl=h, published=NOW - timedelta(hours=i), **base) for i, h in
               enumerate(["कानपुर हादसे के बाद जांच तेज", "कानपुर में राहत कार्य", "कानपुर पर बैठक"])]
    t = make_title(Candidate(terms=["hi:कानपुर"], members={"0", "1", "2"}), stories, "soft", FakeLLM(), guard)
    assert t.source == "headline" and t.title == "कानपुर हादसे के बाद जांच तेज"


# ---------- HTTP API ----------

def test_http_api(env, monkeypatch):
    from http.server import ThreadingHTTPServer
    from nbt_topics.server import FileCache, make_handler
    run(env)
    config, taxonomy, store, tmp = env
    monkeypatch.setenv("TOPICS_ADMIN_TOKEN", "secret")
    handler = make_handler(config, taxonomy, tmp / "clusters.db", FileCache(tmp / "out"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return json.loads(r.read())

    try:
        home = get("/topicclusters?scope=home")
        assert [c["title"] for c in home["clusters"]] == ["करवाचौथ 2026", "डॉक्टर"]
        assert len(get(f"/topicclusters?msid={LIFESTYLE}")["clusters"]) == 2
        assert get("/topicclusters?msid=999")["clusters"] == []
        assert len(get("/topicclusters?story=135086995")["clusters"]) == 2
        kid = home["clusters"][0]["clusterId"]
        req = urllib.request.Request(f"{base}/admin/kill?id={kid}", method="POST")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req)                     # no token
        req.add_header("X-Admin-Token", "secret")
        urllib.request.urlopen(req)
        assert kid not in [c["clusterId"] for c in get("/topicclusters?scope=home")["clusters"]]
        assert get("/health")["ok"] is True
    finally:
        httpd.shutdown()


# ---------- real network clients against a local stand-in server ----------

def test_http_feed_and_anthropic_client_end_to_end(tmp_path, monkeypatch):
    """Runs the CLI exactly as in production, with feed and LLM base URLs pointed at a local server
    that speaks the NBT articlelist and Anthropic Messages formats."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    from nbt_topics.__main__ import main

    feed_body = (FIXTURES / f"{LIFESTYLE}.json").read_bytes()
    seen = {"feed": [], "llm": []}

    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            seen["feed"].append(q)
            if q.get("msid") == [LIFESTYLE] and "curpg" not in q:
                return self._json(feed_body)
            return self._json({"error": "no"}, 404)

        def do_POST(self):
            assert self.path == "/v1/messages"
            assert self.headers["x-api-key"] == "test-key"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["llm"].append(body)
            if "check story groupings" in body["system"]:
                ids = [l.split(" | ")[0] for l in body["messages"][0]["content"].splitlines() if " | " in l]
                text = json.dumps({"keep": ids, "reason": "one topic"})
            else:
                text = 'Here you go: {"title": "करवाचौथ 2026", "subtitle": "पायल और आलता के डिजाइन"}'
            return self._json({"content": [{"type": "text", "text": text}]})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    cfg_text = (ROOT / "config.toml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace('base_url = "https://global-feed.indiatimes.com/wufs/feed/list/article"',
                                f'base_url = "{base}/wufs/feed/list/article"')
    cfg_text = cfg_text.replace('provider = "none"', 'provider = "anthropic"')
    cfg_text = cfg_text.replace('base_url = ""            # empty = provider default', f'base_url = "{base}"')
    cfg_text = cfg_text.replace('concurrency = 8', 'concurrency = 4').replace('retries = 2', 'retries = 0')
    (tmp_path / "config.toml").write_text(cfg_text, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sections.tsv").write_bytes((ROOT / "data" / "sections.tsv").read_bytes())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    try:
        assert main(["--config", str(tmp_path / "config.toml"), "run", "--now", "2026-10-22T12:10",
                     "--only", LIFESTYLE, "--only", "5647266"]) == 0
    finally:
        httpd.shutdown()
    assert any(q.get("client") == ["nbt"] and q.get("msid") == [LIFESTYLE] for q in seen["feed"])
    assert all(b["model"] == "claude-haiku-5-5" and b["temperature"] == 0 for b in seen["llm"])
    home = json.loads((tmp_path / "out" / "home.json").read_text(encoding="utf-8"))
    karwa = next(c for c in home["clusters"] if c["count"] == 3)
    assert karwa["titleSource"] == "ai" and karwa["subtitle"] == "पायल और आलता के डिजाइन"
    assert (tmp_path / "state" / "feed_cache" / f"{LIFESTYLE}_p1.json").exists()


# ---------- frontend endpoints ----------

def test_frontend_endpoints(env, monkeypatch):
    from http.server import ThreadingHTTPServer
    from nbt_topics.server import FileCache, make_handler
    run(env)
    config, taxonomy, store, tmp = env
    monkeypatch.setenv("TOPICS_ADMIN_TOKEN", "secret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, taxonomy, tmp / "clusters.db", FileCache(tmp / "out")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for path, ctype in (("/ui/", "text/html"), ("/ui/desk", "text/html"), ("/ui/topic-carousel.js", "javascript")):
            with urllib.request.urlopen(base + path) as r:
                assert ctype in r.headers["Content-Type"] and r.read()
        with urllib.request.urlopen(base + "/ui/../config.py") as r:   # no path traversal out of static/
            pass
    except urllib.error.HTTPError as err:
        assert err.code == 404
    try:
        with urllib.request.urlopen(base + "/topicclusters?scope=home") as r:
            assert r.headers["Access-Control-Allow-Origin"] == "*"
            home = json.loads(r.read())
        item = home["clusters"][0]["items"][0]
        assert item["img"].startswith("https://static.langimg.com/thumb/msid-135108978,imgsize-1382247")
        assert item["published"].startswith("2026-10-22T11:33") and item["syn"]
        with urllib.request.urlopen(base + "/topicclusters?scope=sections") as r:
            assert json.loads(r.read())["sections"] == [{"msid": LIFESTYLE, "sectionName": "लाइफस्टाइल", "level": 1, "count": 2}]
        cid = home["clusters"][0]["clusterId"]
        with urllib.request.urlopen(f"{base}/topicclusters?cluster={cid}") as r:
            assert json.loads(r.read())["clusterId"] == cid
        req = urllib.request.Request(base + "/admin/clusters", headers={"X-Admin-Token": "secret"})
        with urllib.request.urlopen(req) as r:
            desk = json.loads(r.read())["clusters"]
        assert {c["killed"] for c in desk} == {False} and desk[0]["sections"][0]["name"] == "लाइफस्टाइल"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/admin/clusters")             # no token
    finally:
        httpd.shutdown()
