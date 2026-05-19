"""
Session logger for Ruhratna AI Stylist.

Uploads the outfit image to Google Drive and appends one row per completed
session to a Google Sheet. Fully non-blocking: log_session spawns a daemon
thread and returns immediately, so the user-facing response is never delayed
and Railway worker shutdown is never held up.
"""

import base64
import io
import json
import os
import threading
import traceback
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_TAB = "Sessions"

HEADER_ROW = [
    "Session ID",
    "Timestamp UTC",
    "Occasion",
    "Analyse Model",
    "Match Model",
    "Analyse Time (s)",
    "Match Time (s)",
    "Confidence Flag",
    "Num Recommendations",
    "Complete Look Suggested",
    "Stylist Reading (first 200 chars)",
    "Drive Image URL",
    "Outfit Analysis (JSON)",
    "Recommendations (JSON)",
]


def _get_credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _upload_image(drive, image_base64: str, session_id: str, folder_id: str) -> str:
    image_bytes = base64.b64decode(image_base64)
    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype="image/jpeg",
        resumable=False,
    )
    metadata = {"name": f"{session_id}.jpg", "parents": [folder_id]}
    created = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    file_id = created["id"]
    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    return created.get("webViewLink", "")


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


def _append_row(sheets, spreadsheet_id: str, row: list) -> None:
    sheets.values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{SHEET_TAB}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _do_log(
    session_id: str,
    occasion: str,
    image_base64: str,
    outfit_analysis: dict,
    match_result: dict,
    analyse_model: str,
    match_model: str,
    analyse_time: float,
    match_time: float,
    confidence_flag: str,
) -> None:
    try:
        folder_id = os.getenv("DRIVE_FOLDER_ID")
        sheets_id = os.getenv("SHEETS_ID")
        if not folder_id or not sheets_id:
            print("[logger] DRIVE_FOLDER_ID or SHEETS_ID not set — skipping session log")
            return

        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

        drive_url = _upload_image(drive, image_base64, session_id, folder_id)

        recommendations = match_result.get("recommendations", []) if isinstance(match_result, dict) else []
        complete_look = match_result.get("complete_look", {}) if isinstance(match_result, dict) else {}
        stylist_reading = match_result.get("stylist_reading", "") if isinstance(match_result, dict) else ""

        row = [
            session_id,
            datetime.now(timezone.utc).isoformat(),
            occasion,
            analyse_model,
            match_model,
            f"{analyse_time:.2f}",
            f"{match_time:.2f}",
            confidence_flag,
            len(recommendations),
            bool(complete_look.get("suggested", False)) if isinstance(complete_look, dict) else False,
            (stylist_reading or "")[:200],
            drive_url,
            json.dumps(outfit_analysis, ensure_ascii=False),
            json.dumps(recommendations, ensure_ascii=False),
        ]

        _ensure_header(sheets, sheets_id)
        _append_row(sheets, sheets_id, row)
        print(f"[logger] session {session_id} logged")
    except Exception as e:
        print(f"[logger] failed to log session {session_id}: {e}")
        traceback.print_exc()


def log_session(
    session_id: str,
    occasion: str,
    image_base64: str,
    outfit_analysis: dict,
    match_result: dict,
    analyse_model: str,
    match_model: str,
    analyse_time: float,
    match_time: float,
    confidence_flag: str,
) -> None:
    """Spawn a daemon thread to log the session. Returns immediately."""
    thread = threading.Thread(
        target=_do_log,
        args=(
            session_id,
            occasion,
            image_base64,
            outfit_analysis,
            match_result,
            analyse_model,
            match_model,
            analyse_time,
            match_time,
            confidence_flag,
        ),
        daemon=True,
    )
    thread.start()
