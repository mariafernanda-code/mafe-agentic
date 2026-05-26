#!/usr/bin/env python3
"""
Mafe Agentic — MCP server hospedado y multi-usuario.

Hospedado en Railway, accesible vía HTTP desde cualquier cliente MCP
(Cowork, Claude Desktop, Claude Code, conectores de Agentforce/Slack).

Multi-usuario: usa Service Account + Domain-Wide Delegation. Cuando un
request llega, identifica al usuario por el header X-Mafe-User-Email
y actúa como esa persona. Los archivos quedan en el Drive de quien
los pidió.

Variables de entorno (Railway):
    GOOGLE_SERVICE_ACCOUNT_JSON  — JSON completo del service account (obligatorio)
    MAFE_DEFAULT_USER            — correo a impersonar si el request no trae header (opcional)
    PORT                          — Railway lo inyecta automáticamente
    HOST                          — 0.0.0.0 por default
"""

from __future__ import annotations

import contextvars
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mafe_agentic import drive
from mafe_agentic.generators import chart, diagram, pptx, xlsx


# ----- Contexto por request: usuario actual -----

current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user", default=None
)


class UserContextMiddleware(BaseHTTPMiddleware):
    """Extrae el usuario del header X-Mafe-User-Email y lo pone en contexto."""

    async def dispatch(self, request: Request, call_next):
        user = request.headers.get("x-mafe-user-email") or request.headers.get(
            "x-forwarded-user"
        )
        token = current_user.set(user)
        try:
            response = await call_next(request)
        finally:
            current_user.reset(token)
        return response


def _user() -> str | None:
    """Recupera el usuario del contexto actual."""
    return current_user.get()


# ----- MCP server -----

mcp = FastMCP(
    "mafe_agentic",
    instructions=(
        "Mafe Agentic crea presentaciones, hojas de cálculo, gráficos y "
        "diagramas en el Google Drive del usuario que la invoca. Pásale el "
        "correo del usuario en el header X-Mafe-User-Email para que sepa "
        "en qué Drive crear las cosas."
    ),
)


# ----- Helpers -----

def _temp_path(prefix: str, extension: str) -> Path:
    safe = "".join(c for c in prefix if c.isalnum() or c in "-_") or "mafe"
    tmp_dir = Path(tempfile.gettempdir()) / "mafe-agentic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / f"{safe}-{uuid.uuid4().hex[:8]}.{extension}"


def _ok(tipo: str, info: dict, extra: str = "") -> str:
    nota = f"\n{extra}" if extra else ""
    return (
        f"Listo. Te dejé el {tipo} en tu Drive:\n\n"
        f"  {info['name']}\n"
        f"  {info['url']}\n"
        f"{nota}\n\n"
        f"Está en la carpeta 'Mafe Agentic' de tu Drive."
    )


def _ups(action: str, e: Exception) -> str:
    return (
        f"Uy, algo se me atravesó {action}. "
        f"El detalle técnico: {type(e).__name__}: {e}. "
        f"Si quieres lo intentamos de otra forma, dime."
    )


# ----- Modelos de input -----

class ChartInSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "pie"] = Field(description="Tipo de gráfico")
    labels: list[str] = Field(description="Etiquetas")
    values: list[float] = Field(description="Valores numéricos")
    title: str = Field(default="", description="Título opcional")


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(description="Título de la slide")
    bullets: list[str] | None = Field(default=None, description="Bullets, uno por idea")
    notes: str | None = Field(default=None, description="Notas del presentador")
    chart: ChartInSlide | None = Field(default=None, description="Gráfico embebido opcional")


class GenerarPresentacionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(description="Título de la portada", min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, description="Subtítulo opcional")
    slides: list[Slide] = Field(description="Slides en orden", min_length=1, max_length=40)
    filename_hint: str | None = Field(default=None, description="Sugerencia de nombre")


class Sheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Nombre de la pestaña", max_length=31)
    headers: list[str] = Field(description="Encabezados de columnas")
    rows: list[list[Any]] = Field(description="Filas de datos")
    totals_row: bool = Field(default=False, description="Si True, agrega fila TOTAL con SUM")


class GenerarSheetsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sheets: list[Sheet] = Field(description="Hojas a crear", min_length=1, max_length=20)
    filename_hint: str | None = Field(default=None, description="Sugerencia de nombre")


class GenerarGraficoInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    type: Literal["bar", "line", "pie"] = Field(description="bar / line / pie")
    labels: list[str] = Field(description="Etiquetas")
    values: list[float] = Field(description="Valores numéricos")
    title: str | None = Field(default=None, description="Título")
    x_label: str | None = Field(default=None, description="Eje X (no aplica a pie)")
    y_label: str | None = Field(default=None, description="Eje Y (no aplica a pie)")
    filename_hint: str | None = Field(default=None, description="Sugerencia de nombre")


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Identificador único")
    label: str = Field(description="Texto del nodo")
    shape: Literal["box", "ellipse", "diamond"] = Field(default="box")


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_node: str = Field(alias="from", description="ID nodo origen")
    to: str = Field(description="ID nodo destino")
    label: str | None = Field(default=None, description="Texto sobre la flecha")


class GenerarDiagramaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", populate_by_name=True)
    title: str | None = Field(default=None, description="Título del diagrama")
    nodes: list[FlowNode] = Field(description="Pasos/decisiones", min_length=1, max_length=50)
    edges: list[FlowEdge] = Field(description="Conexiones", min_length=1, max_length=100)
    filename_hint: str | None = Field(default=None, description="Sugerencia de nombre")


# ----- Tools -----

@mcp.tool(
    name="generar_presentacion",
    annotations={
        "title": "Generar presentación en Google Slides",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generar_presentacion(params: GenerarPresentacionInput) -> str:
    """
    Crea una presentación nativa de Google Slides en el Drive del usuario.

    Cada slide puede llevar heading, bullets, notas del presentador y un
    gráfico opcional embebido. Bonita y lista para presentar.
    """
    user = _user()
    local_path = None
    try:
        local_path = _temp_path(params.filename_hint or "presentacion", "pptx")
        spec = {
            "title": params.title,
            "subtitle": params.subtitle,
            "slides": [
                {
                    "heading": s.heading,
                    "bullets": s.bullets,
                    "notes": s.notes,
                    "chart": s.chart.model_dump() if s.chart else None,
                }
                for s in params.slides
            ],
        }
        pptx.build(spec, str(local_path))
        info = drive.upload(
            local_path,
            params.filename_hint or params.title or "Presentación",
            user_email=user,
            convert_to_google=True,
        )
        return _ok("deck", info, f"Total: {len(params.slides)} slides en Google Slides.")
    except Exception as e:
        return _ups("armando la presentación", e)
    finally:
        if local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


@mcp.tool(
    name="generar_sheets",
    annotations={
        "title": "Generar Google Sheets",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generar_sheets(params: GenerarSheetsInput) -> str:
    """
    Crea un Google Sheets nativo con varias hojas, encabezados con formato
    y opcionalmente fila TOTAL con fórmulas SUM. Va al Drive del usuario.
    """
    user = _user()
    local_path = None
    try:
        local_path = _temp_path(params.filename_hint or "datos", "xlsx")
        spec = {"sheets": [s.model_dump() for s in params.sheets]}
        xlsx.build(spec, str(local_path))
        info = drive.upload(
            local_path,
            params.filename_hint or "Datos",
            user_email=user,
            convert_to_google=True,
        )
        sheet_names = ", ".join(s.name for s in params.sheets)
        return _ok("Sheets", info, f"Hojas: {sheet_names}.")
    except Exception as e:
        return _ups("armando el Sheets", e)
    finally:
        if local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


@mcp.tool(
    name="generar_grafico",
    annotations={
        "title": "Generar gráfico PNG en Drive",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generar_grafico(params: GenerarGraficoInput) -> str:
    """
    Genera un gráfico PNG (bar/line/pie) y lo sube al Drive del usuario.
    Sirve para insertar en presentaciones, correos o documentos.
    """
    user = _user()
    local_path = None
    try:
        local_path = _temp_path(params.filename_hint or "grafico", "png")
        chart.build(params.model_dump(exclude={"filename_hint"}), str(local_path))
        info = drive.upload(
            local_path,
            params.filename_hint or params.title or "Gráfico",
            user_email=user,
            convert_to_google=False,
        )
        return _ok(f"gráfico {params.type}", info)
    except Exception as e:
        return _ups("haciendo el gráfico", e)
    finally:
        if local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


@mcp.tool(
    name="generar_diagrama_flujo",
    annotations={
        "title": "Generar diagrama de flujo PNG en Drive",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generar_diagrama_flujo(params: GenerarDiagramaInput) -> str:
    """
    Dibuja un diagrama de flujo con cajas, rombos de decisión, elipses
    y flechas. Lo sube como PNG al Drive del usuario.
    """
    user = _user()
    local_path = None
    try:
        local_path = _temp_path(params.filename_hint or "diagrama", "png")
        spec = {
            "title": params.title,
            "nodes": [n.model_dump() for n in params.nodes],
            "edges": [
                {"from": e.from_node, "to": e.to, "label": e.label}
                for e in params.edges
            ],
        }
        diagram.build(spec, str(local_path))
        info = drive.upload(
            local_path,
            params.filename_hint or params.title or "Diagrama",
            user_email=user,
            convert_to_google=False,
        )
        return _ok("diagrama", info, f"Total: {len(params.nodes)} pasos.")
    except Exception as e:
        return _ups("dibujando el diagrama", e)
    finally:
        if local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


# ----- ASGI app con middleware de usuario -----

def get_app():
    """Construye la app ASGI lista para servir con HTTP streamable."""
    app = mcp.streamable_http_app()
    # Envolver con middleware para extraer usuario del header
    from starlette.applications import Starlette
    wrapped = Starlette(
        routes=app.routes,
        middleware=[Middleware(UserContextMiddleware)],
        lifespan=app.router.lifespan_context,
    )
    return wrapped


# ----- Entry point -----

def main() -> None:
    """Arranca el servidor HTTP. Railway inyecta PORT."""
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(
        get_app(),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
