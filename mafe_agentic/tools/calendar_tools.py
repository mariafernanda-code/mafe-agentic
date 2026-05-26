"""
Herramientas de Google Calendar.

Cada usuario que invoca a Mafe en Slack usa SU propio calendario via DWD.
Todas las juntas se crean con link de Meet automático.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from mafe_agentic.auth import credentials_for

log = logging.getLogger(__name__)


def _calendar_service(user_email: str):
    creds = credentials_for(user_email)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _iso(dt: datetime) -> str:
    """Formato ISO con timezone, listo para Google Calendar API."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ----- Tool: listar eventos -----

async def list_events(
    *, user_email: str, days_ahead: int = 7, calendar_id: str = "primary"
) -> dict:
    """Lista próximos eventos en el calendario del usuario."""
    svc = _calendar_service(user_email)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    res = svc.events().list(
        calendarId=calendar_id,
        timeMin=_iso(now),
        timeMax=_iso(end),
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()

    events = []
    for e in res.get("items", []):
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
        end_t = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")
        events.append({
            "id": e.get("id"),
            "summary": e.get("summary", "(sin título)"),
            "start": start,
            "end": end_t,
            "meet_link": e.get("hangoutLink"),
            "attendees": [a.get("email") for a in e.get("attendees", [])],
            "location": e.get("location"),
        })
    return {"count": len(events), "events": events}


# ----- Tool: encontrar slots libres -----

async def find_free_slots(
    *, user_email: str, duration_minutes: int = 30, days_ahead: int = 5,
    work_start_hour: int = 9, work_end_hour: int = 18,
) -> dict:
    """
    Devuelve slots libres en el calendario, respetando horario de trabajo.
    Hace freebusy query para detectar bloqueos.
    """
    svc = _calendar_service(user_email)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    fb = svc.freebusy().query(body={
        "timeMin": _iso(now),
        "timeMax": _iso(end),
        "items": [{"id": "primary"}],
    }).execute()
    busy = fb.get("calendars", {}).get("primary", {}).get("busy", [])
    busy_periods = [
        (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
         datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
        for b in busy
    ]

    slots = []
    cursor = now.replace(minute=0, second=0, microsecond=0)
    delta = timedelta(minutes=duration_minutes)

    while cursor + delta <= end and len(slots) < 10:
        # Saltar fuera de horario laboral / fines de semana
        if cursor.weekday() >= 5 or cursor.hour < work_start_hour or cursor.hour >= work_end_hour:
            cursor += timedelta(hours=1)
            continue
        slot_end = cursor + delta
        conflict = any(b[0] < slot_end and b[1] > cursor for b in busy_periods)
        if not conflict and cursor > now:
            slots.append({
                "start": cursor.isoformat(),
                "end": slot_end.isoformat(),
            })
        cursor += timedelta(minutes=30)

    return {"duration_minutes": duration_minutes, "slots": slots}


# ----- Tool: crear evento con Meet -----

async def create_event(
    *, user_email: str, summary: str, start_iso: str, end_iso: str,
    attendees: list[str] | None = None, description: str | None = None,
    location: str | None = None, with_meet: bool = True,
) -> dict:
    """Crea un evento en Calendar. with_meet=True genera link de Meet automático."""
    svc = _calendar_service(user_email)

    body = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]
    if with_meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"mafe-{datetime.now().timestamp()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    event = svc.events().insert(
        calendarId="primary",
        body=body,
        conferenceDataVersion=1 if with_meet else 0,
        sendUpdates="all" if attendees else "none",
    ).execute()

    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "start": event.get("start", {}).get("dateTime"),
        "end": event.get("end", {}).get("dateTime"),
        "meet_link": event.get("hangoutLink"),
        "html_link": event.get("htmlLink"),
        "attendees": [a.get("email") for a in event.get("attendees", [])],
    }


# ----- Specs -----

SPECS = [
    {
        "name": "calendar_list_events",
        "description": "Lista los próximos eventos del calendario del usuario. Úsalo cuando te pregunten qué tienen agendado, qué sigue, su agenda.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Cuántos días hacia adelante (default 7)",
                    "default": 7,
                },
            },
        },
    },
    {
        "name": "calendar_find_free_slots",
        "description": "Encuentra slots libres en el calendario del usuario, respetando horario laboral (9-18h L-V).",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_minutes": {"type": "integer", "default": 30},
                "days_ahead": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Crea un evento en Google Calendar. Por default agrega link de Meet automáticamente. "
            "Si hay attendees, les manda invitación. Recibe start_iso y end_iso en formato ISO 8601 "
            "(ej: '2026-05-28T10:00:00-06:00')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Título del evento"},
                "start_iso": {"type": "string", "description": "Inicio en ISO 8601 con zona horaria"},
                "end_iso": {"type": "string", "description": "Fin en ISO 8601 con zona horaria"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de emails a invitar",
                },
                "description": {"type": "string", "description": "Descripción del evento"},
                "location": {"type": "string", "description": "Lugar físico (opcional)"},
                "with_meet": {
                    "type": "boolean",
                    "default": True,
                    "description": "Si True, agrega link de Meet auto",
                },
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
]


DISPATCH = {
    "calendar_list_events": list_events,
    "calendar_find_free_slots": find_free_slots,
    "calendar_create_event": create_event,
}
