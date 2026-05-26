"""
Handler de Slack Events API.

Recibe @menciones del bot, identifica al usuario, dispara el cerebro
y responde en el hilo. Verifica la firma de Slack para asegurar que el
request viene de Slack y no de un atacante.

Endpoints expuestos por la app Starlette:
    POST /slack/events       — events API (handled by Bolt)
    GET  /slack/install      — opcional, instalación OAuth (no usado por ahora)
"""

from __future__ import annotations

import logging
import os
import re

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.starlette.async_handler import AsyncSlackRequestHandler

from mafe_agentic import agent_brain
from mafe_agentic.identity import resolve_email

log = logging.getLogger(__name__)


# ----- Bolt App (lazy init para que el import no falle sin tokens) -----

# Placeholder NO-secret para que la inicialización no rompa antes de que
# Railway inyecte las variables reales. Es texto literal con guion bajo,
# no matchea ningún pattern de Slack token real.
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


async def _resolve_channel_name(client, channel_id: str) -> str | None:
    """Intenta obtener el nombre del canal para incluir en contexto."""
    try:
        resp = await client.conversations_info(channel=channel_id)
        if resp.get("ok"):
            return resp.get("channel", {}).get("name")
    except Exception:
        pass
    return None


async def _resolve_user_name(client, user_id: str) -> str | None:
    """Intenta obtener el nombre del usuario."""
    try:
        resp = await client.users_info(user=user_id)
        if resp.get("ok"):
            profile = resp.get("user", {}).get("profile", {})
            return profile.get("real_name") or profile.get("display_name")
    except Exception:
        pass
    return None


# ----- Event: app_mention -----

@bolt_app.event("app_mention")
async def on_app_mention(event, client, say, logger):
    """
    Mafe fue @mencionada en un canal.
    Identifica al usuario, dispara el cerebro, responde en hilo.
    """
    slack_user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = _strip_mention(event.get("text", ""))

    log.info(
        "@mention de %s en %s: %s",
        slack_user_id, channel_id, text[:80]
    )

    if not text:
        await say(
            text="Aquí estoy. ¿En qué te ayudo? Puedo leer canales, crear presentaciones, programar juntas, hacer Canvas, listas, y más.",
            thread_ts=thread_ts,
        )
        return

    # Resolver identidad del usuario
    user_email = await resolve_email(client, slack_user_id)
    if not user_email:
        await say(
            text=(
                "Quería ayudarte pero no logré identificar tu correo en Slack. "
                "Pídele al admin del workspace que active la visibilidad de email del perfil."
            ),
            thread_ts=thread_ts,
        )
        return

    # Contexto extra
    channel_name = await _resolve_channel_name(client, channel_id)
    user_name = await _resolve_user_name(client, slack_user_id)
    slack_context = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_ts": thread_ts,
        "user_id": slack_user_id,
        "user_name": user_name,
    }

    # Avisar que está trabajando si la tarea suena pesada
    if any(w in text.lower() for w in ("resumen", "lee", "revisa", "presentación", "deck", "propuesta")):
        try:
            await say(text="Va, trabajando en eso…", thread_ts=thread_ts)
        except Exception:
            pass

    # Ejecutar cerebro
    try:
        reply = await agent_brain.run(
            user_email=user_email,
            user_message=text,
            slack_context=slack_context,
        )
    except Exception as e:
        log.exception("agent_brain falló: %s", e)
        reply = (
            f"Algo se me atravesó por dentro. Detalle: {type(e).__name__}: {e}. "
            "¿Lo intentamos de nuevo?"
        )

    # Postear respuesta final en el hilo
    await say(text=reply, thread_ts=thread_ts)


# ----- Handler Starlette para mount en server.py -----

slack_handler = AsyncSlackRequestHandler(bolt_app)


async def slack_events_endpoint(request):
    """Endpoint POST /slack/events que delega a Bolt."""
    return await slack_handler.handle(request)
