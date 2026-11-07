# Niche Discovery Engine — System 1

An autonomous, local-first market-research pipeline. Five LangGraph agents mine
public sources for real pain points, extract structured evidence with a local
LLM, cluster the evidence into opportunities, and score each one with a
weighted formula. You review and approve opportunities in the Streamlit
Control Center, then export them as the handoff payload for System 2.

Built to run 24/7 on a **Mac mini (Apple Silicon, 32GB RAM)** with **LM Studio**
as the local AI server. No cloud LLM calls, no proxies, no headless browsers.

---

## Architecture

```
                ┌────────────────────────────────────────────────────┐
                │                  LangGraph pipeline                │
                │                                                    │
  seeds ──────► │  Scout ──► Collector ──► Extractor ──► Cluster ──► Strategist
                │  (PRIMARY)  (httpx/RSS/   (SMALL)      (PRIMARY)   (PRIMARY)
                │             GraphQL)     │            │           │
                └──────────────────────────┼────────────┼───────────┼─────┘
                                           ▼            ▼           ▼
                ┌────────────────────────────────────────────────────┐
                │   SQLite (SQLModel): RawPost → PainPoint →           │
                │   OpportunityCluster  (+ Run log)                  │
                └──────────────────────────┬─────────────────────────┘
                                           ▼
                ┌────────────────────────────────────────────────────┐
                │  Streamlit Control Center: Dashboard · Evidence    │
                │  Vault · War Room · Approvals · Export (System 2)  │
                └────────────────────────────────────────────────────┘
```

| Agent | Model | Responsibility |
|---|---|---|
| Scout | PRIMARY (Qwen 27B Flash) | Expands seed keywords into 20 targeted search queries |
| Collector | — | Executes queries per source, dedupes, safety-filters, saves `RawPost` |
| Extractor | SMALL (Qwen MoE 35B Flash) | Extracts structured `PainPoint`s, discards noise |
| Cluster | PRIMARY | Semantically groups pains into `OpportunityCluster`s |
| Strategist | PRIMARY | Scores clusters, writes product ideas + validation plans |

**Scoring formula** (all components 0–10):

```
pain_score = pain_severity*0.25 + buyer_intent*0.25 + frequency*0.15
           + extraction_fit*0.20 + reachability*0.15
           - legal_risk_penalty - competition_penalty
```

`frequency = min(10, 2 × evidence_count)`; each penalty maps the model's 0–10
risk rating to 0–2 points. Full breakdown is stored per cluster as JSON.

**Resilience:** every source and every LLM call is isolated — one failure is
logged and skipped, never fatal. Retries use exponential backoff. Context
overflow shrinks the input budget automatically. Posts are deduped globally by
URL (unique index), so repeated nightly scans add nothing new to the vault. If
LM Studio is unreachable (or all live sources fail), the system falls back to
local fixtures + deterministic heuristics so the pipeline always completes
(**demo mode**).

---

## Setup

### 1. Tooling (macOS)

```bash
brew install uv          # package manager (Apple Silicon: instant bottle)
# alternative if brew wants a source build: curl -LsSf https://astral.sh/uv/install.sh | sh
cd ~/Desktop/niche-discovery-engine
uv sync                  # creates .venv with Python 3.12 + all dependencies
```

`uv` reads `.python-version` (3.12) and provisions the interpreter automatically.

### 2. LM Studio

