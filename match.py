"""
Jewellery matching (Call 2) for Ruhratna AI Stylist.

Takes the structured outfit analysis from analyse.py plus an occasion,
asks the model to pick 3-6 complementary pieces from the prepared
catalog, and returns a stylist response dict.

Primary model: Claude Sonnet 4.6 via Anthropic API (stable latency,
strict JSON output, good Indian-fashion reasoning).
Fallback: Gemini 2.5 Flash-lite via google-genai (kept cross-vendor
so an Anthropic incident doesn't take the whole flow down).

Routing happens in _call_model() based on model-name prefix:
"claude-*" → Anthropic SDK, "gemini-*" → google-genai SDK.
"""

import json
import logging
import os
import time

from anthropic import Anthropic
from dotenv import load_dotenv
from google import genai

load_dotenv()

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MATCH_MODEL_PRIMARY = os.getenv("MATCH_MODEL_PRIMARY")
MATCH_MODEL_FALLBACK = os.getenv("MATCH_MODEL_FALLBACK")

CATALOG_TEXT_PATH = "catalog/catalog_formatted.txt"
CATALOG_JSON_PATH = "catalog/catalog_products.json"

# Cap on Claude visible output. Our prompt produces ~500-900 tokens of JSON;
# 4096 leaves generous headroom without enabling runaway costs.
MATCH_MAX_OUTPUT_TOKENS = 4096

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1alpha"},
) if GEMINI_API_KEY else None

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


def _call_model(model: str, prompt: str) -> str:
    """Route the request to the right SDK based on model-name prefix.

    Returns the raw response text. Caller handles markdown stripping
    and JSON parsing.
    """
    if model.startswith("claude-"):
        if anthropic_client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set but Claude model requested")
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=MATCH_MAX_OUTPUT_TOKENS,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    if model.startswith("gemini-"):
        if client is None:
            raise RuntimeError("GEMINI_API_KEY not set but Gemini model requested")
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config={"temperature": 0},
        )
        return response.text
    raise ValueError(f"Unknown provider for model: {model}")


_catalog_text = None
_product_lookup = None


def preload_catalog(force: bool = False) -> None:
    """Load catalog files into module-level cache. Idempotent unless force=True."""
    global _catalog_text, _product_lookup
    if not force and _catalog_text is not None and _product_lookup is not None:
        return

    try:
        with open(CATALOG_TEXT_PATH, "r", encoding="utf-8") as f:
            _catalog_text = f.read()
        with open(CATALOG_JSON_PATH, "r", encoding="utf-8") as f:
            products = json.load(f)
        _product_lookup = {p["id"]: p for p in products if "id" in p}
        log.info("Catalog preloaded: %d products", len(_product_lookup))
    except FileNotFoundError as e:
        log.error("Catalog file missing: %s. Run catalog.py first.", e.filename)


def get_product_count() -> int:
    return len(_product_lookup) if _product_lookup else 0


