# NBT topic clusters

Builds topic carousels for the Navbharat Times app with zero editorial work. Every cycle it:

1. **Crawls** the articlelist feed of every section in `data/sections.tsv` (384 sections after skipping
   excluded formats and rows without an msid) and merges them into one de-duplicated pool of the last 72 hours.
2. **Finds topics**: terms shared by several stories, from the English URL slug and the Hindi headline
   (`karwachauth` + `करवाचौथ`, `doctor` + `डॉक्टर`). Broad terms (`delhi`) only count in pairs (`delhi + protest`).
   Topics named after a section (`recipe`, `relationship`) are not topics and are dropped.
3. **Verifies** each topic's stories with an LLM (optional), which removes stories that only share a word.
4. **Matches** each topic to yesterday's clusters, so cluster ids and titles stay stable.
5. **Titles** it under the guardrails below.
6. **Ages** clusters: active, then cooling, then archived, per tier.
7. **Writes** JSON for the app home, every section page and every article, and serves it over HTTP.

Python 3.11+, standard library only (pytest for tests).

## Quick start

```bash
python -m nbt_topics sections                 # what was parsed from the taxonomy
python -m nbt_topics run                      # one live cycle (needs access to global-feed.indiatimes.com)
python -m nbt_topics serve                    # API on :8080
```

Try it offline on the real lifestyle feed saved in `tests/fixtures`:

```bash
python -m nbt_topics run --replay tests/fixtures --now 2026-10-22T12:10
```

```
crawl: 1/384 sections, 1 pages, 20 stories in window, 383 sections returned no data
  [created  ] 'करवाचौथ 2026'  (3 stories, soft, active, title by rules)
  [created  ] 'डॉक्टर'  (4 stories, soft, active, title by rules)
```

## Turning on the AI steps

In `config.toml`:

```toml
[llm]
provider = "anthropic"          # or "openai" for any OpenAI-compatible endpoint (set base_url)
model = "claude-haiku-5-5"
api_key_env = "ANTHROPIC_API_KEY"
```

Without a key (`provider = "none"`) everything still runs: membership is kept as found and titles come from
the Hindi word most member headlines share. With the LLM on, titles read better ("करवाचौथ 2026" plus a subtitle
like "पायल और आलता के डिजाइन…") and loose matches get removed.

**Cost** scales with what you publish, not with traffic: one verify call when a topic gets new stories, and
one title call only when a cluster is new or has grown by 30%. Unchanged clusters cost nothing.

## Guardrails

| Tier | Sections (see `[tier_map]`) | Title |
|---|---|---|
| soft | lifestyle, recipes, health, travel, auto, tech… | AI title from headline words |
| medium | entertainment, TV, sports, business | AI title, names only |
| hard | India, State and all cities, metro, world, crime, legal, opinion | latest headline, verbatim |

A cluster takes the strictest tier of any of its stories. On top of that:

- **Sensitive stories** (death, rape, riots, terror, accidents… with Hindi inflections) force the verbatim
  headline whatever the tier. The AI never writes copy for them.
- **Every AI title is checked**: every word, name and number must already appear in the member headlines or
  synopses; judgment words (हमला, बेनकाब, पलटवार…) are refused unless most headlines use them; length, quotes and
  emoji are checked. A failed title falls back to the rule-based one, and the reason is logged.
- **Kill switch**: `python -m nbt_topics kill <cluster_id>` or `POST /admin/kill?id=…` with header
  `X-Admin-Token` (from env `TOPICS_ADMIN_TOKEN`). A killed cluster stays hidden through later runs.
- **Section off switch**: add the msid to `[sections] disabled_msids`.
- **Excluded formats**: jokes, video, photo galleries, visual and web stories are never crawled.

## API

```
GET /topicclusters?scope=home          top clusters for the app home (active only)
GET /topicclusters?msid=2354729        clusters for a section page (active + cooling)
GET /topicclusters?story=135086995     clusters a story belongs to (articleshow module)
GET /health
```

The same JSON is written to `out/` (`home.json`, `sections/<msid>.json`, `story_index.json`, `clusters.json`),
so it can also be pushed to a CDN instead of served from here. Each cluster is a `topicCluster`:

