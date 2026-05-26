"""
Handler de Slack Events API.

Recibe @menciones del bot, identifica al usuario, dispara el cerebro
y responde en el hilo con contexto del thread completo.
"""

from __future__ import annotations

import base64
import logging
import os
import re

import httpx
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.starlette.async_handler import AsyncSlackRequestHandler

from mafe_agentic import agent_brain
from mafe_agentic.identity import resolve_email

log = logging.getLogger(__name__)

# Tipos de imagen que Claude puede procesar
SUPPORTED_IMAGE_MIME = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
MAX_IMAGES_PER_MESSAGE = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


# ----- Bolt App (lazy init para que el import no falle sin tokens) -----

_PLACEHOLDER_TOKEN = "_not_configured_yet_"
_PLACEHOLDER_SECRET = "_not_configured_yet_"

bolt_app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN") or _PLACEHOLDER_TOKEN,
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET") or _PLACEHOLDER_SECRET,
    raise_error_for_unhandled_request=False,
)


def _strip_mention(text: str) -> str:
    """Quita la @mención del bot del inicio del mensaje."""
    return re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()


def _slackify(text: str) -> str:
    """
    Convierte markdown estilo Claude a markdown que Slack renderiza bien.
    Slack NO entiende **bold** ni *italic* normales, usa *bold* y _italic_.
    Para evitar inconsistencias y la fealdad de ** crudos, limpiamos:
    - **bold** → *bold*  (Slack lo renderiza como bold)
    - __bold__ → *bold*
    - Quitamos headers de Markdown (# Titulo → Titulo)
    """
    if not text:
        return text
    # Quitar headers Markdown (# Titulo, ## Subtitulo, etc.)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Convertir **bold** y __bold__ a *bold* (formato Slack)
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"*\1*", text)
    text = re.sub(r"__([^_\n]+?)__", r"*\1*", text)
    # Quitar enlaces Markdown raros tipo [texto](url) → texto: url
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    return text.strip()


async def _resolve_channel_name(client, channel_id: str) -> str | None:
    try:
        resp = await client.conversations_info(channel=channel_id)
        if resp.get("ok"):
            return resp.get("channel", {}).get("name")
    except Exception:
        pass
    return None


async def _resolve_user_name(client, user_id: str) -> str | None:
    try:
        resp = await client.users_info(user=user_id)
        if resp.get("ok"):
            profile = resp.get("user", {}).get("profile", {})
            return profile.get("real_name") or profile.get("display_name")
    except Exception:
        pass
    return None


async def _download_image(file_info: dict) -> dict | None:
    """
    Descarga una imagen de Slack y la devuelve como base64 lista para Claude.
    Slack files son privados, requieren Bot Token como Bearer.
    """
    url = file_info.get("url_private_download") or file_info.get("url_private")
    mime = (file_info.get("mimetype") or "").lower()
    name = file_info.get("name", "imagen")

    if not url or mime not in SUPPORTED_IMAGE_MIME:
        return None

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not bot_token or bot_token.startswith("_not_"):
        return None

    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content = resp.content
            if len(content) > MAX_IMAGE_BYTES:
                log.warning("Imagen %s muy grande (%d bytes), saltando", name, len(content))
                return None
            b64 = base64.standard_b64encode(content).decode("ascii")
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": b64,
                },
            }
    except Exception as e:
        log.warning("No pude bajar imagen %s: %s", name, e)
        return None


async def _extract_images(event: dict) -> list[dict]:
    """Saca imágenes adjuntas del evento de Slack y las prepara para Claude."""
    files = event.get("files") or []
    if not files:
        return []
    images = []
    for f in files[:MAX_IMAGES_PER_MESSAGE]:
        img = await _download_image(f)
        if img:
            images.append(img)
    if images:
        log.info("Adjuntando %d imagen(es) al mensaje", len(images))
    return images


