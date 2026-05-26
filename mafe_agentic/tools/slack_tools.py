"""
Herramientas de Slack para Mafe Agentic.

Mafe puede:
  - leer mensajes recientes de un canal
  - leer un hilo entero
  - buscar mensajes en el workspace
  - crear un Canvas adjunto a un canal o standalone
  - crear una lista (Slack Lists) en un canal
  - publicar mensajes (lo usa internamente el agent_brain para responder)

Todas las funciones usan el AsyncWebClient global con el Bot Token
(xoxb-...). El bot token tiene los scopes necesarios — ver manifest.
"""

from __future__ import annotations

import logging
import os

from slack_sdk.web.async_client import AsyncWebClient

log = logging.getLogger(__name__)


def _client() -> AsyncWebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Falta SLACK_BOT_TOKEN en el entorno.")
    return AsyncWebClient(token=token)


# ----- Tool: leer canal -----

async def read_channel(
    *, user_email: str, channel_id: str, limit: int = 30
) -> dict:
    """Lee los últimos N mensajes de un canal de Slack."""
    client = _client()
    resp = await client.conversations_history(channel=channel_id, limit=min(limit, 100))
    if not resp.get("ok"):
        raise RuntimeError(f"Slack rechazó conversations.history: {resp.get('error')}")
    msgs = []
    for m in resp.get("messages", []):
        msgs.append({
            "user": m.get("user") or m.get("bot_id") or "system",
            "ts": m.get("ts"),
            "text": m.get("text", ""),
            "thread_ts": m.get("thread_ts"),
            "reply_count": m.get("reply_count", 0),
        })
    return {"channel": channel_id, "count": len(msgs), "messages": msgs}


# ----- Tool: leer hilo -----

async def read_thread(
    *, user_email: str, channel_id: str, thread_ts: str
) -> dict:
    """Lee un hilo completo (mensaje raíz + respuestas)."""
    client = _client()
    resp = await client.conversations_replies(channel=channel_id, ts=thread_ts)
    if not resp.get("ok"):
        raise RuntimeError(f"Slack rechazó conversations.replies: {resp.get('error')}")
    msgs = [
        {
            "user": m.get("user") or m.get("bot_id") or "system",
            "ts": m.get("ts"),
            "text": m.get("text", ""),
        }
        for m in resp.get("messages", [])
    ]
    return {"channel": channel_id, "thread_ts": thread_ts, "messages": msgs}


# ----- Tool: buscar en workspace -----

async def search_messages(
    *, user_email: str, query: str, count: int = 20
) -> dict:
    """
    Busca mensajes en el workspace que contengan el query.
    Requiere User Token (xoxp-...) con scope search:read.
    """
    user_token = os.environ.get("SLACK_USER_TOKEN", "")
    if not user_token:
        return {
            "error": "Para búsqueda global necesito SLACK_USER_TOKEN configurado.",
            "matches": [],
        }
    client = AsyncWebClient(token=user_token)
    resp = await client.search_messages(query=query, count=min(count, 50))
    if not resp.get("ok"):
        raise RuntimeError(f"Slack rechazó search.messages: {resp.get('error')}")
    matches = []
    for m in resp.get("messages", {}).get("matches", []):
        matches.append({
            "channel": m.get("channel", {}).get("name"),
            "user": m.get("username"),
            "text": m.get("text", ""),
            "ts": m.get("ts"),
            "permalink": m.get("permalink"),
        })
    return {"query": query, "count": len(matches), "matches": matches}


# ----- Tool: crear Canvas -----

async def create_canvas(
    *, user_email: str, channel_id: str, title: str, markdown: str
) -> dict:
    """
    Crea un Canvas en Slack adjunto a un canal.
    Requiere scope canvases:write.
    """
    client = _client()
    resp = await client.api_call(
        "conversations.canvases.create",
        json={
            "channel_id": channel_id,
            "document_content": {
                "type": "markdown",
                "markdown": markdown,
            },
            "title": title,
        },
    )
    if not resp.get("ok"):
        raise RuntimeError(f"Slack rechazó canvases.create: {resp.get('error')}")
    canvas_id = resp.get("canvas_id")
    return {
        "canvas_id": canvas_id,
        "title": title,
        "channel_id": channel_id,
    }


# ----- Tool: crear Lista -----

async def create_list(
    *, user_email: str, channel_id: str, title: str, items: list[str]
) -> dict:
    """
    Crea una Lista de Slack en un canal con items checklist.

    Nota: la API de Slack Lists está en evolución; si falla, lo intentamos
    primero como un message con bloques de checkbox como fallback.
    """
    client = _client()
    try:
        resp = await client.api_call(
            "slackLists.create",
            json={
                "channel_id": channel_id,
                "name": title,
                "items": [{"text": i} for i in items],
            },
        )
        if resp.get("ok"):
            return {"list_id": resp.get("list_id"), "title": title}
    except Exception as e:
        log.warning("slackLists.create no soportado, uso fallback: %s", e)

    # Fallback: postea un mensaje con checklist en Markdown
    md_items = "\n".join(f"☐ {i}" for i in items)
    text = f"*{title}*\n{md_items}"
    resp = await client.chat_postMessage(channel=channel_id, text=text, mrkdwn=True)
    return {
        "list_id": None,
        "title": title,
        "message_ts": resp.get("ts"),
        "note": "Slack Lists API no disponible — publiqué la lista como mensaje con checklist.",
    }


# ----- Specs -----

SPECS = [
    {
        "name": "slack_read_channel",
        "description": "Lee los últimos mensajes de un canal de Slack. Úsalo cuando necesites contexto del canal antes de responder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "ID del canal (C...)"},
                "limit": {"type": "integer", "default": 30, "description": "Cuántos mensajes (max 100)"},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "slack_read_thread",
        "description": "Lee un hilo completo de Slack (mensaje raíz + replies).",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "thread_ts": {"type": "string", "description": "Timestamp del mensaje raíz del hilo"},
            },
            "required": ["channel_id", "thread_ts"],
        },
    },
    {
        "name": "slack_search",
        "description": "Busca mensajes en el workspace que contengan un query (texto, from:user, in:#canal, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query estilo búsqueda de Slack"},
                "count": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "slack_create_canvas",
        "description": "Crea un Canvas de Slack adjunto a un canal. El contenido va en Markdown — úsalo para resúmenes estructurados, notas de junta, planes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "title": {"type": "string"},
                "markdown": {"type": "string", "description": "Contenido del canvas en Markdown"},
            },
            "required": ["channel_id", "title", "markdown"],
        },
    },
    {
        "name": "slack_create_list",
        "description": "Crea una Lista de Slack (checklist) en un canal. Si la API de Lists no está disponible, publica como mensaje con checkboxes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "title": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["channel_id", "title", "items"],
        },
    },
]


DISPATCH = {
    "slack_read_channel": read_channel,
    "slack_read_thread": read_thread,
    "slack_search": search_messages,
    "slack_create_canvas": create_canvas,
    "slack_create_list": create_list,
}
