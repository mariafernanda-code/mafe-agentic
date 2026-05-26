"""
Herramientas de Google Drive para Mafe Agentic.

- create_slides_from_template: copia una plantilla de Google Slides y
  reemplaza placeholders. Esto es lo más poderoso — Mafe respeta el
  branding/diseño y solo cambia el contenido.
- create_slides_from_scratch: deck nuevo desde cero (usa pptx generator)
- create_sheets: hoja nueva con datos (usa xlsx generator)
- create_chart: gráfico PNG en Drive
- create_diagram: diagrama de flujo PNG en Drive
"""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from pathlib import Path

from googleapiclient.discovery import build

from mafe_agentic import drive as drive_client
from mafe_agentic.auth import credentials_for
from mafe_agentic.generators import chart, diagram, pptx, xlsx

log = logging.getLogger(__name__)


# ----- Helpers -----

def _temp_path(prefix: str, extension: str) -> Path:
    safe = "".join(c for c in prefix if c.isalnum() or c in "-_") or "mafe"
    tmp_dir = Path(tempfile.gettempdir()) / "mafe-agentic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / f"{safe}-{uuid.uuid4().hex[:8]}.{extension}"


def _extract_slides_id(template: str) -> str:
    """
    Extrae el ID de Google Slides de cualquier formato común:
    - URL completa: https://docs.google.com/presentation/d/ABC123/edit
    - URL corta: docs.google.com/presentation/d/ABC123
    - Solo ID: ABC123
    """
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", template)
    if m:
        return m.group(1)
    # Si no hay /d/, asumir que es el ID directo
    return template.strip()


# ----- Tool: Slides desde plantilla -----

async def create_slides_from_template(
    *, user_email: str, template: str, replacements: dict, new_name: str | None = None
) -> dict:
    """
    Copia una plantilla de Google Slides en el Drive del usuario y
    reemplaza placeholders por valores reales.

    template: URL o ID de la presentación plantilla (debe ser accesible para el usuario)
    replacements: dict {placeholder: valor_real}. Los placeholders en la plantilla
                  pueden estar como {{nombre}} o como texto literal.
    new_name: nombre del archivo nuevo. Si no se pasa, agrega ' — copia' al original.
    """
    template_id = _extract_slides_id(template)
    creds = credentials_for(user_email)
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    slides_svc = build("slides", "v1", credentials=creds, cache_discovery=False)

    # 1. Leer metadata de la plantilla
    orig = drive_svc.files().get(
        fileId=template_id, fields="name, mimeType"
    ).execute()
    if orig.get("mimeType") != "application/vnd.google-apps.presentation":
        raise ValueError(
            f"El archivo {template_id} no es una presentación de Google Slides."
        )

    # 2. Asegurar carpeta destino
    folder_id = drive_client._ensure_folder(drive_svc, user_email)

    # 3. Copiar la plantilla a la carpeta del usuario
    copy_name = new_name or f"{orig['name']} — copia"
    copy = drive_svc.files().copy(
        fileId=template_id,
        body={"name": copy_name, "parents": [folder_id]},
        fields="id, name, webViewLink",
    ).execute()
    new_id = copy["id"]

    # 4. Reemplazar placeholders en la copia
    requests = []
    for placeholder, value in replacements.items():
        # Acepta tanto {{nombre}} como texto literal
        for variant in (f"{{{{{placeholder}}}}}", placeholder):
            requests.append({
                "replaceAllText": {
                    "containsText": {"text": variant, "matchCase": False},
                    "replaceText": str(value),
                }
            })

    if requests:
        slides_svc.presentations().batchUpdate(
            presentationId=new_id,
            body={"requests": requests},
        ).execute()

    log.info(
        "Slides from template: %s → %s para %s",
        template_id, new_id, user_email
    )
    return {
        "id": new_id,
        "name": copy["name"],
        "url": copy["webViewLink"],
        "replacements_applied": len(replacements),
    }


# ----- Tools wrapper: presentación desde cero -----

async def create_slides_from_scratch(
    *, user_email: str, title: str, slides: list, subtitle: str | None = None
) -> dict:
    """Crea presentación nueva desde cero usando el generator pptx."""
    local_path = _temp_path(title or "presentacion", "pptx")
    try:
        spec = {
            "title": title,
            "subtitle": subtitle,
            "slides": slides,
        }
        pptx.build(spec, str(local_path))
        info = drive_client.upload(
            local_path,
            title or "Presentación",
            user_email=user_email,
            convert_to_google=True,
        )
        return info
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


# ----- Tool: Sheets -----

async def create_sheets(
    *, user_email: str, sheets: list, filename: str | None = None
) -> dict:
    """Crea Google Sheets con hojas y datos."""
    local_path = _temp_path(filename or "datos", "xlsx")
    try:
        xlsx.build({"sheets": sheets}, str(local_path))
        info = drive_client.upload(
            local_path,
            filename or "Datos",
            user_email=user_email,
            convert_to_google=True,
        )
        return info
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


