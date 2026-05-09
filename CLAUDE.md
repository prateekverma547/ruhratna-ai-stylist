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

## What is built

- **`catalog.py`** — offline preparation script. Run manually (or via cron later). Reads the XML feed, filters out `Gift Cards`, `Hot Pick Any 5 @ 1999`, `Combo`, and out-of-stock items. Writes `catalog_products.json` and `catalog_formatted.txt`.
- **`analyse.py`** — Call 1. Takes `(image_base64, occasion)`, returns the structured outfit dict. Has a CLI test block: `python analyse.py img.jpg office`.
- **`match.py`** — Call 2. Takes `(outfit_analysis, occasion)`, returns `{stylist_reading, recommendations, complete_look}`. Cross-references URLs. Has a CLI test block with a hardcoded black-saree sample.
- **`app.py`** — Flask REST API: `POST /analyse`, `POST /match`, `GET /health`. CORS enabled for all origins. `preload_catalog()` runs at import time.
- **Deployment plumbing** — `Procfile`, `runtime.txt` (Python 3.11.0), `.railwayignore`, `gunicorn` in `requirements.txt`. Railway-ready.

## What is NOT built yet

- **WordPress plugin** — the frontend that will live on `ruhratna.com/ai-stylist`. Not started.
- **Railway deployment** — files are ready, repo isn't pushed yet.
- **Cron job** for catalog refresh — `catalog.py` exists, but no schedule wired up.
- **Custom domain setup** on Railway — pending deployment.
- **`test/test_outfit.py`** — placeholder file, not populated.

## Performance (measured locally)

- `/analyse` → ~6 seconds end-to-end
- `/match` → ~19 seconds (heavier prompt, larger output, `gemini-2.5-pro`)
- User-facing flow: outfit reading appears at ~6s, recommendations at ~25s total

The 60-second gunicorn timeout in `Procfile` accommodates this with headroom.

## Current status

Backend POC is **complete and tested locally**. Both endpoints have been smoke-tested via Flask test client and via real Gemini calls. The fallback chain has been exercised in practice (saw `gemini-2.5-pro` 503 once, `gemini-2.5-flash-lite` took over cleanly).

**Immediate next step:** push `ai-stylist-backend/` to GitHub → deploy on Railway → set env vars in dashboard → confirm `/health` from the Railway URL.

**After that:** build the WordPress plugin.

## WordPress plugin plan

- **Shortcode:** `[ruhratna_ai_stylist]`
- **Page:** `ruhratna.com/ai-stylist`
- **Flow** (4 screens already mockuped):
  1. Upload outfit photo + pick occasion
  2. Loading → call `/analyse`, show outfit reading when it returns (~6s)
  3. Loading → call `/match` in parallel/after, show recommendations grid + `complete_look` card if `suggested: true`
  4. Final styled output with "Add to cart" links to the WooCommerce product pages

The plugin needs to know the Railway base URL (env var or plugin setting) and call the two endpoints in sequence. CORS is already permissive on the API side.

## Key files

| Path | What |
|---|---|
| `catalog/recommendationeng.xml` | Source WooCommerce product feed. Not deployed (`.railwayignore` excludes `*.xml`). |
| `catalog/catalog_products.json` | Parsed product dicts. Used by `/match` for URL lookup. Committed. |
| `catalog/catalog_formatted.txt` | LLM-ready catalog string. Embedded into match prompt. Committed. |
| `.env` | Real API keys. Never committed. |
| `.env.example` | Template listing all 7 required env keys. |
| `Procfile` | `gunicorn app:app --workers 2 --timeout 60 --bind 0.0.0.0:$PORT` |
| `runtime.txt` | `python-3.11.0` |

## Conventions worth knowing

- **Prompts use `__SENTINEL__` placeholders + `.replace()`**, not f-strings. The prompts contain literal `{` `}` JSON braces that would break f-string parsing.
- **JSON parsing uses `removeprefix`/`removesuffix`**, not `.strip("```json")`. The latter is a Python footgun (strips characters from a set, not a substring).
- **Each module reads its own env vars at import time** via `python-dotenv`. No central config object.
- **`match.py` preloads the catalog via a module-level cache** (`_catalog_text`, `_product_lookup`). `match_jewellery()` calls `preload_catalog()` defensively, so standalone `python match.py` runs work too.
- **The `for-else` pattern in retry/fallback logic is intentional.** The inner loop's `break` happens only on success; on retry exhaustion, the loop completes naturally so the `for-else` triggers `continue` to the next model.
