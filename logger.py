"""
Two-stage session logger for Ruhratna AI Stylist.

Stage 1 (log_analyse_session): /analyse uploads the outfit image to imgbb
and appends a new Sessions row with the analyse-side columns filled and
the match-side columns left blank.

Stage 2 (log_match_session): /match looks up the row by session_id and
updates only the match-side columns in place.

Both are fully non-blocking: each public function spawns a daemon thread
and returns immediately. All exceptions are caught and logged — never
raised to the caller. Logging never delays the user response or holds
up Railway worker shutdown.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"

SHEET_TAB = "Sessions"

# Column layout — analyse-side first (A–H), then match-side (I–N).
# log_match_session writes only I–N, keyed by session_id in column A.
HEADER_ROW = [
    "Session ID",                          # A
    "Timestamp UTC",                       # B
    "Occasion",                            # C
    "Analyse Model",                       # D
    "Analyse Time (s)",                    # E
    "Confidence Flag",                     # F
    "Outfit Analysis (JSON)",              # G
    "Image URL",                           # H
    "Match Model",                         # I
    "Match Time (s)",                      # J
    "Num Recommendations",                 # K
    "Complete Look Suggested",             # L
    "Stylist Reading (first 200 chars)",   # M
    "Recommendations (JSON)",              # N
]


def _get_credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _sheets_client():
    creds = _get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()


def _upload_to_imgbb(image_base64: str, session_id: str) -> str:
    """POST the base64 image to imgbb. Returns the direct URL, or "" on failure."""
    try:
        api_key = os.getenv("IMGBB_API_KEY")
        if not api_key:
            log.warning("IMGBB_API_KEY not set — skipping image upload")
            return ""
        response = requests.post(
            IMGBB_UPLOAD_URL,
            params={"key": api_key, "name": session_id},
            data={"image": image_base64},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["data"]["url"]
    except Exception:
        log.warning("imgbb upload failed for session %s", session_id, exc_info=True)
        return ""


def _ensure_header(sheets, spreadsheet_id: str) -> None:
    response = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TAB}!A1:N1",
    ).execute()
    if not response.get("values"):
        sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW",
            body={"values": [HEADER_ROW]},
        ).execute()


def _find_row_by_session_id(sheets, spreadsheet_id: str, session_id: str):
    response = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TAB}!A:A",
    ).execute()
    values = response.get("values", [])
    for idx, row in enumerate(values, start=1):
        if row and row[0] == session_id:
            return idx
    return None


def _do_log_analyse(
    session_id: str,
    occasion: str,
    image_base64: str,
    outfit_analysis: dict,
    analyse_model: str,
    analyse_time: float,
    confidence_flag: str,
) -> None:
    log.info("_do_log_analyse started for session %s", session_id)
    try:
        sheets_id = os.getenv("SHEETS_ID")
        if not sheets_id:
            log.error("SHEETS_ID not set — skipping analyse log")
            return

        sheets = _sheets_client()
        log.info("Credentials loaded successfully")

        log.info("Attempting imgbb upload...")
        image_url = _upload_to_imgbb(image_base64, session_id)
        log.info("imgbb upload result: %s", image_url or "(empty — see warning above)")

        _ensure_header(sheets, sheets_id)

        # Only the analyse-side columns (A–H). I–N stay blank until /match.
        row = [
            session_id,
            datetime.now(timezone.utc).isoformat(),
            occasion,
            analyse_model,
            f"{analyse_time:.2f}",
            confidence_flag,
            json.dumps(outfit_analysis, ensure_ascii=False),
            image_url,
        ]

        log.info("Attempting Sheets append (analyse row)...")
        sheets.values().append(
            spreadsheetId=sheets_id,
            range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        log.info("analyse session %s logged", session_id)
    except Exception:
        log.exception("failed to log analyse session %s", session_id)


def _do_log_match(
    session_id: str,
    match_result: dict,
    match_model: str,
    match_time: float,
) -> None:
    log.info("_do_log_match started for session %s", session_id)
    try:
        sheets_id = os.getenv("SHEETS_ID")
        if not sheets_id:
            log.error("SHEETS_ID not set — skipping match log")
            return

        sheets = _sheets_client()
        log.info("Credentials loaded successfully")

        row_number = _find_row_by_session_id(sheets, sheets_id, session_id)
        if row_number is None:
            log.warning("session_id %s not found in %s — skipping match update", session_id, SHEET_TAB)
            return

        recommendations = match_result.get("recommendations", []) if isinstance(match_result, dict) else []
        complete_look = match_result.get("complete_look", {}) if isinstance(match_result, dict) else {}
        stylist_reading = match_result.get("stylist_reading", "") if isinstance(match_result, dict) else ""

        match_row = [
            match_model,
            f"{match_time:.2f}",
            len(recommendations),
            bool(complete_look.get("suggested", False)) if isinstance(complete_look, dict) else False,
            (stylist_reading or "")[:200],
            json.dumps(recommendations, ensure_ascii=False),
        ]

        log.info("Attempting Sheets update (match row %d)...", row_number)
        sheets.values().update(
            spreadsheetId=sheets_id,
            range=f"{SHEET_TAB}!I{row_number}:N{row_number}",
            valueInputOption="RAW",
            body={"values": [match_row]},
        ).execute()
        log.info("match session %s logged at row %d", session_id, row_number)
    except Exception:
        log.exception("failed to log match session %s", session_id)


def log_analyse_session(
    session_id: str,
    occasion: str,
    image_base64: str,
    outfit_analysis: dict,
    analyse_model: str,
    analyse_time: float,
    confidence_flag: str,
) -> None:
    """Stage 1 — Drive upload + new Sessions row. Daemon thread, returns immediately."""
    log.info("log_analyse_session() spawning thread for %s", session_id)
    threading.Thread(
        target=_do_log_analyse,
        args=(
            session_id,
            occasion,
            image_base64,
            outfit_analysis,
            analyse_model,
            analyse_time,
            confidence_flag,
        ),
        daemon=True,
    ).start()


def log_match_session(
    session_id: str,
    match_result: dict,
    match_model: str,
    match_time: float,
) -> None:
    """Stage 2 — in-place update of the match columns on the existing row. Daemon thread."""
    log.info("log_match_session() spawning thread for %s", session_id)
    threading.Thread(
        target=_do_log_match,
        args=(session_id, match_result, match_model, match_time),
        daemon=True,
    ).start()