# ----- Tool: gráfico -----

async def create_chart(
    *, user_email: str, type: str, labels: list, values: list,
    title: str | None = None, x_label: str | None = None,
    y_label: str | None = None, filename: str | None = None
) -> dict:
    """Crea gráfico PNG en Drive."""
    local_path = _temp_path(filename or "grafico", "png")
    try:
        chart.build({
            "type": type, "labels": labels, "values": values,
            "title": title, "x_label": x_label, "y_label": y_label,
        }, str(local_path))
        info = drive_client.upload(
            local_path,
            filename or title or "Gráfico",
            user_email=user_email,
            convert_to_google=False,
        )
        return info
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


# ----- Tool: diagrama -----

async def create_diagram(
    *, user_email: str, nodes: list, edges: list,
    title: str | None = None, filename: str | None = None
) -> dict:
    """Crea diagrama de flujo PNG en Drive."""
    local_path = _temp_path(filename or "diagrama", "png")
    try:
        # Convertir edges con from/to
        edge_specs = [
            {"from": e.get("from") or e.get("from_node"), "to": e["to"], "label": e.get("label")}
            for e in edges
        ]
        diagram.build({
            "title": title,
            "nodes": nodes,
            "edges": edge_specs,
        }, str(local_path))
        info = drive_client.upload(
            local_path,
            filename or title or "Diagrama",
            user_email=user_email,
            convert_to_google=False,
        )
        return info
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


# ----- Specs en formato Anthropic tool use -----

SPECS = [
    {
        "name": "drive_slides_from_template",
        "description": (
            "Copia una plantilla de Google Slides en el Drive del usuario y reemplaza placeholders "
            "con valores reales. Úsalo cuando el usuario pide una propuesta o presentación basada en "
            "una plantilla específica de Golden Gate Grid o de un cliente. La plantilla debe ser un "
            "Google Slides accesible. Los placeholders en la plantilla pueden estar como {{nombre}} "
            "o como texto literal que se va a reemplazar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "URL completa o ID del Google Slides plantilla",
                },
                "replacements": {
                    "type": "object",
                    "description": "Dict de placeholder→valor (ej: {\"CLIENTE\": \"Tiendas Neto\", \"FECHA\": \"27 mayo 2026\"})",
                    "additionalProperties": {"type": "string"},
                },
                "new_name": {
                    "type": "string",
                    "description": "Nombre del archivo copiado. Si se omite, se agrega ' — copia' al nombre original.",
                },
            },
            "required": ["template", "replacements"],
        },
    },
    {
        "name": "drive_slides_from_scratch",
        "description": (
            "Crea una presentación de Google Slides nueva desde cero (sin plantilla). "
            "Úsalo solo cuando el usuario no te da una plantilla. Cada slide puede tener "
            "heading, bullets, notas del presentador y un gráfico embebido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título de la portada"},
                "subtitle": {"type": "string", "description": "Subtítulo opcional"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "notes": {"type": "string"},
                            "chart": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["bar", "line", "pie"]},
                                    "labels": {"type": "array", "items": {"type": "string"}},
                                    "values": {"type": "array", "items": {"type": "number"}},
                                    "title": {"type": "string"},
                                },
                            },
                        },
                        "required": ["heading"],
                    },
                },
            },
            "required": ["title", "slides"],
        },
    },
    {
        "name": "drive_sheets",
        "description": "Crea un Google Sheets nuevo con una o más hojas, encabezados y datos. Opcional fila TOTAL con SUM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Nombre del archivo"},
                "sheets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "headers": {"type": "array", "items": {"type": "string"}},
                            "rows": {"type": "array", "items": {"type": "array"}},
                            "totals_row": {"type": "boolean"},
                        },
                        "required": ["name", "headers", "rows"],
                    },
                },
            },
            "required": ["sheets"],
        },
    },
    {
        "name": "drive_chart",
        "description": "Genera un gráfico PNG (bar/line/pie) y lo guarda en el Drive del usuario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bar", "line", "pie"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "values": {"type": "array", "items": {"type": "number"}},
                "title": {"type": "string"},
                "x_label": {"type": "string"},
                "y_label": {"type": "string"},
                "filename": {"type": "string"},
            },
            "required": ["type", "labels", "values"],
        },
    },
    {
        "name": "drive_diagram",
        "description": "Genera un diagrama de flujo PNG (cajas, rombos, elipses, flechas) y lo guarda en el Drive del usuario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "filename": {"type": "string"},
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "shape": {"type": "string", "enum": ["box", "ellipse", "diamond"]},
                        },
                        "required": ["id", "label"],
                    },
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["from", "to"],
                    },
                },
            },
            "required": ["nodes", "edges"],
        },
    },
]


DISPATCH = {
    "drive_slides_from_template": create_slides_from_template,
    "drive_slides_from_scratch": create_slides_from_scratch,
    "drive_sheets": create_sheets,
    "drive_chart": create_chart,
    "drive_diagram": create_diagram,
}
