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
    attendees: list[str] | None = None,
) -> dict:
    """
    Devuelve slots libres comunes entre el invocador Y los attendees pedidos.
    Útil para planear juntas: pasa los correos de los asistentes y devuelve
    huecos donde TODOS están libres.

    En Google Workspace los miembros del dominio pueden ver el free/busy
    de otros miembros del dominio por default.
    """
    svc = _calendar_service(user_email)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    # Construir items para freebusy: el usuario + attendees
    emails_to_check = [user_email]
    if attendees:
        for email in attendees:
            if email and email not in emails_to_check:
                emails_to_check.append(email)

    fb = svc.freebusy().query(body={
        "timeMin": _iso(now),
        "timeMax": _iso(end),
        "items": [{"id": e} for e in emails_to_check],
    }).execute()

    calendars = fb.get("calendars", {})

    # Verificar errores por calendario (a veces el bot puede no tener acceso)
    errors_by_email = {}
    busy_periods = []
    for email in emails_to_check:
        cal_data = calendars.get(email, {})
        errors = cal_data.get("errors", [])
        if errors:
            errors_by_email[email] = [e.get("reason") for e in errors]
        for b in cal_data.get("busy", []):
            busy_periods.append((
                datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
            ))

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

    result = {
        "duration_minutes": duration_minutes,
        "slots": slots,
        "calendars_checked": emails_to_check,
    }
    if errors_by_email:
        result["errors_by_email"] = errors_by_email
        result["note"] = (
            "Algunos calendarios devolvieron errores (probablemente no compartidos contigo). "
            "Los slots devueltos solo consideran los calendarios accesibles."
        )
    return result


# ----- Tool: crear evento con Meet -----

async def create_event(
    *, user_email: str, summary: str, start_iso: str, end_iso: str,
    attendees: list[str] | None = None, description: str | None = None,
    location: str | None = None, with_meet: bool = True,
    allow_weekend: bool = False,
) -> dict:
    """Crea un evento en Calendar. with_meet=True genera link de Meet automático.

    Por default NO permite crear eventos en sábado/domingo. Si Claude tiene
    que crearlos en fin de semana porque el usuario lo pidió explícito,
    debe pasar allow_weekend=True.
    """
    # Validar que no caiga en fin de semana
    if not allow_weekend:
        try:
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            if start_dt.weekday() >= 5:  # sábado=5, domingo=6
                day_name = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][start_dt.weekday()]
                return {
                    "error": (
                        f"La fecha {start_iso[:10]} es {day_name} (fin de semana). "
                        f"Por política no agendo juntas en fin de semana salvo que se pida explícito. "
                        f"Recalcula a un día hábil (lunes-viernes) y vuelve a intentar. "
                        f"Si el usuario SÍ lo quiere en fin de semana, pasa allow_weekend=True."
                    ),
                    "weekend": True,
                    "day": day_name,
                }
        except Exception as e:
            log.warning("No pude validar start_iso: %s", e)

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


# ----- Tool: actualizar evento existente -----

async def update_event(
    *, user_email: str, event_id: str,
    summary: str | None = None, start_iso: str | None = None,
    end_iso: str | None = None, description: str | None = None,
    attendees: list[str] | None = None, location: str | None = None,
) -> dict:
    """
    Actualiza un evento existente. Solo se modifican los campos que pases.
    Mantiene el link de Meet original (no se regenera).
    """
    svc = _calendar_service(user_email)

    # Leer el evento actual para hacer un patch parcial
    event = svc.events().get(calendarId="primary", eventId=event_id).execute()

    if summary is not None:
        event["summary"] = summary
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    if start_iso is not None:
        event["start"] = {"dateTime": start_iso}
    if end_iso is not None:
        event["end"] = {"dateTime": end_iso}
    if attendees is not None:
        event["attendees"] = [{"email": e} for e in attendees]

    updated = svc.events().update(
        calendarId="primary",
        eventId=event_id,
        body=event,
        sendUpdates="all" if event.get("attendees") else "none",
    ).execute()

    return {
        "id": updated.get("id"),
        "summary": updated.get("summary"),
        "start": updated.get("start", {}).get("dateTime"),
        "end": updated.get("end", {}).get("dateTime"),
        "meet_link": updated.get("hangoutLink"),
        "html_link": updated.get("htmlLink"),
        "attendees": [a.get("email") for a in updated.get("attendees", [])],
    }


# ----- Tool: borrar evento -----

async def delete_event(
    *, user_email: str, event_id: str, notify_attendees: bool = True
) -> dict:
    """Borra un evento del calendario. Notifica a los asistentes si los hay."""
    svc = _calendar_service(user_email)
    svc.events().delete(
        calendarId="primary",
        eventId=event_id,
        sendUpdates="all" if notify_attendees else "none",
    ).execute()
    return {"deleted": True, "event_id": event_id}


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
        "description": (
            "Encuentra slots libres COMUNES entre el invocador y otras personas. "
            "Si pasas attendees, devuelve slots donde TODOS están libres. "
            "Si no, solo del invocador. Respeta horario laboral (9-18h L-V)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_minutes": {"type": "integer", "default": 30},
                "days_ahead": {"type": "integer", "default": 5},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de correos de las personas con las que se quiere coordinar. Mafe revisará su free/busy y solo devolverá huecos donde todos estén libres.",
                },
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
                "allow_weekend": {
                    "type": "boolean",
                    "default": False,
                    "description": "Por default no agendo en fin de semana. Pásame True solo si el usuario lo pidió explícitamente.",
                },
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
]


SPECS.append({
    "name": "calendar_update_event",
    "description": (
        "Actualiza un evento existente (cambia título, fecha, descripción, asistentes). "
        "Úsalo cuando te digan 'cambia la junta', 'actualiza la junta', 'pon mejor a las 11', etc. "
        "NO crees una junta nueva, actualiza la existente. "
        "Si no tienes el event_id, primero llama calendar_list_events para obtenerlo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "ID del evento (de calendar_list_events)"},
            "summary": {"type": "string", "description": "Nuevo título (opcional)"},
            "start_iso": {"type": "string", "description": "Nueva fecha de inicio ISO 8601 con zona (opcional)"},
            "end_iso": {"type": "string", "description": "Nueva fecha de fin ISO 8601 con zona (opcional)"},
            "description": {"type": "string", "description": "Nueva descripción (opcional)"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Lista completa de attendees (reemplaza la existente)"},
            "location": {"type": "string"},
        },
        "required": ["event_id"],
    },
})

SPECS.append({
    "name": "calendar_delete_event",
    "description": "Borra un evento del calendario. Úsalo cuando te digan 'cancela', 'borra', 'elimina la junta'. Notifica a los attendees por default.",
    "input_schema": {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "notify_attendees": {"type": "boolean", "default": True},
        },
        "required": ["event_id"],
    },
})


DISPATCH = {
    "calendar_list_events": list_events,
    "calendar_find_free_slots": find_free_slots,
    "calendar_create_event": create_event,
    "calendar_update_event": update_event,
    "calendar_delete_event": delete_event,
}
