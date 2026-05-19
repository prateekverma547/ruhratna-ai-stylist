"""
Ruhratna AI Stylist — Flask REST API.

Wires analyse.py (image → outfit JSON) and match.py (outfit JSON → top
jewellery picks) into REST endpoints the WordPress frontend calls.
Catalog is preloaded at startup so the first request isn't slow.

/match runs asynchronously: POST returns a job_id immediately, then the
client polls GET /result/<job_id> until the job reports done or error.
Lets the API stay well under reverse-proxy timeouts on the slower
Gemini call.
"""

import os
import threading
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from analyse import analyse_outfit
from logger import log_session
from match import get_product_count, match_jewellery, preload_catalog

load_dotenv()

app = Flask(__name__)
CORS(app)

PORT = int(os.getenv("PORT", 5000))
ANALYSE_MODEL = os.getenv("ANALYSE_MODEL_PRIMARY")
MATCH_MODEL = os.getenv("MATCH_MODEL_PRIMARY")

preload_catalog()

jobs = {}
jobs_lock = threading.Lock()


def _run_match(
    job_id: str,
    outfit_analysis: dict,
    occasion: str,
    image_b64: str,
    analyse_time: float,
) -> None:
    """Background worker: runs match_jewellery, stores the outcome, and fires async session log."""
    try:
        match_start = time.time()
        result = match_jewellery(outfit_analysis, occasion)
        match_time = time.time() - match_start

        with jobs_lock:
            if "error" in result:
                jobs[job_id] = {
                    "status": "error",
                    "result": None,
                    "error": result.get("error"),
                }
            else:
                jobs[job_id] = {
                    "status": "done",
                    "result": result,
                    "error": None,
                }

        if "error" in result or not image_b64:
            return

        analyse_model = outfit_analysis.get("model_used", ANALYSE_MODEL) if isinstance(outfit_analysis, dict) else ANALYSE_MODEL
        match_model = result.get("model_used", MATCH_MODEL) if isinstance(result, dict) else MATCH_MODEL
        confidence_flag = outfit_analysis.get("confidence_flag", "ok") if isinstance(outfit_analysis, dict) else "ok"

        print(f"[logger] Calling log_session for job {job_id}", flush=True)
        import sys
        print("[logger] ABOUT TO CALL log_session", flush=True, file=sys.stderr)
        log_session(
            session_id=job_id,
            occasion=occasion,
            image_base64=image_b64,
            outfit_analysis=outfit_analysis,
            match_result=result,
            analyse_model=analyse_model,
            match_model=match_model,
            analyse_time=analyse_time,
            match_time=match_time,
            confidence_flag=confidence_flag,
        )
    except Exception as e:
        with jobs_lock:
            jobs[job_id] = {
                "status": "error",
                "result": None,
                "error": str(e),
            }


@app.route("/analyse", methods=["POST"])
def analyse():
    try:
        data = request.get_json(silent=True) or {}

        image_b64 = data.get("image")
        if not image_b64:
            return jsonify({"error": "image field is required"}), 400

        occasion = data.get("occasion", "festive")

        analyse_start = time.time()
        outfit_analysis = analyse_outfit(image_b64, occasion)
        analyse_elapsed = time.time() - analyse_start

        if "error" in outfit_analysis:
            return jsonify(outfit_analysis), 400

        if isinstance(outfit_analysis, dict):
            outfit_analysis["_analyse_time"] = analyse_elapsed

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
        image_b64 = data.get("image", "")
        analyse_time = 0.0
        if isinstance(outfit_analysis, dict):
            analyse_time = float(outfit_analysis.get("_analyse_time", 0.0) or 0.0)

        job_id = str(uuid.uuid4())
        with jobs_lock:
            jobs[job_id] = {"status": "running", "result": None, "error": None}

        thread = threading.Thread(
            target=_run_match,
            args=(job_id, outfit_analysis, occasion, image_b64, analyse_time),
            daemon=True,
        )
        thread.start()

        return jsonify({"job_id": job_id}), 202

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@app.route("/result/<job_id>", methods=["GET"])
def result(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404

        status = job["status"]

        if status == "running":
            return jsonify({"status": "running"}), 200

        if status == "done":
            response = {"status": "done", "result": job["result"]}
        else:
            response = {"status": "error", "error": job["error"]}

        del jobs[job_id]
        return jsonify(response), 200


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
