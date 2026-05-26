"""
Cliente de Google Drive para Mafe Agentic.

Sube archivos al Drive del usuario que solicitó la acción (gracias a
Domain-Wide Delegation: el service account impersona al usuario).

Cada usuario tiene su propia carpeta "Mafe Agentic" en su Drive
personal donde se guardan los archivos generados.
"""

from __future__ import annotations

import threading
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from mafe_agentic.auth import credentials_for


MAFE_FOLDER_NAME = "Mafe Agentic"

# Cache de folder IDs por usuario, thread-safe
_folder_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


CONVERT_TO_GOOGLE = {
    ".pptx": "application/vnd.google-apps.presentation",
    ".xlsx": "application/vnd.google-apps.spreadsheet",
    ".docx": "application/vnd.google-apps.document",
}

SOURCE_MIME = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}


def _drive_service(user_email: str | None):
    creds = credentials_for(user_email)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_folder(svc, user_email: str) -> str:
    """Encuentra o crea la carpeta 'Mafe Agentic' en el Drive del usuario."""
    with _cache_lock:
        if user_email in _folder_cache:
            return _folder_cache[user_email]

    query = (
        f"name='{MAFE_FOLDER_NAME}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    res = svc.files().list(
        q=query, spaces="drive", fields="files(id, name)", pageSize=1
    ).execute()
    items = res.get("files", [])

    if items:
        folder_id = items[0]["id"]
    else:
        folder = svc.files().create(
            body={
                "name": MAFE_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        ).execute()
        folder_id = folder["id"]

    with _cache_lock:
        _folder_cache[user_email] = folder_id
    return folder_id


def upload(
    local_path: str | Path,
    drive_filename: str,
    user_email: str,
    convert_to_google: bool = True,
) -> dict:
    """
    Sube un archivo local al Drive del usuario indicado.

    Si convert_to_google=True y la extensión es .pptx/.xlsx/.docx,
    lo convierte al formato nativo de Google (Slides/Sheets/Docs).

    Devuelve: {id, name, url, mime}
    """
    local = Path(local_path)
    if not local.exists():
        raise FileNotFoundError(f"No encontré el archivo local: {local}")

    ext = local.suffix.lower()
    source_mime = SOURCE_MIME.get(ext, "application/octet-stream")

    svc = _drive_service(user_email)
    folder_id = _ensure_folder(svc, user_email)

    metadata: dict = {
        "name": drive_filename,
        "parents": [folder_id],
    }
    if convert_to_google and ext in CONVERT_TO_GOOGLE:
        metadata["mimeType"] = CONVERT_TO_GOOGLE[ext]

    media = MediaFileUpload(str(local), mimetype=source_mime, resumable=False)

    file = svc.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, webViewLink, mimeType",
    ).execute()

    return {
        "id": file.get("id"),
        "name": file.get("name"),
        "url": file.get("webViewLink"),
        "mime": file.get("mimeType"),
    }
