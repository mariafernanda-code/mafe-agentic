"""
Resolución de identidad: Slack user ID → email corporativo.

Cada vez que llega una @mención, sabemos el slack_user_id pero
necesitamos el correo de Google Workspace para impersonar via DWD.
Resolvemos con users.info y cacheamos en memoria — los emails
no cambian seguido.
"""

from __future__ import annotations

import logging
import os
import threading

from slack_sdk.web.async_client import AsyncWebClient

log = logging.getLogger(__name__)

# Fallback cuando no logremos resolver email (testing local, errores de scope)
DEFAULT_EMAIL = os.environ.get("MAFE_DEFAULT_USER", "")

# Caché thread-safe: slack_user_id → email
_cache: dict[str, str] = {}
_lock = threading.Lock()


async def resolve_email(slack_client: AsyncWebClient, slack_user_id: str) -> str:
    """
    Devuelve el email corporativo del usuario que escribió en Slack.

    1. Si está en caché → lo devuelve directo
    2. Si no → llama users.info para obtener profile.email (requiere users:read.email)
    3. Si falla → cae al MAFE_DEFAULT_USER para no romper el flujo
    """
    if not slack_user_id:
        return DEFAULT_EMAIL

    with _lock:
        cached = _cache.get(slack_user_id)
    if cached:
        return cached

    try:
        resp = await slack_client.users_info(user=slack_user_id)
        if not resp.get("ok"):
            log.warning("users.info no devolvió ok para %s", slack_user_id)
            return DEFAULT_EMAIL
        email = resp.get("user", {}).get("profile", {}).get("email", "")
        if not email:
            log.warning("Sin email visible para %s (¿falta scope users:read.email?)", slack_user_id)
            return DEFAULT_EMAIL
        with _lock:
            _cache[slack_user_id] = email
        log.info("Resuelto %s → %s", slack_user_id, email)
        return email
    except Exception as e:
        log.exception("Error resolviendo email de %s: %s", slack_user_id, e)
        return DEFAULT_EMAIL
