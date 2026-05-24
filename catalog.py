"""
Catalog preparation script for Ruhratna AI Stylist.

Run daily via cron (or manually for POC). Reads the product XML feed,
filters out unwanted items, and writes two artifacts the API server reads:

  catalog/catalog_products.json  — list of product dicts
  catalog/catalog_formatted.txt  — pre-built LLM-ready string

Not imported by the API server. Standalone.
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

import requests

log = logging.getLogger(__name__)

SOURCE_PATH = "https://ruhratna.com/wp-content/uploads/woo-feed/chatgpt/xml/recommendationeng.xml"
OUTPUT_JSON = "catalog/catalog_products.json"
OUTPUT_TEXT = "catalog/catalog_formatted.txt"

EXCLUDED_CATEGORY_TOKENS = ("Gift Cards", "Hot Pick Any 5 @ 1999", "Combo")


def load_source(source: str) -> str:
    """Load XML payload from a URL or local file path."""
    if source.startswith("http://") or source.startswith("https://"):
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        return response.text
    with open(source, "r", encoding="utf-8") as f:
        return f.read()


def _text(product: ET.Element, tag: str) -> str:
    node = product.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _parse_price(raw: str) -> Optional[float]:
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace(" INR", "").replace("INR", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_excluded(category_string: str, availability: str) -> bool:
    if availability.lower() != "in stock":
        return True
    return any(token in category_string for token in EXCLUDED_CATEGORY_TOKENS)


def parse_products(xml_text: str) -> list[dict]:
    """Parse the XML feed into a list of cleaned product dicts."""
    root = ET.fromstring(xml_text)
    products: list[dict] = []

    for node in root.findall("product"):
        category_string = _text(node, "product_category")
        availability = _text(node, "availability")

        if _is_excluded(category_string, availability):
            continue

        price = _parse_price(_text(node, "price"))
        if price is None:
            continue

        categories = [c.strip() for c in category_string.split(">") if c.strip()]

        products.append({
            "id": _text(node, "id"),
            "title": _text(node, "title"),
            "description": _text(node, "description"),
            "categories": categories,
            "category_string": category_string,
            "price": price,
            "image_url": _text(node, "image_link"),
            "product_url": _text(node, "link"),
        })

    return products


def format_for_llm(products: list[dict]) -> str:
    """Render products as a single LLM-friendly string."""
    blocks = []
    for p in products:
        blocks.append(
            f"[ID: {p['id']}]\n"
            f"Title: {p['title']}\n"
            f"Description: {p['description']}\n"
            f"Categories: {p['category_string']}\n"
            f"Price: ₹{int(p['price'])}"
        )
    return "\n\n---\n\n".join(blocks)


def main() -> None:
    xml_text = load_source(SOURCE_PATH)
    products = parse_products(xml_text)

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_TEXT, "w", encoding="utf-8") as f:
        f.write(format_for_llm(products))

    log.info("Catalog ready: %d products", len(products))
    log.info("Saved: %s", OUTPUT_JSON)
    log.info("Saved: %s", OUTPUT_TEXT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
