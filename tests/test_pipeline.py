"""End-to-end and unit tests on real NBT articlelist feeds of 22 Oct 2026: page 1 of 12 news sections
(tests/fixtures/news, trimmed to the fields the code reads) and the lifestyle section (tests/fixtures/2354729.json)."""
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
NEWS_DIR = FIXTURES / "news"
NEWS = {p.stem for p in NEWS_DIR.glob("*.json")}
TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 22, 14, 40, tzinfo=TZ)
LIFESTYLE = "2354729"
JAIPUR = "5194923"
TIGER = {"135074223", "135074822", "135079133", "135081187", "135082807", "135086176", "135088812", "135095829",
         "135103045", "135106276", "135108888", "135111118"}          # Tiger Shroff firing case
BARALA = {"135059789", "135064847", "135067086", "135083543", "135086687", "135088103", "135095509", "135096235"}
AZAM = {"135053897", "135068285", "135091954", "135109448", "135109843", "135112091", "135113575"}
SABARMATI = {"135059490", "135103045", "135108634"}                    # Lawrence Bishnoi in Sabarmati jail
DOCTOR = {"135107855", "135086995", "135085207", "135082360"}          # lifestyle stories that only share "डॉक्टर"
SOFT = {"tier_map.hard": [], "tier_map.medium": [], "tier_map.default_tier": "soft"}


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
        self.title = title or {"title": "रितु बराला केस", "subtitle": "जयपुर कमिश्नरेट में कांग्रेस का धरना"}
        self.prompts: list[str] = []

    def complete_json(self, system: str, user: str, max_tokens: int = 600) -> dict:
        self.calls += 1
        self.prompts.append(system + "\n" + user)
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


def run(env, llm=None, now=NOW, source=None, only=NEWS):
    config, taxonomy, store, _ = env
    report = run_once(config, taxonomy, source or ReplayFeedSource(NEWS_DIR), store, llm, now, only)
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

def test_real_feed_gives_one_cluster_per_story(env):
    report = run(env)
    _, _, store, tmp = env
    clusters = {frozenset(c.members): c for c in store.clusters()}
    for topic in (TIGER, BARALA, AZAM, SABARMATI):
        assert frozenset(topic) in clusters
    for c in clusters.values():          # every topic is a name written in the headline and the URL alike
        assert any(t.startswith("hi:") for t in c.terms) and any(t.startswith("en:") for t in c.terms)
    tiger = clusters[frozenset(TIGER)]
    assert tiger.score == max(c.score for c in clusters.values())
    assert tiger.title.startswith("टाइगर श्रॉफ पर फायरिंग केस का कानपुर कनेक्शन")   # hard tier: newest headline
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert home["clusters"][0]["count"] == 12 and len(home["clusters"]) == 5
    item = home["clusters"][0]["items"][0]
    assert item["id"] == "135111118"
    assert item["wu"].endswith("/kanpur/kanpur-connection-in-firing-case-involving-tiger-shroff/articleshow/135111118.cms")
    index = json.loads((tmp / "out" / "story_index.json").read_text(encoding="utf-8"))["stories"]
    assert len(index["135103045"]) == 2             # "सलमान खान, बाबा सिद्दीकी, अब टाइगर श्रॉफ... साबरमती जेल से"
    assert (tmp / "out" / "sections" / f"{JAIPUR}.json").exists()
    assert report["llm_calls"] == 0