PROMPT_TEMPLATE = """You are a senior jewellery stylist at Ruhratna, an Indian imitation jewellery brand selling ethnic and western jewellery.

CUSTOMER OUTFIT DETAILS:
__OUTFIT_SUMMARY__

YOUR TASK:
Recommend between 3 and 6 jewellery pieces from our catalog that genuinely complement this outfit.
Remember alawys read full catalog, so that you can recommend the best from full list.

RULES FOR RECOMMENDATIONS:
- Minimum 3, maximum 6 products
- Return 6 ONLY if 6 genuinely good matches exist in the catalog — never pad with poor matches
- tier 1: your top 3 highest match scores (shown first)
- tier 2: the next 2-3 ONLY if they genuinely suit the outfit; otherwise omit tier 2 entirely
- type: classify each piece as one of "neck" / "ears" / "accent" / "hands"
- Try for variety across types but DO NOT force it — if only neck pieces suit, recommend the best neck pieces
- Order ALL recommendations by match_score descending

BEFORE YOU PICK — understand these styling rules:

COLOUR RULE (most important):
- NEVER recommend jewellery in the same colour as the outfit
- Black outfit → gold-toned, silver, champagne, pearl, oxidised, or coloured stone pieces
- White outfit → gold kundan, meenakari, oxidised silver, coloured pieces
- Dark jewel tones → gold with contrasting stones
- Pastels → delicate gold or oxidised pieces

NECKLINE RULE:
- V-neck → chokers, short necklaces, pendants work best
- Round neck → chokers, sets, medium necklaces
- High neck → earrings only, no necklace
- Boat neck → collar pieces, short necklaces
- Off shoulder → statement necklaces, chokers

STYLE WEIGHT RULE:
- Minimal outfit → medium weight jewellery to add interest without overpowering
- Heavy outfit → statement pieces that match the energy
- Casual outfit → delicate or oxidised pieces
- Office outfit → subtle elegance, avoid very heavy pieces

OCCASION RULE:
- Wedding/Festive → traditional ethnic pieces preferred
- Office/Casual → lighter, more contemporary pieces
- Party/Cocktail → modern, statement, or fusion pieces

ETHNIC vs WESTERN RULE:
- Ethnic outfit → ethnic or fusion jewellery
- Western outfit → western, geometric, gemstone pieces
- Fusion outfit → either works, prefer fusion pieces

COMPLETE LOOK — after picking individual pieces, decide if 2 or 3 of your tier-1 picks genuinely coordinate as a styled look:
- suggested: true ONLY when 2+ tier-1 recommendations are different types AND genuinely coordinate together
- pieces: 2-3 product_ids drawn ONLY from tier-1 recommendations
- combined_price: sum of those pieces' prices (integer)
- look_description: 1 warm sentence explaining the pairing
- If the pieces don't naturally coordinate, set suggested: false and leave pieces / look_description / combined_price as empty/zero — never force a complete look

YOUR PROCESS — do this in your head before answering:
1. Read the outfit details above carefully
2. Note: occasion, neckline, style weight, colour, ethnic or western
3. Read through the COMPLETE catalog below
4. Find all products that match the styling rules
5. Pick the 3 absolute best (tier 1) from the ENTIRE catalog
6. Optionally pick 2-3 more (tier 2) only if they genuinely suit
7. Decide whether the tier-1 picks form a coordinated complete look

COMPLETE PRODUCT CATALOG:
__CATALOG_TEXT__

Now return your answer as ONLY a valid JSON object. No markdown. No backticks. No explanation. Just JSON.

{
  "stylist_reading": "2-3 sentences about the outfit and overall styling direction",
  "recommendations": [
    {
      "product_id": "exact id from catalog",
      "title": "exact title from catalog",
      "type": "neck / ears / accent / hands",
      "tier": 1,
      "reason": "2 sentences. Specific styling reason.",
      "match_score": 98,
      "price": 1200,
      "sale_price": 900,
      "image_url": "from catalog",
      "product_url": "from catalog"
    }
  ],
  "complete_look": {
    "suggested": true,
    "pieces": ["product_id_1", "product_id_2"],
    "look_description": "1 sentence why these work together",
    "combined_price": 2950
  }
}

Return between 3 and 6 recommendations.
Order ALL by match_score descending.
Pick from across the full catalog."""


def _build_outfit_summary(outfit_analysis: dict, occasion: str) -> str:
    return (
        f"Outfit Type: {outfit_analysis.get('outfit_type')}\n"
        f"Dominant Colour: {outfit_analysis.get('dominant_colour')}\n"
        f"Colour Family: {outfit_analysis.get('colour_family')}\n"
        f"Neckline: {outfit_analysis.get('neckline')}\n"
        f"Style Weight: {outfit_analysis.get('style_weight')}\n"
        f"Ethnic or Western: {outfit_analysis.get('western_or_ethnic')}\n"
        f"Embellishment: {outfit_analysis.get('border_or_embellishment')}\n"
        f"Colour Accents: {outfit_analysis.get('colour_accents')}\n"
        f"Occasion: {occasion}"
    )


