"""
Outfit analysis (Call 1) for Ruhratna AI Stylist.

Takes a base64-encoded outfit photo and an occasion, asks Gemini to
describe the outfit, and returns a structured dict the matcher can use.
"""

import base64
import io
import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from PIL import Image

load_dotenv()

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANALYSE_MODEL_PRIMARY = os.getenv("ANALYSE_MODEL_PRIMARY")
ANALYSE_MODEL_FALLBACK = os.getenv("ANALYSE_MODEL_FALLBACK")

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options={"api_version": "v1alpha"},
) if GEMINI_API_KEY else None


PROMPT_TEMPLATE = """You are an expert Indian fashion stylist analysing a customer outfit photo to help recommend jewellery.

Study this outfit image carefully and return ONLY a valid JSON object. No markdown. No backticks. No explanation. Just JSON.

The occasion has been selected by the user as OCCASION_PLACEHOLDER. Use this exact value for occasion_confirmed field. Do not override it.

BEFORE you describe the outfit, decide whether the image is usable for a jewellery recommendation.

Set confidence_flag to 0 and write a rejection_reason when:
- No clothing is visible in the image (random object, landscape, food etc.)
- Multiple people are in the image
- A person is visible but no clothing is visible at all (face/headshot only)
- Image is too blurry or dark to read any outfit details
- Image is a screenshot, graphic, illustration, or cartoon
- Clothing is partially visible but not enough to make a meaningful jewellery recommendation

Set confidence_flag to 1 when you can confidently analyse the outfit and make meaningful jewellery recommendations from it.

Use your own judgment — if you are unsure whether you have enough information to give a good recommendation, set confidence_flag to 0.

rejection_reason should be warm and friendly, written as if a stylist is speaking to the customer. Maximum 20 words. When confidence_flag is 1, set rejection_reason to "" (empty string).

{
  "confidence_flag": 1,
  "rejection_reason": "",
  "outfit_type": "saree / lehenga / anarkali / kurta / salwar suit / western dress / top / indo-western / other",
  "dominant_colour": "main colour in plain English",
  "colour_family": "warm / cool / neutral / jewel-toned / pastel / earthy / dark / bright",
  "colour_tone": "light / medium / dark",
  "neckline": "round neck / v-neck / boat neck / sweetheart / high neck / off-shoulder / halter / collar / not visible",
  "style_weight": "minimal / medium / heavy / statement",
  "western_or_ethnic": "ethnic / western / fusion",
  "occasion_confirmed": "OCCASION_PLACEHOLDER — copy this exact value as-is, do not infer from the image",
  "border_or_embellishment": "gold border / silver border / zari work / embroidery / heavy embellishment / plain / none",
  "colour_accents": ["list", "accent", "colours", "if", "any"],
  "image_quality": "good / average / poor"
}

confidence_flag must be an integer 0 or 1. Never use strings, booleans, or any other value. When confidence_flag is 0, still fill the other fields with your best attempt — the app will ignore them and surface rejection_reason to the customer."""


def analyse_outfit(image_base64: str, occasion: str) -> dict:
    """Call Gemini with the outfit image and return structured analysis."""
    prompt = PROMPT_TEMPLATE.replace("OCCASION_PLACEHOLDER", occasion)

    try:
        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        return {
            "error": "Could not decode image",
            "details": str(e),
            "occasion_confirmed": occasion,
        }

    models_to_try = [ANALYSE_MODEL_PRIMARY, ANALYSE_MODEL_FALLBACK]
    response_text = None
    last_error = None

    for model in models_to_try:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[image, prompt],
                    config={"temperature": 0},
                )
                response_text = response.text
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
        return {
            "error": "All models failed",
            "details": str(last_error),
            "occasion_confirmed": occasion,
        }

    text = response_text.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    if text.startswith("```"):
        text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "error": "Could not parse image analysis",
            "raw": response_text,
            "occasion_confirmed": occasion,
        }


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if len(sys.argv) < 2:
        log.info("Usage: python analyse.py <image_path> [occasion]")
        log.info("Example: python analyse.py test/saree.jpg wedding")
        sys.exit(1)

    image_path = sys.argv[1]
    occasion = sys.argv[2] if len(sys.argv) > 2 else "festive"

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    log.info("Analysing outfit from: %s", image_path)
    log.info("Occasion: %s", occasion)
    log.info("Calling Gemini...")

    result = analyse_outfit(image_b64, occasion)
    log.info("%s", json.dumps(result, indent=2, ensure_ascii=False))