def test_one_shared_word_is_not_a_topic(env):
    """The four lifestyle stories that only share "डॉक्टर" (a fertility test, urine leakage after delivery, an
    unmarried girl's pregnancy, fasting in pregnancy) used to be a cluster. So did "सिंह", "कोर्ट", "जिंदगी"."""
    report = run(env, now=datetime(2026, 10, 22, 12, 10, tzinfo=TZ), source=ReplayFeedSource(FIXTURES), only={LIFESTYLE})
    _, _, store, _ = env
    assert report["crawl"]["stories_in_window"] == 20
    assert not any(c.members & DOCTOR for c in store.clusters())


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
    tiger_id = next(c.id for c in store.clusters() if c.members == TIGER)
    replay = tmp_path / "replay2"
    replay.mkdir()
    for msid in NEWS:
        (replay / f"{msid}.json").write_bytes((NEWS_DIR / f"{msid}.json").read_bytes())
    feed = json.loads((replay / "93273647.json").read_text(encoding="utf-8"))   # crime
    new = copy.deepcopy(next(i for i in feed["items"] if i["id"] == "135106276"))
    new.update(id="135199999", hl="टाइगर श्रॉफ फायरिंग केस: लॉरेंस बिश्नोई गैंग के शूटर की बहन मुंबई पुलिस की हिरासत में",
               seolocation="crime/news/tiger-shroff-firing-case-lawrence-bishnoi-gang-shooter-sister-detained",
               dl="Oct 22, 2026, 03:05 PM", lu="Oct 22, 2026, 03:05 PM")
    feed["items"].insert(0, new)
    (replay / "93273647.json").write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    report = run(env, now=NOW + timedelta(hours=1), source=ReplayFeedSource(replay))
    tiger = store.cluster(tiger_id)
    assert tiger.members == TIGER | {"135199999"}
    assert any(e["cluster"] == tiger_id and e["action"] == "updated" for e in report["clusters"])


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
    tiger_id = next(c.id for c in store.clusters() if c.members == TIGER)
    store.set_killed(tiger_id, True)
    build_outputs(store, taxonomy, config, NOW)
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert tiger_id not in [c["clusterId"] for c in home["clusters"]]
    run(env, now=NOW + timedelta(minutes=15))        # stays killed through later runs
    assert store.cluster(tiger_id).killed
    home = json.loads((tmp / "out" / "home.json").read_text(encoding="utf-8"))
    assert tiger_id not in [c["clusterId"] for c in home["clusters"]]


def test_disabled_section_is_not_crawled(tmp_path):
    config = make_config(tmp_path, **{"sections.disabled_msids": [LIFESTYLE]})
    taxonomy = load_taxonomy(ROOT / "data" / "sections.tsv", config)
    report = run((config, taxonomy, Store(tmp_path / "c.db"), tmp_path), source=ReplayFeedSource(FIXTURES),
                 only={LIFESTYLE})
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

@pytest.fixture
def soft_env(tmp_path):
    """Every section soft, so the AI may write titles (on hard news it never does)."""
    config = make_config(tmp_path, **SOFT)
    return config, load_taxonomy(ROOT / "data" / "sections.tsv", config), Store(tmp_path / "clusters.db"), tmp_path


def test_ai_verify_and_title(soft_env):
    llm = FakeLLM(verify_drop={"135095509"})        # "राहुल गांधी की 'बुद्धि' पर ही उठा दिए सवाल"
    run(soft_env, llm=llm)
    _, _, store, _ = soft_env
    by_members = {frozenset(c.members): c for c in store.clusters()}
    barala = by_members[frozenset(BARALA - {"135095509"})]    # the model removed one story
    assert barala.title_source == "ai" and barala.title == "रितु बराला केस"
    assert barala.subtitle == "जयपुर कमिश्नरेट में कांग्रेस का धरना"
    tiger = by_members[frozenset(TIGER)]
    assert tiger.title_source == "headline" and "sensitive word" in tiger.title_note   # a murder accused is a member


def test_ai_title_with_invented_word_falls_back(soft_env):
    llm = FakeLLM(title={"title": "रितु बराला महासंग्राम", "subtitle": ""})
    run(soft_env, llm=llm)
    _, _, store, _ = soft_env
    barala = next(c for c in store.clusters() if c.members == BARALA)
    assert barala.title_source == "rules" and barala.title == "रितु बराला"
    assert "word not in source" in barala.title_note


def test_rules_titles_without_ai(soft_env):
    run(soft_env)
    _, _, store, _ = soft_env
    titles = {frozenset(c.members): c.title for c in store.clusters()}
    assert titles[frozenset(BARALA)] == "रितु बराला"         # one name, as the headlines write it
    assert titles[frozenset(AZAM)] == "अखिलेश · आजम"         # two names


