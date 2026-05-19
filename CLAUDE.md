# Ruhratna AI Stylist — Resume Context

This file is a snapshot for picking up the project in a fresh Claude thread. It captures product context, technical decisions, what is and isn't built, and the immediate next steps.

## Product context

- **Brand:** Ruhratna — a D2C imitation jewellery brand. Storefront at [ruhratna.com](https://ruhratna.com).
- **Stack:** WordPress + WooCommerce hosted on Kinsta.
- **This feature:** AI Stylist — the customer uploads an outfit photo, picks an occasion, and gets jewellery recommendations from the Ruhratna catalog. Surfaces on a dedicated page (planned: `ruhratna.com/ai-stylist`).

## Technical decisions made

- **Two separate API endpoints**, not one. `/analyse` (image → outfit JSON) returns in ~6s and lets the frontend show a "stylist reading" while `/match` (outfit JSON + catalog → recommendations) runs.
- **Model choice and fallback:**
  - `analyse.py` — primary `gemini-2.5-flash-lite`, fallback `gemini-3.1-flash-lite`
  - `match.py` — primary `gemini-2.5-pro`, fallback `gemini-2.5-flash-lite`
  - Both files use the same retry pattern: 3 attempts × 3-second sleep on the primary, then 3 attempts on the fallback, then return `{"error": "All models failed"}`. Implemented as a Python `for-else` over `[primary, fallback]` with an inner retry loop.
- **Catalog handling:** 162 products parsed from `catalog/recommendationeng.xml` (the Ruhratna WooCommerce feed) into two prepared artifacts — `catalog_products.json` (lookup dict for URL cross-referencing) and `catalog_formatted.txt` (the LLM-ready block embedded in the match prompt). `match.py` preloads both at module import time via `preload_catalog()` so the first request isn't slow.
- **Output structure** from `/match`: 3–6 recommendations split into tier 1 (top 3) and tier 2 (next 2–3, optional), classified by `type` (neck / ears / accent / hands), plus an optional `complete_look` block that only sets `suggested: true` when 2+ tier-1 picks of different types genuinely coordinate.
- **`temperature=0`** on every Gemini call for consistency between runs.
- **`v1alpha`** API version explicitly set on the genai client (`http_options={"api_version": "v1alpha"}`) — required to access the newer model IDs.
- **URL cross-reference:** after Gemini returns recommendations, `match.py` overwrites `image_url` and `product_url` from `catalog_products.json` keyed on `product_id`. So even if the model paraphrases or hallucinates a URL, the response carries the authoritative `ruhratna.com` link.
- **Async polling for `/match`**, not synchronous. **Status: DONE — live on Railway and working end-to-end.** `POST /match` generates a UUID `job_id`, spawns a background thread that runs `match_jewellery`, and returns `{"job_id": "..."}` with `202 Accepted` immediately. The client then polls `GET /result/<job_id>` until status is `done` or `error`. Avoids hitting reverse-proxy/CDN timeouts on the ~19s Gemini call and keeps the request worker free to serve other traffic while the Gemini round trip is in flight. Jobs live in an in-memory dict (`jobs`) guarded by a `threading.Lock`; the `/result` endpoint deletes the entry on the fetch that returns the final outcome (no TTL — jobs persist until claimed, lost on worker restart). In production we run the gunicorn `gevent` worker class, which monkey-patches `threading`, so `threading.Thread` becomes a cooperative greenlet and `match_jewellery`'s `requests`-based Gemini call yields the event loop on I/O.

## What is built

- **`catalog.py`** — offline preparation script. Run manually (or via cron later). Reads the XML feed, filters out `Gift Cards`, `Hot Pick Any 5 @ 1999`, `Combo`, and out-of-stock items. Writes `catalog_products.json` and `catalog_formatted.txt`.
- **`analyse.py`** — Call 1. Takes `(image_base64, occasion)`, returns the structured outfit dict. Has a CLI test block: `python analyse.py img.jpg office`.
- **`match.py`** — Call 2. Takes `(outfit_analysis, occasion)`, returns `{stylist_reading, recommendations, complete_look}`. Cross-references URLs. Has a CLI test block with a hardcoded black-saree sample.
- **`app.py`** — Flask REST API. `POST /analyse` (sync, ~6s blocking) and `GET /health` are normal blocking endpoints. `POST /match` is asynchronous: it queues a job and returns a `job_id`. `GET /result/<job_id>` polls the job state and returns `running` / `done` / `error`. Done and errored jobs are deleted on fetch. CORS enabled for all origins. `preload_catalog()` runs at module-import time so the first request isn't slow.
- **Deployment plumbing** — `Procfile` (gunicorn + gevent worker class, 2 workers × 100 connections, 120s timeout), `runtime.txt` (Python 3.11.9), `.railwayignore`, `gevent` + `gunicorn` in `requirements.txt`.
- **Live on Railway** — **DONE.** Deployed from this repo on the `main` branch. Live URL: <https://web-production-8b1fc.up.railway.app>. Env vars set in the Railway dashboard.

## What is NOT built yet

- **WordPress plugin** — the frontend that will live on `ruhratna.com/ai-stylist`. Not started.
- **Cron job** for catalog refresh — `catalog.py` exists, but no schedule wired up.
- **Custom domain setup** on Railway — pending deployment.
- **`test/test_outfit.py`** — placeholder file, not populated.

## Performance (measured locally)

- `/analyse` → ~6 seconds end-to-end
- `/match` → ~19 seconds (heavier prompt, larger output, `gemini-2.5-pro`)
- User-facing flow: outfit reading appears at ~6s, recommendations at ~25s total

The 120-second gunicorn timeout in `Procfile` accommodates this with generous headroom. The user-facing wait is also gated by the WordPress plugin's polling interval (~2s), so a typical end-to-end `/match` round trip is ~21–22s wall-clock.

## Railway deployment setup

Railway uses its default Nixpacks builder — there's no `railway.json` or `nixpacks.toml`. It just picks up the files in the repo root and builds.

- **`ai-stylist-backend/` IS the repo root.** No monorepo subdir config needed on the Railway side.
- **`Procfile`** — `web: gunicorn app:app --worker-class gevent --workers 2 --worker-connections 100 --timeout 120 --bind 0.0.0.0:$PORT`. Two `gevent` workers × 100 cooperative greenlet connections each (~200 concurrent in-flight requests). 120s worker timeout protects against a stuck Gemini call. `gevent` monkey-patches `threading`, which is what makes the background-thread polling pattern in `app.py` actually cooperative under load (greenlets that yield on I/O) rather than spawning real OS threads.
- **`runtime.txt`** — `python-3.11.9`. Started at `python-3.11.0` but Railway's Nixpacks didn't resolve that exact patch, so it was bumped. Don't downgrade without re-testing the build.
- **`.railwayignore`** — excludes `.env`, `__pycache__/`, `*.pyc`, `venv/`, images (`*.jpeg` / `*.jpg` / `*.png`), and `*.xml` so the source product feed and local test images stay out of the build context. Mirrors `.gitignore` roughly but isn't a substitute for it — both files exist independently.
- **Env vars are set in the Railway dashboard, not via `.env`.** The dashboard is the source of truth in production; `.env` is local-dev only and excluded from both git and the Railway build context. The five keys to set: `GEMINI_API_KEY`, `ANALYSE_MODEL_PRIMARY`, `ANALYSE_MODEL_FALLBACK`, `MATCH_MODEL_PRIMARY`, `MATCH_MODEL_FALLBACK`. `PORT` is injected automatically by Railway and read by the Procfile's `$PORT`.
- **CORS** is permissive (`CORS(app)` in `app.py`, no origin allowlist) so the WordPress plugin can call from any origin once deployed. Tighten later if needed.
- **Catalog artifacts ship in the repo.** `catalog/catalog_products.json` and `catalog/catalog_formatted.txt` are committed so Railway doesn't need to run `catalog.py` at deploy time. If the WooCommerce feed changes, regenerate locally (`python catalog.py`) and commit the refreshed artifacts before redeploying. The source XML is gitignored and railwayignored — only the prepared artifacts travel with deploys.

## Current status

Backend POC is **complete and tested locally**. Both endpoints have been smoke-tested via Flask test client and via real Gemini calls. The fallback chain has been exercised in practice (saw `gemini-2.5-pro` 503 once, `gemini-2.5-flash-lite` took over cleanly).

**Repository:** [github.com/prateekverma547/ruhratna-ai-stylist](https://github.com/prateekverma547/ruhratna-ai-stylist) (main branch). The `ai-stylist-backend/` directory is the repo root.

**Deployment:** **DONE — live on Railway** at <https://web-production-8b1fc.up.railway.app>, deployed from `main`. The five env keys (`GEMINI_API_KEY`, `ANALYSE_MODEL_PRIMARY`, `ANALYSE_MODEL_FALLBACK`, `MATCH_MODEL_PRIMARY`, `MATCH_MODEL_FALLBACK`) are set in the Railway dashboard. `/health` confirms model and product-count state.

**Immediate next step:** build the WordPress plugin. Plugin should target the live Railway URL above and use the polling pattern for `/match` (POST → `job_id` → poll `/result/<job_id>`).

## WordPress plugin plan

- **Shortcode:** `[ruhratna_ai_stylist]`
- **Page:** `ruhratna.com/ai-stylist`
- **Flow** (4 screens already mockuped):
  1. Upload outfit photo + pick occasion
  2. Loading → `POST /analyse`, show outfit reading when it returns (~6s)
  3. Loading → `POST /match` to get a `job_id`, then poll `GET /result/<job_id>` (every ~2s) until `status === "done"`. Render the recommendations grid + `complete_look` card if `suggested: true`. On `status === "error"`, surface the error message and offer a retry.
  4. Final styled output with "Add to cart" links to the WooCommerce product pages

The plugin needs to know the Railway base URL (plugin setting) and the polling interval. CORS is already permissive on the API side.

## Key files

| Path | What |
|---|---|
| `catalog/recommendationeng.xml` | Source WooCommerce product feed. Not deployed (`.railwayignore` excludes `*.xml`). |
| `catalog/catalog_products.json` | Parsed product dicts. Used by `/match` for URL lookup. Committed. |
| `catalog/catalog_formatted.txt` | LLM-ready catalog string. Embedded into match prompt. Committed. |
| `.env` | Real API keys. Never committed. |
| `.env.example` | Template listing all 7 required env keys. |
| `Procfile` | `gunicorn app:app --worker-class gevent --workers 2 --worker-connections 100 --timeout 120 --bind 0.0.0.0:$PORT` |
| `runtime.txt` | `python-3.11.9` |

## Conventions worth knowing

- **Prompts use `__SENTINEL__` placeholders + `.replace()`**, not f-strings. The prompts contain literal `{` `}` JSON braces that would break f-string parsing.
- **JSON parsing uses `removeprefix`/`removesuffix`**, not `.strip("```json")`. The latter is a Python footgun (strips characters from a set, not a substring).
- **Each module reads its own env vars at import time** via `python-dotenv`. No central config object.
- **`match.py` preloads the catalog via a module-level cache** (`_catalog_text`, `_product_lookup`). `match_jewellery()` calls `preload_catalog()` defensively, so standalone `python match.py` runs work too.
- **The `for-else` pattern in retry/fallback logic is intentional.** The inner loop's `break` happens only on success; on retry exhaustion, the loop completes naturally so the `for-else` triggers `continue` to the next model.