```json
{"tn": "topicCluster", "clusterId": "tc_20261022_fa0b9b4fe2", "title": "करवाचौथ 2026", "subtitle": "",
 "titleSource": "rules", "tier": "soft", "status": "active", "count": 3, "upd": "Oct 22, 2026, 11:33 AM",
 "heroImageId": "135108978", "sectionsSpanned": ["फैमिली", "फैशन"],
 "items": [{"id": "135108978", "hl": "…", "imageid": "135108978", "imgsize": "1382247", "subsecname": "फैशन",
            "cmstype": "PHOTOGALLERYSLIDESHOWSECTION", "lu": "Oct 22, 2026, 11:33 AM",
            "wu": "https://navbharattimes.indiatimes.com/lifestyle/fashion/…/articleshow/135108978.cms", "isNew": true}]}
```

To show it in the app, the feed service adds a slider entry the same way it does today, e.g. in an articleshow:
`{"tn": "topicCluster", "position": "3", "url": "https://<this service>/topicclusters?story=<msid>"}`.

## Frontend

`python -m nbt_topics serve` also serves:

- **`/ui/`**: the app screens. Home with topic carousels, a tab per section that has topics, the article
  page with the "इन टॉपिक पर और पढ़ें" module (chips switch between a story's topics), and a full topic page.
  Dark and light themes follow the device; the sun button cycles auto / dark / light.
- **`/ui/desk`**: the desk page. Every live topic with its title source, tier, score, where it shows and its
  stories; **Hide topic** removes it from the app immediately (needs the `TOPICS_ADMIN_TOKEN` value).
- **`/ui/topic-carousel.js`**: the embeddable widget behind both. Drop it into any NBT web page:

```html
<script src="https://<service>/ui/topic-carousel.js" defer></script>

<nbt-topic-carousel api="https://<service>" scope="home"></nbt-topic-carousel>      <!-- home -->
<nbt-topic-carousel api="https://<service>" msid="2354729"></nbt-topic-carousel>    <!-- section page -->
<nbt-topic-carousel api="https://<service>" story="135086995"></nbt-topic-carousel> <!-- end of article -->
```

It needs no framework, isolates its styles (Shadow DOM), hides itself when there's nothing to show, and
fires `nbt-topic-open` / `nbt-topic-open-cluster` events so a page or an app WebView can handle taps itself
(call `preventDefault()`); otherwise a card opens the article URL. Optional attributes: `limit`, `offset`,
`theme="dark|light"`, `topic-url="/topic/{id}"`. Set `[server] cors_origin` to your site's origin in production.

Images come from `[feed] image_url` (built from each story's `imageid`); if one fails to load, the card
shows a tinted placeholder instead of a broken image. Check that template against your image server.

For the native Android/iOS app, the screens in `/ui/` are the reference: render the same JSON natively, or
load the widget in a WebView.

## Running it in production

```bash
python -m nbt_topics daemon        # a cycle every [daemon] interval_minutes (default 15)
python -m nbt_topics serve         # in a second process
```

Each section is only refetched when its tier's `refresh_minutes` has passed (hard 15, medium 30, soft 60),
using the cache in `state/feed_cache`. If a cycle fails, the last good outputs stay in place. Example units
are in `deploy/`.

## Things to confirm on your side

- **Page parameter.** The feed reports paging in `pg` but the request parameter isn't visible in it. The crawler
  uses `curpg`; if page 2 comes back identical to page 1 it logs a warning and uses page 1 only. Set
  `[feed] page_param` to the right name.
- **First-week review.** Look at `python -m nbt_topics list` and the run reports (stored in the `runs` table)
  daily until the error rate is clearly near zero; tune `[clustering]` and the stop-word lists in `text.py`.
- **Taxonomy gaps.** `python -m nbt_topics sections` lists the 16 rows with no usable msid (e.g. the election
  sub-sections marked `#N/A`); those sections are not crawled until they get one.

## Tests

```bash
pip install pytest && python -m pytest -q
```

18 tests on the real lifestyle feed and taxonomy: clustering result, stable ids across runs, a new story joining
an existing cluster, archiving, kill switch, disabled sections, paging detection, AI verify and title,
guardrail fallbacks, hard-tier and sensitive-story headlines, the HTTP API and frontend endpoints, and the production CLI talking over
HTTP to stand-ins for the feed and the Anthropic API.