def test_ai_is_not_called_again_for_unchanged_clusters(soft_env):
    llm = FakeLLM()
    run(soft_env, llm=llm)
    calls = llm.calls
    run(soft_env, llm=llm, now=NOW + timedelta(minutes=15))
    assert llm.calls == calls


def test_hard_tier_uses_headline_verbatim(env):
    llm = FakeLLM()
    run(env, llm=llm)
    _, _, store, _ = env
    barala = next(c for c in store.clusters() if c.members == BARALA)
    assert barala.title_source == "headline"
    assert barala.title.startswith("'परिवाद और FIR में फर्क है', जयपुर कमिश्नर की ना के बाद रितु बराला")
    assert not any("You write the label" in p and "रितु बराला" in p for p in llm.prompts)   # AI never wrote hard-news copy


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
        assert [c["count"] for c in home["clusters"]] == [12, 7, 6, 3, 4]
        assert home["clusters"][0]["title"].startswith("टाइगर श्रॉफ पर फायरिंग केस")
        assert [c["count"] for c in get(f"/topicclusters?msid={JAIPUR}")["clusters"]] == [8]
        assert get("/topicclusters?msid=999")["clusters"] == []
        assert len(get("/topicclusters?story=135103045")["clusters"]) == 2
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

    feeds = {msid: (NEWS_DIR / f"{msid}.json").read_bytes() for msid in NEWS}
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
            msid = q.get("msid", [""])[0]
            if msid in feeds and "curpg" not in q:
                return self._json(feeds[msid])
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
                text = 'Here you go: {"title": "रितु बराला केस", "subtitle": "जयपुर कमिश्नरेट में कांग्रेस का धरना"}'
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
    cfg_text = cfg_text.replace('default_tier = "medium"', 'default_tier = "soft"')     # so the AI writes titles
    cfg_text = "\n".join("medium = []" if l.startswith("medium = [") else "hard = []" if l.startswith("hard = [")
                         else l for l in cfg_text.splitlines())
    (tmp_path / "config.toml").write_text(cfg_text, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sections.tsv").write_bytes((ROOT / "data" / "sections.tsv").read_bytes())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    try:
        only = [arg for msid in sorted(NEWS) for arg in ("--only", msid)]
        assert main(["--config", str(tmp_path / "config.toml"), "run", "--now", "2026-10-22T14:40", *only]) == 0
    finally:
        httpd.shutdown()
    assert any(q.get("client") == ["nbt"] and q.get("msid") == [JAIPUR] for q in seen["feed"])
    assert all(b["model"] == "claude-haiku-5-5" and b["temperature"] == 0 for b in seen["llm"])
    home = json.loads((tmp_path / "out" / "home.json").read_text(encoding="utf-8"))
    barala = next(c for c in home["clusters"] if c["count"] == 8)
    assert barala["titleSource"] == "ai" and barala["subtitle"] == "जयपुर कमिश्नरेट में कांग्रेस का धरना"
    assert (tmp_path / "state" / "feed_cache" / f"{JAIPUR}_p1.json").exists()


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
        assert item["img"].startswith("https://static.langimg.com/thumb/msid-135112668,imgsize-102141")
        assert item["published"].startswith("2026-10-22T12:44") and item["syn"]
        with urllib.request.urlopen(base + "/topicclusters?scope=sections") as r:
            sections = {s["msid"]: s for s in json.loads(r.read())["sections"]}
        assert sections[JAIPUR] == {"msid": JAIPUR, "sectionName": "जयपुर", "level": 3, "count": 1}
        cid = home["clusters"][0]["clusterId"]
        with urllib.request.urlopen(f"{base}/topicclusters?cluster={cid}") as r:
            assert json.loads(r.read())["clusterId"] == cid
        req = urllib.request.Request(base + "/admin/clusters", headers={"X-Admin-Token": "secret"})
        with urllib.request.urlopen(req) as r:
            desk = json.loads(r.read())["clusters"]
        assert {c["killed"] for c in desk} == {False} and len(desk) == len(store.clusters())
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/admin/clusters")             # no token
    finally:
        httpd.shutdown()
