"""
Cerebro de Mafe Agentic.

Recibe un mensaje de Slack (texto + contexto del canal + identidad del usuario)
y orquesta una conversación con Claude usando tool use. Claude decide qué
herramientas usar, las ejecutamos pasando la identidad del usuario, y
regresamos la respuesta final en texto.

Modelo: claude-sonnet-4-5 (rápido y muy capaz para tool use).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from anthropic import AsyncAnthropic

from mafe_agentic.system_prompt import SYSTEM_PROMPT
from mafe_agentic.tools import all_specs, dispatch_table

log = logging.getLogger(__name__)

MODEL = os.environ.get("MAFE_MODEL", "claude-sonnet-4-5")
MAX_TOOL_TURNS = 10
MAX_TOKENS = 4096


def _client() -> AsyncAnthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el entorno.")
    return AsyncAnthropic(api_key=api_key)


async def _execute_tool(
    name: str, args: dict, user_email: str
) -> str:
    """Ejecuta una tool por nombre. Inyecta user_email en kwargs."""
    table = dispatch_table()
    fn = table.get(name)
    if not fn:
        return json.dumps({"error": f"Tool '{name}' no existe."})
    try:
        # Todas las tools aceptan user_email como kwarg explícito
        result = await fn(user_email=user_email, **args)
        return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        log.exception("Tool %s falló: %s", name, e)
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "tool": name,
        }, ensure_ascii=False)


async def run(
    *,
    user_email: str,
    user_message: str,
    slack_context: dict | None = None,
) -> str:
    """
    Ejecuta el loop completo de tool use de Claude.

    user_email: identidad del usuario (para impersonation Drive/Calendar)
    user_message: texto del @mention
    slack_context: dict con channel_id, thread_ts, channel_name, user_name
                   (se inyecta al primer mensaje para que Claude sepa dónde está)
    """
    client = _client()
    tools = all_specs()

    # Mensaje inicial enriquecido con contexto Slack
    ctx_lines = []
    if slack_context:
        ctx_lines.append(f"[Contexto: estás respondiendo en Slack.")
        if slack_context.get("channel_name"):
            ctx_lines.append(f"Canal: #{slack_context['channel_name']} (ID {slack_context.get('channel_id')})")
        else:
            ctx_lines.append(f"Canal ID: {slack_context.get('channel_id')}")
        if slack_context.get("user_name"):
            ctx_lines.append(f"Quien te invocó: {slack_context['user_name']} ({user_email})")
        else:
            ctx_lines.append(f"Quien te invocó: {user_email}")
        if slack_context.get("thread_ts"):
            ctx_lines.append(f"Hilo: {slack_context['thread_ts']}")
        ctx_lines.append("]")
        ctx_lines.append("")

    full_message = ("\n".join(ctx_lines) + user_message).strip()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": full_message},
    ]

    for turn in range(MAX_TOOL_TURNS):
        log.info("Turn %d, mensajes=%d", turn, len(messages))
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Acumular la respuesta del assistant
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            # Respuesta final — extraer texto
            text_parts = [
                block.text for block in resp.content if block.type == "text"
            ]
            return "\n\n".join(text_parts).strip() or "Listo."

        # Ejecutar todas las tool calls de este turn
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            log.info("Tool call: %s args=%s", block.name, json.dumps(block.input)[:200])
            result_str = await _execute_tool(block.name, block.input, user_email)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return (
        "Llegué al máximo de pasos sin terminar. "
        "Dime con más detalle qué necesitas y lo intentamos de nuevo."
    )
