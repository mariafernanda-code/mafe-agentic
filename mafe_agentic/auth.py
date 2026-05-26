"""
Autenticación con Google Workspace.

Dos modos:
  1. JSON key (modo simple, actual): GOOGLE_SERVICE_ACCOUNT_JSON con el contenido
     completo del JSON del service account. Usa DWD via subject.
  2. WIF (modo seguro, futuro): Workload Identity Federation con ADC.

En desarrollo local funciona igual con `gcloud auth application-default login`.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

import google.auth
from google.auth import impersonated_credentials
from google.oauth2 import service_account

log = logging.getLogger(__name__)


# Scopes amplios para Drive (full), Slides, Sheets, Calendar (events + readonly).
# IMPORTANTE: estos scopes deben estar autorizados en Domain-Wide Delegation
# desde Google Workspace Admin (Security → API Controls → Domain-wide delegation)
# para el Client ID del service account. Sin esa autorización, falla con 403.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

TARGET_SA = os.environ.get(
    "MAFE_TARGET_SA",
    "mafe-agentic-sa@numeric-mile-496216-q7.iam.gserviceaccount.com",
)


def _has_json_key() -> bool:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    return bool(raw) and len(raw) > 50  # JSON key real es ~2KB


@lru_cache(maxsize=1)
def _service_account_info() -> dict:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    log.info("Cargando JSON del service account (len=%d)", len(raw))
    try:
        info = json.loads(raw)
        # Validar campos críticos
        if "private_key" not in info or "client_email" not in info:
            log.error(
                "JSON del service account incompleto. Keys presentes: %s",
                list(info.keys()),
            )
            raise ValueError(
                "El JSON del service account no tiene private_key o client_email. "
                "Verifica que pegaste el JSON completo en GOOGLE_SERVICE_ACCOUNT_JSON."
            )
        log.info(
            "JSON parseado OK. client_email=%s key_id=%s",
            info.get("client_email"),
            info.get("private_key_id", "?")[:10],
        )
        return info
    except json.JSONDecodeError as e:
        log.error(
            "GOOGLE_SERVICE_ACCOUNT_JSON está malformado: %s. "
            "Primeros 80 chars: %r",
            e, raw[:80],
        )
        raise


@lru_cache(maxsize=1)
def _source_credentials():
    """Credenciales source via ADC (cuando usamos WIF)."""
    creds, _ = google.auth.default()
    return creds


def credentials_for(user_email: str | None):
    """
    Devuelve credenciales temporales que actúan como `user_email`.

    Modos:
      1. JSON key: si GOOGLE_SERVICE_ACCOUNT_JSON está set y es válido,
         usa el JSON del service account con DWD via subject.
      2. WIF: si no, usa Application Default Credentials con
         impersonated_credentials.
    """
    if not user_email:
        user_email = os.environ.get("MAFE_DEFAULT_USER")
    if not user_email:
        raise RuntimeError(
            "No sé como qué usuario actuar. Pásame el correo en el header "
            "X-Mafe-User-Email o configura MAFE_DEFAULT_USER."
        )

    if _has_json_key():
        info = _service_account_info()
        log.info("Auth via JSON key, impersonando a %s", user_email)
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES, subject=user_email
        )

    # Modo WIF
    log.info("Auth via WIF, impersonando a %s", user_email)
    source = _source_credentials()
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=TARGET_SA,
        target_scopes=SCOPES,
        delegates=[],
        subject=user_email,
    )
