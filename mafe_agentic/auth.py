from __future__ import annotations

import json
import os
from functools import lru_cache

import google.auth
from google.auth import impersonated_credentials
from google.oauth2 import service_account


SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/calendar",
]

TARGET_SA = os.environ.get(
    "MAFE_TARGET_SA",
    "mafe-agentic-sa@numeric-mile-496216-q7.iam.gserviceaccount.com",
)


def _has_json_key():
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))


@lru_cache(maxsize=1)
def _service_account_info():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    return json.loads(raw)


@lru_cache(maxsize=1)
def _source_credentials():
    creds, _ = google.auth.default()
    return creds


def credentials_for(user_email):
    if not user_email:
        user_email = os.environ.get("MAFE_DEFAULT_USER")
    if not user_email:
        raise RuntimeError(
            "No se como que usuario actuar. Pasa el correo en X-Mafe-User-Email o MAFE_DEFAULT_USER."
        )

    if _has_json_key():
        info = _service_account_info()
