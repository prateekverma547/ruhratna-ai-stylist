# Ruhratna AI Stylist

A Flask REST API that recommends jewellery from the Ruhratna catalog based on a customer's outfit photo. The customer uploads a picture, picks an occasion, and gets back 3–6 hand-picked pieces (with an optional "complete look" pairing) styled by Gemini.

## What it does

The service runs in two stages, exposed as two HTTP endpoints. `/analyse` takes the outfit photo and returns a structured JSON description (outfit type, colour, neckline, style weight, etc.) — fast enough to show the user a "stylist reading" before the heavier call returns. `/match` then takes that JSON description plus the prepared catalog and asks Gemini to pick the 3–6 best complementary pieces, classify them by type (neck / ears / accent / hands), split them into tier 1 (top 3) and tier 2 (next 2–3, optional), and decide whether two of them coordinate as a complete look.

## Architecture

```
WordPress frontend
    │
    ├── POST /analyse   ──►  analyse.py  ──►  Gemini (vision)
    │                                              │
    │                                              ▼
    │                                       outfit_analysis JSON
    │
    └── POST /match     ──►  match.py    ──►  Gemini (text)
                                                   │
                                                   ▼
                                       3–6 recommendations
                                       + optional complete_look
                                       (URLs cross-referenced
                                        against catalog JSON)
```

Catalog data is prepared offline by `catalog.py` from the WooCommerce product feed (`recommendationeng.xml`) and written to two artifacts that the API loads at startup. The split into two endpoints lets the frontend show the outfit reading immediately while the (slower) match call is still in flight.

## File structure

```
ai-stylist-backend/
├── app.py                  Flask REST API — wires analyse + match
├── analyse.py              Call 1: image (base64) → outfit JSON via Gemini Vision
├── match.py                Call 2: outfit JSON + catalog → recommendations + complete_look
├── catalog.py              Offline prep script: parses XML, writes JSON + formatted TXT
├── requirements.txt        Python dependencies
├── Procfile                Gunicorn launch command for Railway
├── runtime.txt             Python version pin (3.11.0)
├── .env.example            Template for environment variables
├── .gitignore              Files excluded from git
├── .railwayignore          Files excluded from Railway build context
├── catalog/
│   ├── recommendationeng.xml   Source product feed (not committed in deploys)
│   ├── catalog_products.json   Parsed product list — read by /match for URL lookup
│   └── catalog_formatted.txt   LLM-ready catalog string — embedded in match prompt
└── test/
    └── test_outfit.py      Test placeholder (not yet populated)
```

## API endpoints

### `POST /analyse`

**Request**
```json
{
  "image": "<base64-encoded JPEG/PNG>",
  "occasion": "office"
}
```
`occasion` is optional and defaults to `"festive"`.

**Response 200**
```json
{
  "success": true,
  "outfit_analysis": {
    "outfit_type": "saree",
    "dominant_colour": "black",
    "colour_family": "dark",
    "colour_tone": "dark",
    "neckline": "v-neck",
    "style_weight": "minimal",
    "western_or_ethnic": "ethnic",
    "occasion_confirmed": "office",
    "border_or_embellishment": "plain",
    "colour_accents": [],
    "image_quality": "good"
  }
}
```

**Errors**
- `400` if `image` field is missing or analyse step returns an error
- `500` on unexpected server exceptions

### `POST /match`

**Request**
```json
{
  "outfit_analysis": { "...exact dict returned by /analyse..." },
  "occasion": "office"
}
```
`occasion` is optional and defaults to `"festive"`.

**Response 200**
```json
{
  "success": true,
  "stylist_reading": "2-3 sentences about the outfit and overall styling direction",
  "recommendations": [
    {
      "product_id": "9720",
      "title": "Sorrel – Gold-Plated with Real Uncut Pearl Pendant Necklace",
      "type": "neck",
      "tier": 1,
      "reason": "...",
      "match_score": 99,
      "price": 3200,
      "image_url": "https://ruhratna.com/wp-content/uploads/...",
      "product_url": "https://ruhratna.com/shop/sorrel-..."
    }
  ],
  "complete_look": {
    "suggested": true,
    "pieces": ["9720", "7313"],
    "look_description": "1 sentence why these work together",
    "combined_price": 4000
  }
}
```

