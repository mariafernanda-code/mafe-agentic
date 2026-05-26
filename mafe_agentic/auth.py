"""
Autenticación con Google Workspace vía Workload Identity Federation + DWD.

Mafe Agentic NO usa llaves JSON descargadas. En su lugar:

  1. Railway emite un OIDC token corto para cada deployment (no es secreto).
  2. GCP intercambia ese token por credenciales temporales vía el
     Workload Identity Pool configurado.
  3. Esas credenciales tienen permiso (workloadIdentityUser +
     serviceAccountTokenCreator) para impersonar mafe-agentic-sa.
  4. Para cada request, impersonamos al usuario final via DWD (subject).

Cero llaves estáticas. Tokens temporales (~1h). Audit logs nativos en
Cloud Audit Logs. Imposible filtrar credenciales permanentes — no existen.

Variables de entorno (Railway):
    GOOGLE_APPLICATION_CREDENTIALS  — ruta al archivo .json de config WIF
                                       (NO es secreto, solo dice "para
                                       autenticarte, usa el endpoint X")
    MAFE_TARGET_SA                   — email del SA que impersonamos
    MAFE_DEFAULT_USER                — correo opcional para testing local

En desarrollo local funciona igual con `gcloud auth application-default login`.
"""

from __future__ import annotations

import os
from functools import lru_cache

import google.auth
from google.auth import impersonated_credentials


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


@lru_cache(maxsize=1)
def _source_credentials():
    """
    Credenciales source obtenidas por Application Default Credentials.

    En Railway, ADC detecta el archivo apuntado por GOOGLE_APPLICATION_CREDENTIALS
    (configuración WIF, no llave) y arma el flujo de intercambio OIDC.

    En local, ADC usa el login de gcloud del developer.
    """
    creds, _ = google.auth.default()
    return creds


def credentials_for(user_email: str | None):
    """
    Devuelve credenciales temporales que actúan como `user_email`.

    Flujo de dos saltos:
      WIF source creds  →  impersona mafe-agentic-sa  →  DWD a user_email

    El resultado es un OAuth token temporal con los scopes de Mafe Agentic,
    actuando como user_email. Sin llaves estáticas en ningún lado.
    """
    if not user_email:
        user_email = os.environ.get("MAFE_DEFAULT_USER")
    if not user_email:
        raise RuntimeError(
            "No sé como qué usuario actuar. Pásame el correo en el header "
            "X-Mafe-User-Email o configura MAFE_DEFAULT_USER."
        )

    source = _source_credentials()

    creds = impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=TARGET_SA,
        target_scopes=SCOPES,
        delegates=[],
        subject=user_email,
    )
    return creds
