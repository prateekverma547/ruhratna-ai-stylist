"""
Ruhratna AI Stylist — Flask REST API.

Wires analyse.py (image → outfit JSON) and match.py (outfit JSON → top 3
jewellery picks) into a single /style-match endpoint that the WordPress
frontend calls. Catalog is preloaded at startup so the first request
isn't slow.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from analyse import analyse_outfit
from match import get_product_count, match_jewellery, preload_catalog

load_dotenv()

app = Flask(__name__)
CORS(app)

PORT = int(os.getenv("PORT", 5000))
ANALYSE_MODEL = os.getenv("ANALYSE_MODEL_PRIMARY")
MATCH_MODEL = os.getenv("MATCH_MODEL_PRIMARY")

preload_catalog()


@app.route("/analyse", methods=["POST"])
def analyse():
    try:
        data = request.get_json(silent=True) or {}

        image_b64 = data.get("image")
        if not image_b64:
            return jsonify({"error": "image field is required"}), 400

        occasion = data.get("occasion", "festive")

        outfit_analysis = analyse_outfit(image_b64, occasion)
        if "error" in outfit_analysis:
            return jsonify(outfit_analysis), 400

        return jsonify({
            "success": True,
            "outfit_analysis": outfit_analysis,
        }), 200

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@app.route("/match", methods=["POST"])
def match():
    try:
        data = request.get_json(silent=True) or {}

        outfit_analysis = data.get("outfit_analysis")
        if not outfit_analysis:
            return jsonify({"error": "outfit_analysis field is required"}), 400

        occasion = data.get("occasion", "festive")

        match_result = match_jewellery(outfit_analysis, occasion)
        if "error" in match_result:
            return jsonify(match_result), 500

        return jsonify({
            "success": True,
            "stylist_reading": match_result.get("stylist_reading"),
            "recommendations": match_result.get("recommendations"),
            "complete_look": match_result.get("complete_look"),
        }), 200

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "analyse_model": ANALYSE_MODEL,
        "match_model": MATCH_MODEL,
        "products_loaded": get_product_count(),
    }), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=os.getenv("FLASK_ENV") == "development",
        threaded=True,
    )