1. Install [LM Studio](https://lmstudio.ai) and download two Qwen models:
   a ~27B dense model (primary) and a MoE model (small/fast).
2. Load both models, then open the **Developer** tab and start the server on
   port **1234** (it serves an OpenAI-compatible API at
   `http://localhost:1234/v1`). Enable "keep server running" so it stays up 24/7.
3. Copy the **exact model identifiers** from the Developer tab into `.env`:

```bash
cp .env.example .env
```

```dotenv
PRIMARY_MODEL=Qwen 27B Flash      # strategy, clustering, product ideas
SMALL_MODEL=Qwen MoE 35B Flash    # bulk extraction, classification
```

> The names above are placeholders from the spec — LM Studio identifiers are
> usually like `qwen2.5-27b-instruct`. Whatever string the server lists under
> `/v1/models` is what goes in `.env`. Any API key works; default is `lm-studio`.

### 3. Verify

```bash
uv run python main.py --demo     # offline end-to-end smoke test (fixtures)
uv run pytest                    # full test suite (all mocked, no network)
uv run ruff check .              # lint gate (also runs in CI)
```

---

## Custom extraction engine (X / Facebook)

`scraper/advanced_scraper.py` talks to the same internal GraphQL endpoints the
real web clients use, authenticated with **your own session cookies** — no
Selenium/Playwright, no proxies. Without cookies these sources are skipped
gracefully and everything else still runs.

### X (Twitter)

1. Log into x.com in your browser.
2. DevTools → Application → Cookies → copy `auth_token` and `ct0` into `.env`:
   ```dotenv
   X_AUTH_TOKEN=...
   X_CT0=...
   ```
3. GraphQL query IDs rotate. If X starts failing: DevTools → Network → run a
   search on x.com → find the `SearchTimeline` request → copy the ID segment
   of its URL into `X_GRAPHQL_QUERY_ID`.

### Facebook

1. Log into facebook.com, DevTools → Network → copy the full `cookie` header
   of any request (must contain `c_user` and `xs`) into `FACEBOOK_COOKIE`.
2. If search fails, copy a fresh `doc_id` from a `api/graphql` search request
   into `FACEBOOK_DOC_ID`.

Cookies are secrets: `.env` is gitignored. They expire — refresh them when the
sources start returning auth errors. Use accounts you control; respect each
platform's ToS and rate limits (built-in delays).

---

## Running

### Control Center (UI)

```bash
uv run streamlit run ui.py
```

- **Sidebar:** run button, source toggles, items-per-source, live log viewer.
- **Dashboard:** posts/pains/opportunities metrics, run history, top-scored chart.
- **Evidence Vault:** searchable raw posts + extracted pain points, post inspector.
- **War Room:** every cluster with score breakdown, product idea, validation plan.
- **Approvals:** one-click Approve / Reject (persisted to SQLite).
- **Export:** approved opportunities as CSV/JSON — the System 2 handoff schema.

### Overnight run

```bash
./run_overnight.sh               # auto-detects demo vs live
./run_overnight.sh --demo        # force demo
./run_overnight.sh --live --sources hacker_news,reddit --limit 50
```

The script wraps the pipeline in `caffeinate -i -s`, so the Mac mini will not
sleep mid-run. Logs go to console + `logs/engine.log` (JSON lines, rotating).

### Nightly schedule (launchd)

```bash
./scripts/install_launchd.sh           # every night at 02:00
./scripts/install_launchd.sh 3 30      # every night at 03:30
launchctl start com.niche-discovery.overnight   # trigger immediately
./scripts/install_launchd.sh --uninstall
```

---

## Demo mode

Auto-detected: if LM Studio isn't serving models at `localhost:1234`, the
pipeline loads `fixtures/raw_posts.json` and uses deterministic heuristics in
place of the LLM. The full pipeline — collection, extraction, clustering,
scoring, approvals, export — works identically. Force with `DEMO_MODE=true`,
`--demo`, or `--live` to override.

## Testing

```bash
uv run pytest
```

The suite mocks the DB (in-memory/tmp SQLite), LLM (fake OpenAI client), and
HTTP (httpx MockTransport) — no network, no LM Studio needed. Covers model
routing, JSON robustness, context fallback, payload budget capping, every
source parser, cookie-gated scraper behavior, the safety blocklist, global
cross-run URL dedupe, per-post crash isolation, the scoring formula,
source-failure isolation, and a full end-to-end demo run. `ruff check` runs in
the GitHub Actions CI workflow on every push.

---

## Transferring to the Mac mini

The project is fully self-contained and path-independent (everything resolves
relative to the project folder):

```bash
# on the MacBook
rsync -av --exclude .venv --exclude data --exclude logs \
  ~/Desktop/niche-discovery-engine/ mini:~/Desktop/niche-discovery-engine/

# on the Mac mini
brew install uv
cd ~/Desktop/niche-discovery-engine
uv sync
cp .env.example .env        # then set your real LM Studio model IDs
uv run python main.py --demo
./scripts/install_launchd.sh
```

Include `data/` in the rsync if you want to carry over existing evidence and
approvals. `uv.lock` pins exact dependency versions for a reproducible install.

---

## Project structure

```
├── main.py                  # CLI entry (overnight runs)
├── ui.py                    # Streamlit Control Center
├── config.py                # pydantic-settings, .env loading, demo detection
├── llm_client.py            # LM Studio wrapper, task→model routing
├── heuristics.py            # deterministic offline LLM stand-in
├── export.py                # System 2 handoff (JSON/CSV, schema v1.0)
├── logging_setup.py         # console + rotating JSON file logs
├── utils.py                 # retries/backoff, tolerant JSON, dates
├── database/
│   ├── models.py            # RawPost, PainPoint, OpportunityCluster, Run
│   └── db.py                # engine + session management
├── scraper/
│   ├── advanced_scraper.py  # session-cookie GraphQL engine (X, Facebook)
│   ├── sources.py           # HN Algolia, Reddit JSON, job boards, YC RFS
│   ├── base.py              # shared HTTP client, polite delays
│   ├── safety.py            # adult/illegal/weapons/hate exclusions
│   └── blocked_patterns.txt # editable safety blocklist (one regex per line)
├── agents/
│   ├── graph.py             # LangGraph wiring + run_pipeline()
│   ├── scout.py / collector.py / extractor.py / cluster.py / strategist.py
│   ├── state.py / schemas.py
├── fixtures/raw_posts.json  # demo data
├── scripts/install_launchd.sh
├── run_overnight.sh         # caffeinate-wrapped pipeline
└── tests/                   # pytest suite (fully mocked)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| UI shows DEMO MODE but LM Studio is running | Check the server is on port 1234 and a model is **loaded**; verify `curl localhost:1234/v1/models` |
| `model not found` errors | Set `PRIMARY_MODEL`/`SMALL_MODEL` to the exact IDs from `/v1/models` |
| X/Facebook return 401/403 | Cookies expired — refresh them; GraphQL ID may need rotation |
| Reddit returns 403 | Rate-limited; the source stops politely. Raise `REQUEST_DELAY_SECONDS` |
| Slow extraction | Lower `MAX_POSTS_TO_EXTRACT` or load a faster SMALL model |
| launchd run fails | Check `logs/launchd.err.log`; ensure `uv` is on the PATH in the plist |