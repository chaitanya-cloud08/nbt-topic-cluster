"""Command line: python -m nbt_topics <command> [options]

  sections   show what was parsed from the taxonomy
  run        one cycle: crawl, cluster, title, write outputs
  daemon     run cycles forever on the configured interval
  serve      start the HTTP API
  list       show stored clusters
  kill ID    hide a cluster (also: unkill ID)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .feed import HttpFeedSource, ReplayFeedSource
from .llm import LLM
from .output import build_outputs
from .pipeline import run_once
from .server import serve
from .store import Store
from .taxonomy import load_taxonomy

log = logging.getLogger("nbt_topics")


def _now(config: Config, value: str | None) -> datetime:
    tz = ZoneInfo(config.section("pool").get("timezone", "Asia/Kolkata"))
    if value:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    return datetime.now(tz)


def _cycle(args, config, taxonomy) -> dict:
    store = Store(config.path(config.section("store").get("path", "state/clusters.db")))
    source = ReplayFeedSource(args.replay) if args.replay else HttpFeedSource(config)
    llm = LLM.from_config(config.section("llm"))
    now = _now(config, args.now)
    only = set(args.only) if args.only else None
    report = run_once(config, taxonomy, source, store, llm, now, only)
    report["outputs"] = build_outputs(store, taxonomy, config, now)
    return report


def _print_report(report: dict) -> None:
    c = report["crawl"]
    print(f"crawl: {c['sections_with_data']}/{c['sections_requested']} sections, {c['pages_fetched']} pages, "
          f"{c['stories_in_window']} stories in window, {len(c['failures'])} sections returned no data")
    for e in report["clusters"]:
        if "stories" in e:
            print(f"  [{e['action']:9}] {e['title']!r}  ({e['stories']} stories, {e['tier']}, {e['status']}, "
                  f"title by {e['title_source']}{', KILLED' if e['killed'] else ''})")
            if e.get("title_note"):
                print(f"              note: {e['title_note']}")
        else:
            print(f"  [{e['action']}] {e['title']!r}")
    for r in report["rejected"]:
        print(f"  [rejected ] {' + '.join(r['terms'])} ({r['stories']} stories): {r['reason']}")
    print(f"llm calls: {report['llm_calls']}; outputs: {report['outputs']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nbt_topics")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--taxonomy", default="data/sections.tsv")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sections")
    for name in ("run", "daemon"):
        sp = sub.add_parser(name)
        sp.add_argument("--replay", help="read saved feed JSON from this folder instead of the network")
        sp.add_argument("--now", help="pretend the current time is this ISO timestamp (testing/replay)")
        sp.add_argument("--only", action="append", help="limit to this section msid (repeatable)")
        sp.add_argument("--json", action="store_true", help="print the full run report as JSON")
    sub.add_parser("serve")
    sp = sub.add_parser("list")
    sp.add_argument("--all", action="store_true", help="include archived clusters")
    for name in ("kill", "unkill"):
        sp = sub.add_parser(name)
        sp.add_argument("cluster_id")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.load(args.config)
    taxonomy = load_taxonomy(config.path(args.taxonomy), config)

    if args.cmd == "sections":
        from collections import Counter
        print(f"{len(taxonomy.sections)} sections will be crawled: {dict(Counter(s.tier for s in taxonomy.sections))}")
        print(f"{len(taxonomy.excluded)} excluded by tier_map, {len(taxonomy.duplicates)} duplicate msids, "
              f"{len(taxonomy.skipped)} rows without a usable msid:")
        for s in taxonomy.skipped:
            print(f"  - {s}")
        return 0

    if args.cmd == "run":
        report = _cycle(args, config, taxonomy)
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str)) if args.json else _print_report(report)
        return 0

    if args.cmd == "daemon":
        interval = float(config.section("daemon").get("interval_minutes", 15)) * 60
        while True:
            started = time.monotonic()
            try:
                _print_report(_cycle(args, config, taxonomy))
            except Exception:  # keep the daemon alive; the last good outputs stay in place
                log.exception("cycle failed")
            time.sleep(max(5.0, interval - (time.monotonic() - started)))

    if args.cmd == "serve":
        logging.getLogger().setLevel(logging.INFO)
        serve(config, taxonomy)
        return 0

    store = Store(config.path(config.section("store").get("path", "state/clusters.db")))
    if args.cmd == "list":
        for c in store.clusters(include_archived=args.all):
            flag = " KILLED" if c.killed else ""
            print(f"{c.id}  {c.status:8} {c.tier:6} {len(c.members):3} stories  score {c.score:5.2f}  "
                  f"{c.title!r}{flag}")
        return 0
    if args.cmd in ("kill", "unkill"):
        if not store.set_killed(args.cluster_id, args.cmd == "kill"):
            print(f"no cluster {args.cluster_id}", file=sys.stderr)
            return 1
        build_outputs(store, taxonomy, config, _now(config, None))
        print(f"{args.cmd}ed {args.cluster_id}; outputs rebuilt")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