async def _fetch_thread_history(
    client, channel_id: str, thread_ts: str, current_ts: str
) -> list[dict]:
    """
    Lee los mensajes anteriores del hilo para que Mafe tenga contexto.
    Excluye el mensaje actual (current_ts) que es el que dispara la llamada.
    Devuelve lista de {user, text, is_bot, ts} en orden cronológico.
    """
    if not thread_ts or thread_ts == current_ts:
        return []  # no hay historia previa, este es el primer mensaje del hilo

    try:
        resp = await client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=30,
        )
        if not resp.get("ok"):
            return []
        msgs = []
        for m in resp.get("messages", []):
            ts = m.get("ts", "")
            if ts == current_ts:
                continue  # no incluir el mensaje actual
            text = _strip_mention(m.get("text", ""))
            if not text:
                continue
            msgs.append({
                "user": m.get("user") or m.get("bot_id") or "system",
                "text": text,
                "is_bot": bool(m.get("bot_id")) or m.get("subtype") == "bot_message",
                "ts": ts,
            })
        return msgs
    except Exception as e:
        log.warning("No pude leer historia del hilo: %s", e)
        return []


# ----- Event: app_mention -----

@bolt_app.event("app_mention")
async def on_app_mention(event, client, say, logger):
    """
    Mafe fue @mencionada. Lee contexto del hilo, dispara cerebro, responde.
    """
    slack_user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    event_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or event_ts
    text = _strip_mention(event.get("text", ""))

    log.info(
        "@mention de %s en %s (thread=%s): %s",
        slack_user_id, channel_id, thread_ts, text[:80]
    )

    user_mention = f"<@{slack_user_id}>" if slack_user_id else ""

    if not text:
        await say(
            text=f"{user_mention} Aquí estoy 👋 ¿En qué te ayudo? Puedo leer canales, hacer presentaciones, programar juntas, crear Canvas y listas, y más.",
            thread_ts=thread_ts,
        )
        return

    # Resolver identidad del usuario
    user_email = await resolve_email(client, slack_user_id)
    if not user_email:
        await say(
            text=(
                f"{user_mention} Quería ayudarte pero no logré identificar tu correo en Slack. "
                "Pídele al admin del workspace que active la visibilidad de email del perfil."
            ),
            thread_ts=thread_ts,
        )
        return

    # Contexto extra
    channel_name = await _resolve_channel_name(client, channel_id)
    user_name = await _resolve_user_name(client, slack_user_id)

    # Leer historia del hilo (mensajes previos, sin el actual)
    thread_history = await _fetch_thread_history(
        client, channel_id, thread_ts, event_ts
    )
    log.info("Historia del hilo: %d mensajes previos", len(thread_history))

    # Extraer imágenes adjuntas (si las hay)
    images = await _extract_images(event)

    slack_context = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_ts": thread_ts,
        "user_id": slack_user_id,
        "user_name": user_name,
        "thread_history": thread_history,
    }

    # Avisar que está trabajando si la tarea suena pesada
    if any(w in text.lower() for w in ("resumen", "lee", "revisa", "presentación", "deck", "propuesta", "copia", "plantilla")):
        try:
            await say(text=f"{user_mention} Va, trabajando en eso ✨", thread_ts=thread_ts)
        except Exception:
            pass

    # Ejecutar cerebro
    try:
        reply = await agent_brain.run(
            user_email=user_email,
            user_message=text,
            slack_context=slack_context,
            images=images,
        )
    except Exception as e:
        log.exception("agent_brain falló: %s", e)
        reply = (
            f"Algo se me atravesó por dentro 😅 Detalle técnico: {type(e).__name__}: {e}. "
            "¿Lo intentamos de nuevo?"
        )

    # Limpiar markdown estilo Claude que Slack no renderiza bien
    reply = _slackify(reply)

    # Anteponer @-mención si no la incluye ya
    final_reply = reply
    if user_mention and user_mention not in reply[:50]:
        final_reply = f"{user_mention} {reply}"

    # Postear respuesta final en el hilo
    await say(text=final_reply, thread_ts=thread_ts)


# ----- Handler Starlette para mount en server.py -----

slack_handler = AsyncSlackRequestHandler(bolt_app)


async def slack_events_endpoint(request):
    """Endpoint POST /slack/events que delega a Bolt."""
    return await slack_handler.handle(request)