`recommendations` contains 3–6 items ordered by `match_score` descending. `tier 1` is the top 3; `tier 2` is the next 2–3, included only when those picks genuinely suit the outfit. `complete_look.suggested` is `true` only when 2+ tier-1 picks of different types coordinate together — otherwise it's `false` and `pieces` is empty.

**Errors**
- `400` if `outfit_analysis` field is missing
- `500` if match step returns an error (catalog not loaded, all models failed, JSON parse failed)

### `GET /health`

```json
{
  "status": "ok",
  "analyse_model": "gemini-2.5-flash-lite",
  "match_model": "gemini-2.5-pro",
  "products_loaded": 162
}
```

## Environment variables

Copy `.env.example` to `.env` and fill in. All seven keys:

| Key | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio API key. Required. |
| `ANALYSE_MODEL_PRIMARY` | Model used by `/analyse` first. Default in template: `gemini-2.5-flash-lite`. |
| `ANALYSE_MODEL_FALLBACK` | Used by `/analyse` if primary fails 3 retries. Default: `gemini-3.1-flash-lite`. |
| `MATCH_MODEL_PRIMARY` | Model used by `/match` first. Default: `gemini-2.5-pro`. |
| `MATCH_MODEL_FALLBACK` | Used by `/match` if primary fails 3 retries. Default: `gemini-2.5-flash-lite`. |
| `PORT` | Port the Flask server binds to. Defaults to `5000` if unset. Railway injects this automatically. |
| `FLASK_ENV` | Set to `development` to enable Flask debug mode. Any other value (or unset) → production. |

## Local development

```bash
cd ai-stylist-backend

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set GEMINI_API_KEY

python catalog.py        # one-time, builds catalog/catalog_products.json + catalog_formatted.txt
python app.py            # serves on http://localhost:5000
```

Test from another terminal:
```bash
curl http://localhost:5000/health

curl -X POST http://localhost:5000/analyse \
  -H "Content-Type: application/json" \
  -d "{\"image\": \"$(base64 -i path/to/outfit.jpg)\", \"occasion\": \"office\"}"
```

Each script also has a standalone test block:
```bash
python analyse.py path/to/outfit.jpg office
python match.py     # uses the hardcoded black-saree sample
```

## Catalog preparation

`catalog.py` is a standalone offline prep script. It reads `catalog/recommendationeng.xml`, filters out unavailable products and excluded categories (`Gift Cards`, `Hot Pick Any 5 @ 1999`, `Combo`), parses prices, and writes two artifacts:

- `catalog/catalog_products.json` — list of cleaned product dicts (id, title, description, categories, price, image_url, product_url). Used by `/match` to overwrite image and product URLs with authoritative values.
- `catalog/catalog_formatted.txt` — pre-built block of `[ID: …] Title: … Description: … Categories: … Price: ₹…` separated by `---`. This is what gets embedded into the match prompt.

**When to run it:** any time the catalog feed changes (new products, price updates, availability changes). Run manually for the POC; wire it into a daily cron later. The Flask app does not regenerate these files at runtime.

The two artifacts are committed alongside the source so deploys don't need the XML — `.railwayignore` excludes `*.xml` from the build context.

## Deployment (Railway)

The repo ships with a `Procfile`, `runtime.txt`, and `.railwayignore`. To deploy:

1. Push the `ai-stylist-backend/` directory to a GitHub repo.
2. Create a new Railway project from that repo.
3. In the Railway dashboard, set the environment variables listed above. Do **not** ship `.env` — `.railwayignore` excludes it from the build context.
4. Deploy. Railway uses Nixpacks, picks up `runtime.txt` for Python 3.11.0, installs `requirements.txt`, and runs the `Procfile` `web` command.
5. Custom domain → Railway dashboard → Settings → Domains. CORS is already enabled for all origins.

The Procfile runs `gunicorn app:app --workers 2 --timeout 60 --bind 0.0.0.0:$PORT`. Two workers handle concurrent requests; 60-second timeout accommodates the slower `/match` call (typically ~19s on `gemini-2.5-pro`).

## Tech stack

- **Python 3.11** — runtime
- **Flask 3** + **flask-cors** — REST API
- **Gunicorn** — production WSGI server
- **google-genai** (v1alpha) — Gemini SDK with explicit version pin
- **Pillow** — image decoding for the vision call
- **python-dotenv** — local env var loading
- **requests** — HTTP client used by `catalog.py` for future URL-based feed loading