def match_jewellery(outfit_analysis: dict, occasion: str) -> dict:
    """Send outfit summary + catalog to Gemini and return top 3 picks."""
    preload_catalog()
    if _catalog_text is None:
        return {"error": "Catalog not loaded. Run catalog.py first."}

    catalog_text = _catalog_text
    product_lookup = _product_lookup or {}

    outfit_summary = _build_outfit_summary(outfit_analysis, occasion)
    prompt = (
        PROMPT_TEMPLATE
        .replace("__OUTFIT_SUMMARY__", outfit_summary)
        .replace("__CATALOG_TEXT__", catalog_text)
    )

    models_to_try = [MATCH_MODEL_PRIMARY, MATCH_MODEL_FALLBACK]
    response_text = None
    last_error = None

    for model in models_to_try:
        for attempt in range(3):
            try:
                # === Gemini-only call site (kept commented for rollback reference) ===
                # response = client.models.generate_content(
                #     model=model,
                #     contents=[prompt],
                #     config={"temperature": 0},
                # )
                # response_text = response.text
                # === Provider-routing call (Claude primary, Gemini fallback) ===
                response_text = _call_model(model, prompt)
                break
            except Exception as e:
                last_error = e
                if attempt < 2:
                    log.error("[%s] Attempt %d failed, retrying in 3s...", model, attempt + 1)
                    time.sleep(3)
                    continue
                log.error("[%s] All retries exhausted: %s", model, str(e))
        else:
            continue
        break
    else:
        return {"error": "All models failed", "details": str(last_error)}

    text = response_text.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    if text.startswith("```"):
        text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Could not parse recommendations", "raw": response_text}

    for rec in result.get("recommendations", []):
        pid = rec.get("product_id")
        if pid and pid in product_lookup:
            p = product_lookup[pid]
            rec["image_url"] = p["image_url"]
            rec["product_url"] = p["product_url"]
            rec["price"] = p["price"]
            rec["sale_price"] = p.get("sale_price")

    complete_look = result.get("complete_look")
    if isinstance(complete_look, dict) and complete_look.get("suggested"):
        pieces = complete_look.get("pieces") or []
        total = 0.0
        for pid in pieces:
            if pid in product_lookup:
                p = product_lookup[pid]
                sp = p.get("sale_price")
                total += sp if sp is not None else p["price"]
        complete_look["combined_price"] = int(total)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    sample_outfit = {
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
    }

    log.info("Finding jewellery matches...")
    log.info("Occasion: office")

    result = match_jewellery(sample_outfit, "office")

    if "error" in result:
        log.error("Error: %s", result["error"])
        if "details" in result:
            log.error("Details: %s", result["details"])
    else:
        log.info("Stylist says: %s", result["stylist_reading"])
        for i, rec in enumerate(result["recommendations"], 1):
            log.info(
                "#%d [tier %s · %s] — %s (%s%% match)",
                i, rec.get("tier"), rec.get("type"), rec["title"], rec["match_score"],
            )
            if rec.get("sale_price") is not None:
                log.info("     Price: ₹%s → Sale: ₹%s", rec["price"], rec["sale_price"])
            else:
                log.info("     Price: ₹%s", rec["price"])
            log.info("     Why: %s", rec["reason"])
            log.info("     URL: %s", rec["product_url"])

        look = result.get("complete_look", {})
        if look.get("suggested"):
            log.info("Complete look suggested:")
            log.info("     Pieces: %s", ", ".join(look.get("pieces", [])))
            log.info("     Combined price: ₹%s", look.get("combined_price"))
            log.info("     Why: %s", look.get("look_description"))
        else:
            log.info("Complete look: not suggested for this outfit")
