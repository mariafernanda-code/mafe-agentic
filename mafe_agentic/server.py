#!/usr/bin/env python3
"""
Mafe Agentic — servidor en Railway que vive en Slack y también expone MCP.

Una sola app Starlette que sirve tres cosas:
    POST /slack/events  → Slack Events API (app_mention, etc.)
    POST /mcp           → MCP HTTP transport (Cowork/Claude Desktop)
    GET  /              → healthcheck simple

Multi-usuario: cada @mención en Slack identifica al invocador por su correo
de Workspace, y todas las acciones (Drive, Calendar) se hacen con esa
identidad via Domain-Wide Delegation.

Variables de entorno (Railway):
    ANTHROPIC_API_KEY            — para llamar a Claude
    SLACK_BOT_TOKEN              — xoxb-... bot token de la Slack App
    SLACK_SIGNING_SECRET         — para verificar firma de Slack
    SLACK_USER_TOKEN             — opcional, xoxp-... para search.messages
    GOOGLE_SERVICE_ACCOUNT_JSON  — JSON del service account con DWD
    MAFE_DEFAULT_USER            — fallback de identidad si no hay header (MCP)
    PORT                         — inyectado por Railway
"""

from __future__ import annotations

import contextvars
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mafe_agentic import drive
from mafe_agentic.generators import chart, diagram, pptx, xlsx
from mafe_agentic.slack_handler import slack_events_endpoint

log = logging.getLogger(__name__)


# ----- Contexto por request (para MCP) -----

current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user", default=None
)


class UserContextMiddleware(BaseHTTPMiddleware):
    """Extrae el usuario del header X-Mafe-User-Email (solo para path /mcp)."""

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
    return current_user.get()


# ----- MCP server (igual que antes — para Cowork/Claude Desktop) -----

mcp = FastMCP(
    "mafe_agentic",
    instructions=(
        "Mafe Agentic crea presentaciones, hojas de cálculo, gráficos y "
        "diagramas en el Google Drive del usuario que la invoca."
    ),
)


def _temp_path(prefix: str, extension: str) -> Path:
    safe = "".join(c for c in prefix if c.isalnum() or c in "-_") or "mafe"
    tmp_dir = Path(tempfile.gettempdir()) / "mafe-agentic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / f"{safe}-{uuid.uuid4().hex[:8]}.{extension}"


def _ok(tipo: str, info: dict, extra: str = "") -> str:
    nota = f"\n{extra}" if extra else ""
    return (
        f"Listo. Te dejé el {tipo} en tu Drive de Golden Gate Grid:\n\n"
        f"  {info['name']}\n"
        f"  {info['url']}\n"
        f"{nota}"
    )


def _ups(action: str, e: Exception) -> str:
    return (
        f"Uy, algo se me atravesó {action}. "
        f"Detalle técnico: {type(e).__name__}: {e}."
    )


class ChartInSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "pie"] = Field(description="Tipo de gráfico")
    labels: list[str] = Field(description="Etiquetas")
    values: list[float] = Field(description="Valores numéricos")
    title: str = Field(default="", description="Título opcional")


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(description="Título de la slide")
    bullets: list[str] | None = Field(default=None, description="Bullets")
    notes: str | None = Field(default=None, description="Notas del presentador")
    chart: ChartInSlide | None = Field(default=None)


class GenerarPresentacionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = None
    slides: list[Slide] = Field(min_length=1, max_length=40)
    filename_hint: str | None = None


class Sheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=31)
    headers: list[str]
    rows: list[list[Any]]
    totals_row: bool = False


class GenerarSheetsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sheets: list[Sheet] = Field(min_length=1, max_length=20)
    filename_hint: str | None = None


class GenerarGraficoInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    type: Literal["bar", "line", "pie"]
    labels: list[str]
    values: list[float]
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    filename_hint: str | None = None


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    shape: Literal["box", "ellipse", "diamond"] = "box"


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_node: str = Field(alias="from")
    to: str
    label: str | None = None


class GenerarDiagramaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", populate_by_name=True)
    title: str | None = None
    nodes: list[FlowNode] = Field(min_length=1, max_length=50)
    edges: list[FlowEdge] = Field(min_length=1, max_length=100)
    filename_hint: str | None = None


@mcp.tool(name="generar_presentacion")
async def generar_presentacion(params: GenerarPresentacionInput) -> str:
    """Crea presentación nativa de Google Slides en el Drive del usuario."""
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
        return _ok("deck", info, f"Total: {len(params.slides)} slides.")
    except Exception as e:
        return _ups("armando la presentación", e)
    finally:
        if local_path and local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


@mcp.tool(name="generar_sheets")
async def generar_sheets(params: GenerarSheetsInput) -> str:
    """Crea Google Sheets con varias hojas en el Drive del usuario."""
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


@mcp.tool(name="generar_grafico")
async def generar_grafico(params: GenerarGraficoInput) -> str:
    """Genera gráfico PNG y lo sube al Drive del usuario."""
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


@mcp.tool(name="generar_diagrama_flujo")
async def generar_diagrama_flujo(params: GenerarDiagramaInput) -> str:
    """Diagrama de flujo PNG en Drive."""
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


# ----- Healthcheck simple -----

async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "service": "mafe-agentic",
        "endpoints": ["/mcp", "/slack/events"],
    })


# ----- App principal -----

def get_app() -> Starlette:
    """
    Construye la app ASGI completa con todos los endpoints.

    Estructura:
        /                  → healthcheck
        /slack/events      → Slack Events API
        /mcp               → MCP HTTP transport (con UserContextMiddleware)
    """
    mcp_inner = mcp.streamable_http_app()

    # App MCP con middleware (solo aplica al sub-mount /mcp)
    mcp_app = Starlette(
        routes=mcp_inner.routes,
        middleware=[Middleware(UserContextMiddleware)],
        lifespan=mcp_inner.router.lifespan_context,
    )

    routes = [
        Route("/", health, methods=["GET"]),
        Route("/slack/events", slack_events_endpoint, methods=["POST"]),
        Mount("/mcp", app=mcp_app),
    ]

    app = Starlette(routes=routes, lifespan=mcp_inner.router.lifespan_context)
    return app


def main() -> None:
    """Arranca Uvicorn. Railway inyecta PORT."""
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info("Mafe Agentic arrancando en %s:%d", host, port)
    log.info("  POST /slack/events  ← Slack Events")
    log.info("  POST /mcp           ← MCP HTTP")
    log.info("  GET  /              ← healthcheck")

    uvicorn.run(
        get_app(),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
