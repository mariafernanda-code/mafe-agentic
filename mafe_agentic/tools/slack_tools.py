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
import unicodedata

from slack_sdk.web.async_client import AsyncWebClient

log = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Quita acentos y pasa a minúsculas para búsqueda flexible."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.lower().strip()


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


# ----- Tool: buscar usuarios por nombre -----

async def lookup_users(
    *, user_email: str, query: str, limit: int = 10
) -> dict:
    """
    Busca usuarios en el workspace por nombre, display name o email parcial.
    - Normaliza texto: ignora acentos y mayúsculas/minúsculas
    - Pagina users.list para workspaces grandes (>200 personas)
    - Soporta búsquedas parciales: "Christian" matches "Christian Hernández"
    - Devuelve TODOS los matches para que Claude pueda elegir o pedir aclaración
    """
    client = _client()

    q_norm = _normalize(query)
    # También permitir match por palabras individuales (ej. "Christian Hernandez" → ["christian", "hernandez"])
    q_words = [w for w in q_norm.split() if len(w) >= 2]

    matches = []
    cursor = ""
    pages = 0
    total_users_seen = 0

    while pages < 10:  # safety limit, max 10 páginas = ~2000 usuarios
        resp = await client.users_list(limit=200, cursor=cursor) if cursor else await client.users_list(limit=200)
        if not resp.get("ok"):
            raise RuntimeError(f"Slack rechazó users.list: {resp.get('error')}")

        for u in resp.get("members", []):
            total_users_seen += 1
            if u.get("deleted") or u.get("is_bot") or u.get("id") == "USLACKBOT":
                continue
            profile = u.get("profile", {})
            candidates_raw = " ".join([
                u.get("name", ""),
                u.get("real_name", ""),
                profile.get("real_name", ""),
                profile.get("display_name", ""),
                profile.get("display_name_normalized", ""),
                profile.get("first_name", ""),
                profile.get("last_name", ""),
                profile.get("email", ""),
            ])
            text_norm = _normalize(candidates_raw)
            # Match si el query completo está, O si TODAS las palabras del query están
            full_match = q_norm and q_norm in text_norm
            words_match = q_words and all(w in text_norm for w in q_words)
            if full_match or words_match:
                matches.append({
                    "id": u.get("id"),
                    "name": u.get("real_name") or profile.get("real_name") or u.get("name"),
                    "display_name": profile.get("display_name"),
                    "email": profile.get("email"),
                    "title": profile.get("title"),
                })
                if len(matches) >= limit:
                    break

        if len(matches) >= limit:
            break

        cursor = resp.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
        pages += 1

    log.info(
        "lookup_users query=%r → %d matches (revisé %d usuarios, %d páginas)",
        query, len(matches), total_users_seen, pages + 1,
    )

    note = ""
    if len(matches) == 0:
        note = (
            f"No encontré a nadie que matche '{query}'. Pídele a quien te invocó "
            f"el correo directo (algo como 'pasame su email') para poder agendar."
        )
    elif len(matches) > 1:
        note = (
            f"Encontré {len(matches)} personas. Antes de actuar, lista las opciones "
            f"a quien te invocó y pregúntale cuál."
        )
    elif len(matches) == 1:
        email = matches[0].get("email")
        if email and (email.count(".") > 1 or len(email.split("@")[0]) > 12):
            note = (
                f"El email del perfil ({email}) se ve largo o con varios puntos. "
                f"Antes de usarlo, confírmaselo a quien te invocó."
            )

    return {"query": query, "count": len(matches), "users": matches, "note": note}


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
        "name": "slack_lookup_users",
        "description": (
            "Busca usuarios del workspace por nombre, display name o email. "
            "OBLIGATORIO usar esto cuando alguien mencione a otra persona por nombre "
            "(ej: 'agenda con Christian Hernandez') para obtener su correo de Google Workspace, "
            "que luego usas en calendar_create_event como attendee."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Nombre o parte del nombre (ej: 'Christian', 'Maria Fernanda')"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
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
    "slack_lookup_users": lookup_users,
    "slack_search": search_messages,
    "slack_create_canvas": create_canvas,
    "slack_create_list": create_list,
}
