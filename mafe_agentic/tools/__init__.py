"""
Caja de herramientas que Claude puede invocar.

Cada módulo expone:
    SPECS — lista de specs en formato Anthropic tool use
    DISPATCH — dict {tool_name: async function} para ejecutar la tool
"""

from mafe_agentic.tools import calendar_tools, drive_tools, slack_tools


def all_specs():
    """Junta los specs de todas las tools en una sola lista."""
    return [
        *drive_tools.SPECS,
        *calendar_tools.SPECS,
        *slack_tools.SPECS,
    ]


def dispatch_table():
    """Junta los handlers de todas las tools en un solo dict."""
    table = {}
    table.update(drive_tools.DISPATCH)
    table.update(calendar_tools.DISPATCH)
    table.update(slack_tools.DISPATCH)
    return table
