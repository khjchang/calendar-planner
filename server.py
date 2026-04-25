import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8080"))
GOOGLE_API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "America/Los_Angeles")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_ACCESS_TOKEN = os.environ.get("GOOGLE_ACCESS_TOKEN", "").strip()
LLM_ENABLED = os.environ.get("LLM_ENABLED", "1").strip() != "0"
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).strip().rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "lm-studio").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemma-3-12b").strip()
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))
DEMO_MODE = not bool(GOOGLE_ACCESS_TOKEN)

PARTICIPANTS = {
    "alice": {
        "display_name": "Alice",
        "email": os.environ.get("ALICE_EMAIL", "alice@example.com"),
        "timezone": "America/Los_Angeles",
    },
    "bob": {
        "display_name": "Bob",
        "email": os.environ.get("BOB_EMAIL", "bob@example.com"),
        "timezone": "America/New_York",
    },
    "priya": {
        "display_name": "Priya",
        "email": os.environ.get("PRIYA_EMAIL", "priya@example.com"),
        "timezone": "Asia/Seoul",
    },
}

EMAIL_TO_NAME = {value["email"]: value["display_name"] for value in PARTICIPANTS.values()}

BASE_EVENTS = [
    {
        "id": "evt-1",
        "title": "Design Review",
        "attendees": ["You", "Alice"],
        "start": "2026-04-22T17:00:00Z",
        "end": "2026-04-22T17:30:00Z",
    },
    {
        "id": "evt-2",
        "title": "1:1 with Priya",
        "attendees": ["You", "Priya"],
        "start": "2026-04-22T20:00:00Z",
        "end": "2026-04-22T20:45:00Z",
    },
    {
        "id": "evt-3",
        "title": "Recruiting Sync",
        "attendees": ["You", "Bob"],
        "start": "2026-04-25T21:00:00Z",
        "end": "2026-04-25T22:00:00Z",
    },
]

PARTICIPANT_BUSY = {
    "Alice": [
        {"start": "2026-04-22T18:00:00Z", "end": "2026-04-22T18:30:00Z"},
        {"start": "2026-04-25T21:30:00Z", "end": "2026-04-25T22:00:00Z"},
    ],
    "Bob": [
        {"start": "2026-04-22T22:00:00Z", "end": "2026-04-22T22:30:00Z"},
        {"start": "2026-04-25T20:00:00Z", "end": "2026-04-25T21:00:00Z"},
    ],
    "Priya": [
        {"start": "2026-04-22T20:00:00Z", "end": "2026-04-22T21:00:00Z"},
        {"start": "2026-04-25T18:00:00Z", "end": "2026-04-25T19:00:00Z"},
    ],
}

DISPLAY_ZONES = [
    ("PT", "America/Los_Angeles"),
    ("ET", "America/New_York"),
    ("KST", "Asia/Seoul"),
]

DEMO_EVENTS = deepcopy(BASE_EVENTS)
PENDING_ACTIONS: dict[str, dict[str, Any]] = {}
CONVERSATION_STATE: dict[str, Any] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def format_display(date_text: str, timezone_name: str) -> str:
    dt = parse_iso(date_text)
    return dt.astimezone().astimezone(datetime.now().astimezone().tzinfo).strftime("%a, %b %d %I:%M %p")


def format_in_zone(date_text: str, timezone_name: str) -> str:
    dt = parse_iso(date_text)
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(timezone_name)
        return dt.astimezone(zone).strftime("%a, %b %d %I:%M %p")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def preview_for_range(start_text: str, end_text: str) -> list[dict[str, str]]:
    return [
        {
            "label": label,
            "start": format_in_zone(start_text, zone),
            "end": format_in_zone(end_text, zone),
        }
        for label, zone in DISPLAY_ZONES
    ]


def extract_llm_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for item in content:
                text = item.get("text")
                if text:
                    chunks.append(text)
            return "".join(chunks).strip()
    if isinstance(payload.get("output_text"), str) and payload["output_text"]:
        return payload["output_text"].strip()
    return ""


def parse_json_loose(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def llm_parse_request(user_text: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["book", "reschedule", "delete", "unknown"],
            },
            "title": {"type": "string"},
            "target_title": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "duration_minutes": {"type": "integer"},
            "day_phrase": {"type": "string"},
            "exact_time_phrase": {"type": "string"},
            "window_phrase": {"type": "string"},
            "should_clarify": {"type": "boolean"},
            "clarification_question": {"type": "string"},
        },
        "required": [
            "intent",
            "title",
            "target_title",
            "attendees",
            "duration_minutes",
            "day_phrase",
            "exact_time_phrase",
            "window_phrase",
            "should_clarify",
            "clarification_question",
        ],
    }
    system_prompt = (
        "You extract calendar scheduling intent from user requests. "
        "Return JSON only. Normalize attendees to names when obvious. "
        "Use should_clarify=true when date, time, timezone, attendees, or target event are too ambiguous "
        "to safely act. Keep day_phrase, exact_time_phrase, and window_phrase short and literal."
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Return one JSON object with keys: "
                    "intent, title, target_title, attendees, duration_minutes, day_phrase, "
                    "exact_time_phrase, window_phrase, should_clarify, clarification_question.\n"
                    "Allowed intent values: book, reschedule, delete, unknown.\n"
                    "Use empty strings or empty arrays when fields are missing.\n"
                    f"User request: {user_text}"
                ),
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "calendar_request",
                "schema": schema,
            },
        },
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(f"{LLM_BASE_URL}/chat/completions", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_SECONDS) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"LLM API error: {exc.code} {detail}") from exc

    text = extract_llm_text(raw)
    if not text:
        raise RuntimeError("LLM parser returned no text output.")
    return parse_json_loose(text)


class CalendarStore:
    def __init__(self) -> None:
        self.demo_mode = DEMO_MODE

    def _request(self, method: str, url: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if self.demo_mode:
            raise RuntimeError("HTTP request is not used in demo mode.")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"Google Calendar API error: {exc.code} {detail}") from exc

    def list_state(self) -> list[dict[str, Any]]:
        if self.demo_mode:
            return sorted(deepcopy(DEMO_EVENTS), key=lambda item: item["start"])

        time_min = isoformat_utc(utc_now() - timedelta(days=1))
        time_max = isoformat_utc(utc_now() + timedelta(days=14))
        params = urllib.parse.urlencode(
            {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
            }
        )
        data = self._request(
            "GET",
            f"{GOOGLE_API_BASE}/calendars/{urllib.parse.quote(CALENDAR_ID, safe='')}/events?{params}",
        )
        events = []
        for item in data.get("items", []):
            start = item.get("start", {}).get("dateTime")
            end = item.get("end", {}).get("dateTime")
            if not start or not end:
                continue
            attendees = [attendee.get("email", "") for attendee in item.get("attendees", [])]
            events.append(
                {
                    "id": item["id"],
                    "title": item.get("summary", "(No title)"),
                    "attendees": attendees,
                    "start": start,
                    "end": end,
                }
            )
        return events

    def reset_demo(self) -> list[dict[str, Any]]:
        global DEMO_EVENTS
        DEMO_EVENTS = deepcopy(BASE_EVENTS)
        PENDING_ACTIONS.clear()
        CONVERSATION_STATE.clear()
        return self.list_state()

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        if self.demo_mode:
            for event in DEMO_EVENTS:
                if event["id"] == event_id:
                    return deepcopy(event)
            return None
        data = self._request(
            "GET",
            f"{GOOGLE_API_BASE}/calendars/{urllib.parse.quote(CALENDAR_ID, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
        )
        start = data.get("start", {}).get("dateTime")
        end = data.get("end", {}).get("dateTime")
        if not start or not end:
            return None
        attendees = [attendee.get("email", "") for attendee in data.get("attendees", [])]
        return {
            "id": data["id"],
            "title": data.get("summary", "(No title)"),
            "attendees": attendees,
            "start": start,
            "end": end,
        }

    def search_event_by_title(self, title: str) -> Optional[dict[str, Any]]:
        title_lower = title.lower()
        for event in self.list_state():
            if title_lower in event["title"].lower():
                return event
        return None

    def get_busy_blocks(
        self,
        time_min: str,
        time_max: str,
        participant_names: Optional[list[str]] = None,
        participant_emails: Optional[list[str]] = None,
    ) -> list[dict[str, str]]:
        if self.demo_mode:
            busy = []
            for event in DEMO_EVENTS:
                busy.append({"start": event["start"], "end": event["end"], "source": "primary"})
            for name in participant_names or []:
                for block in PARTICIPANT_BUSY.get(name, []):
                    busy.append({**block, "source": name})
            return busy

        items = [{"id": CALENDAR_ID}]
        for email in participant_emails or []:
            items.append({"id": email})
        payload = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": "UTC",
            "items": items,
        }
        data = self._request("POST", f"{GOOGLE_API_BASE}/freeBusy", payload)
        busy = []
        for calendar_id, value in data.get("calendars", {}).items():
            for block in value.get("busy", []):
                busy.append({"start": block["start"], "end": block["end"], "source": calendar_id})
        return busy

    def create_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        attendees: list[str],
        timezone_display: str,
    ) -> dict[str, Any]:
        if self.demo_mode:
            event = {
                "id": f"evt-{secrets.token_hex(3)}",
                "title": title,
                "attendees": ["You", *display_attendees(attendees)],
                "start": start_time,
                "end": end_time,
            }
            DEMO_EVENTS.append(event)
            return event

        payload = {
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone_display},
            "end": {"dateTime": end_time, "timeZone": timezone_display},
            "attendees": [{"email": attendee} for attendee in attendees],
        }
        data = self._request(
            "POST",
            f"{GOOGLE_API_BASE}/calendars/{urllib.parse.quote(CALENDAR_ID, safe='')}/events",
            payload,
        )
        return {
            "id": data["id"],
            "title": data.get("summary", title),
            "attendees": display_attendees(attendees),
            "start": data["start"]["dateTime"],
            "end": data["end"]["dateTime"],
        }

    def delete_event(self, event_id: str) -> dict[str, Any]:
        if self.demo_mode:
            global DEMO_EVENTS
            before = len(DEMO_EVENTS)
            DEMO_EVENTS = [event for event in DEMO_EVENTS if event["id"] != event_id]
            return {"ok": len(DEMO_EVENTS) != before}

        self._request(
            "DELETE",
            f"{GOOGLE_API_BASE}/calendars/{urllib.parse.quote(CALENDAR_ID, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
        )
        return {"ok": True}

    def reschedule_event(self, event_id: str, new_start_time: str, new_end_time: str) -> dict[str, Any]:
        if self.demo_mode:
            for event in DEMO_EVENTS:
                if event["id"] == event_id:
                    event["start"] = new_start_time
                    event["end"] = new_end_time
                    return deepcopy(event)
            raise RuntimeError("Event not found.")

        payload = {
            "start": {"dateTime": new_start_time, "timeZone": DEFAULT_TIMEZONE},
            "end": {"dateTime": new_end_time, "timeZone": DEFAULT_TIMEZONE},
        }
        data = self._request(
            "PATCH",
            f"{GOOGLE_API_BASE}/calendars/{urllib.parse.quote(CALENDAR_ID, safe='')}/events/{urllib.parse.quote(event_id, safe='')}",
            payload,
        )
        return {
            "id": data["id"],
            "title": data.get("summary", "(No title)"),
            "attendees": [attendee.get("email", "") for attendee in data.get("attendees", [])],
            "start": data["start"]["dateTime"],
            "end": data["end"]["dateTime"],
        }


STORE = CalendarStore()


def capitalize(name: str) -> str:
    return name[:1].upper() + name[1:].lower()


def participant_emails(names: list[str]) -> list[str]:
    emails = []
    for name in names:
        record = PARTICIPANTS.get(name.lower())
        if record:
            emails.append(record["email"])
    return emails


def display_attendees(attendees: list[str]) -> list[str]:
    return [EMAIL_TO_NAME.get(attendee, attendee) for attendee in attendees]


def build_title(attendees: list[str]) -> str:
    return "New Meeting" if not attendees else f"{' + '.join(attendees)} Sync"


def parse_day_reference(text: str) -> datetime:
    base = utc_now()
    if "tomorrow" in text:
        return base + timedelta(days=1)

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for index, day_name in enumerate(weekdays):
        if day_name in text:
            current = base
            target_weekday = index
            while current.weekday() != target_weekday:
                current += timedelta(days=1)
            return current
    return base


def parse_explicit_time(text: str) -> Optional[dict[str, Any]]:
    import re

    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(pt|et|kst)?", text, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "pm":
        hour += 12
    minute = int(match.group(2) or 0)
    zone_token = (match.group(4) or "pt").lower()
    zone = {
        "pt": "America/Los_Angeles",
        "et": "America/New_York",
        "kst": "Asia/Seoul",
    }.get(zone_token, DEFAULT_TIMEZONE)
    return {"hours": hour, "minutes": minute, "zone": zone}


def parse_window(text: str, explicit: Optional[dict[str, Any]]) -> dict[str, Any]:
    import re

    if explicit:
        return {"start_hour": explicit["hours"], "end_hour": explicit["hours"] + 2, "zone": explicit["zone"]}

    lower = text.lower()
    if "morning" in lower:
        return {"start_hour": 9, "end_hour": 12, "zone": DEFAULT_TIMEZONE}
    if "afternoon" in lower:
        return {"start_hour": 13, "end_hour": 17, "zone": DEFAULT_TIMEZONE}

    match = re.search(r"after\s+(\d{1,2})\s*(am|pm)\s*(pt|et|kst)?", lower)
    if match:
        start_hour = int(match.group(1)) % 12
        if match.group(2) == "pm":
            start_hour += 12
        zone = {
            "pt": "America/Los_Angeles",
            "et": "America/New_York",
            "kst": "Asia/Seoul",
        }.get((match.group(3) or "pt").lower(), DEFAULT_TIMEZONE)
        return {"start_hour": start_hour, "end_hour": 18, "zone": zone}

    return {"start_hour": 9, "end_hour": 17, "zone": DEFAULT_TIMEZONE}


def zoned_datetime(day: datetime, hours: int, minutes: int, zone_name: str) -> datetime:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(zone_name)
    local = datetime(day.year, day.month, day.day, hours, minutes, tzinfo=zone)
    return local.astimezone(timezone.utc)


def parse_request_rule_based(text: str) -> dict[str, Any]:
    import re

    lower = text.lower()
    duration_match = re.search(r"(\d+)\s*(minute|min)", lower)
    duration_minutes = int(duration_match.group(1)) if duration_match else 30
    attendees_match = re.search(r"with\s+([a-z,\s@.\-and]+)", text, re.I)
    attendees = []
    if attendees_match:
        cleaned = attendees_match.group(1).split(" tomorrow")[0].split(" on ")[0].split(" at ")[0]
        attendees = [capitalize(piece.strip()) for piece in re.split(r",|and", cleaned) if piece.strip()]

    explicit_time = parse_explicit_time(text)
    day = parse_day_reference(lower)
    window = parse_window(text, explicit_time)
    preferred_start = None
    if explicit_time:
        preferred_start = zoned_datetime(day, explicit_time["hours"], explicit_time["minutes"], explicit_time["zone"])

    if lower.startswith("reschedule") or lower.startswith("move "):
        target_title = re.sub(r"^(reschedule|move)\s+", "", text, flags=re.I)
        target_title = re.sub(r"\s+to.+$", "", target_title, flags=re.I).strip()
        return {
            "intent": "reschedule",
            "target_title": target_title,
            "day": day,
            "window": window,
            "preferred_start": preferred_start,
            "duration_minutes": duration_minutes,
        }

    if lower.startswith("delete") or lower.startswith("cancel"):
        target_title = re.sub(r"^(delete|cancel)\s+", "", text, flags=re.I).strip().rstrip(".")
        return {"intent": "delete", "target_title": target_title}

    if any(phrase in lower for phrase in ["book", "schedule", "find time", "set up"]):
        return {
            "intent": "book",
            "title": build_title(attendees),
            "attendees": attendees,
            "participant_emails": participant_emails(attendees),
            "day": day,
            "window": window,
            "preferred_start": preferred_start,
            "duration_minutes": duration_minutes,
        }

    return {"intent": "unknown"}


def parse_request(text: str) -> dict[str, Any]:
    if not LLM_ENABLED:
        parsed = parse_request_rule_based(text)
        parsed["_trace"] = [
            trace_item("request_parser", {"request": text, "mode": "rule_based"}, parsed)
        ]
        return parsed

    try:
        llm = llm_parse_request(text)
        attendees = [capitalize(item.strip()) for item in llm.get("attendees", []) if item.strip()]
        duration_minutes = int(llm.get("duration_minutes") or 30)
        day_phrase = (llm.get("day_phrase") or text).lower()
        exact_time_phrase = llm.get("exact_time_phrase") or ""
        window_phrase = llm.get("window_phrase") or ""
        explicit_time = parse_explicit_time(exact_time_phrase)
        day = parse_day_reference(day_phrase)
        window = parse_window(window_phrase or exact_time_phrase or "", explicit_time)
        preferred_start = None
        if explicit_time:
            preferred_start = zoned_datetime(day, explicit_time["hours"], explicit_time["minutes"], explicit_time["zone"])

        if llm.get("should_clarify"):
            parsed = {
                "intent": "clarify",
                "question": llm.get("clarification_question") or "Could you clarify the date, time, or attendees?",
            }
        elif llm["intent"] == "book":
            parsed = {
                "intent": "book",
                "title": llm.get("title") or build_title(attendees),
                "attendees": attendees,
                "participant_emails": participant_emails(attendees),
                "day": day,
                "window": window,
                "preferred_start": preferred_start,
                "duration_minutes": duration_minutes,
            }
        elif llm["intent"] == "reschedule":
            parsed = {
                "intent": "reschedule",
                "target_title": llm.get("target_title") or llm.get("title") or "",
                "day": day,
                "window": window,
                "preferred_start": preferred_start,
                "duration_minutes": duration_minutes,
            }
        elif llm["intent"] == "delete":
            parsed = {
                "intent": "delete",
                "target_title": llm.get("target_title") or llm.get("title") or "",
            }
        else:
            parsed = {"intent": "unknown"}

        parsed["_trace"] = [
            trace_item(
                "request_parser",
                {"request": text, "mode": "llm", "model": LLM_MODEL, "base_url": LLM_BASE_URL},
                llm,
            )
        ]
        return parsed
    except Exception as exc:
        parsed = parse_request_rule_based(text)
        parsed["_trace"] = [
            trace_item(
                "request_parser",
                {"request": text, "mode": "llm", "model": LLM_MODEL, "base_url": LLM_BASE_URL},
                {"error": str(exc), "fallback": "rule_based"},
            ),
            trace_item("request_parser", {"request": text, "mode": "rule_based"}, parsed),
        ]
        return parsed


def slot_summary(slot: dict[str, str]) -> str:
    return " | ".join(
        f"{label}: {format_in_zone(slot['start'], zone)} - {format_in_zone(slot['end'], zone)}"
        for label, zone in DISPLAY_ZONES
    )


def same_calendar_day(left: str, right: str, zone_name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(zone_name)
        return parse_iso(left).astimezone(zone).date() == parse_iso(right).astimezone(zone).date()
    except Exception:
        return left[:10] == right[:10]


def find_existing_booking(parsed: dict[str, Any]) -> Optional[dict[str, Any]]:
    requested_attendees = sorted(display_attendees(parsed.get("participant_emails", [])))
    request_day = isoformat_utc(parsed["day"])
    for event in STORE.list_state():
        if event["title"] != parsed["title"]:
            continue
        existing_attendees = sorted(attendee for attendee in event.get("attendees", []) if attendee != "You")
        if existing_attendees != requested_attendees:
            continue
        duration = int((parse_iso(event["end"]) - parse_iso(event["start"])).total_seconds() // 60)
        if duration != parsed["duration_minutes"]:
            continue
        if not same_calendar_day(event["start"], request_day, parsed["window"]["zone"]):
            continue
        return event
    return None


def find_slots(payload: dict[str, Any]) -> list[dict[str, str]]:
    day = payload["day"]
    zone = payload["window"]["zone"]
    start_bound = zoned_datetime(day, payload["window"]["start_hour"], 0, zone)
    end_bound = zoned_datetime(day, payload["window"]["end_hour"], 0, zone)
    busy_blocks = STORE.get_busy_blocks(
        isoformat_utc(start_bound),
        isoformat_utc(end_bound),
        payload.get("attendees", []),
        payload.get("participant_emails", []),
    )
    ignore_event_id = payload.get("ignore_event_id")
    if ignore_event_id:
        event = STORE.get_event(ignore_event_id)
        if event:
            busy_blocks = [
                block
                for block in busy_blocks
                if not (block["start"] == event["start"] and block["end"] == event["end"] and block["source"] in {"primary", CALENDAR_ID})
            ]

    step = timedelta(minutes=30)
    duration = timedelta(minutes=payload["duration_minutes"])
    slots = []
    cursor = start_bound
    while cursor + duration <= end_bound:
        slot_end = cursor + duration
        blocked = False
        for block in busy_blocks:
            if overlaps(cursor, slot_end, parse_iso(block["start"]), parse_iso(block["end"])):
                blocked = True
                break
        if not blocked:
            slots.append({"start": isoformat_utc(cursor), "end": isoformat_utc(slot_end)})
        cursor += step
    return slots


def trace_item(tool: str, tool_input: dict[str, Any], tool_output: Any) -> dict[str, Any]:
    return {"tool": tool, "input": sanitize(tool_input), "output": sanitize(tool_output)}


def sanitize(value: Any) -> Any:
    if isinstance(value, datetime):
        return isoformat_utc(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    return value


def response_json(
    *,
    status: str = "ok",
    message: str,
    detail: str = "",
    tool_trace: Optional[list[dict[str, Any]]] = None,
    suggestions: Optional[list[dict[str, Any]]] = None,
    confirmation_token: Optional[str] = None,
    preview: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "detail": detail,
        "tool_trace": tool_trace or [],
        "suggestions": suggestions or [],
        "confirmation_token": confirmation_token,
        "preview": preview or [],
    }


def set_active_request(parsed: dict[str, Any]) -> None:
    stored = sanitize(parsed)
    stored.pop("_trace", None)
    CONVERSATION_STATE["active_request"] = stored


def get_active_request() -> Optional[dict[str, Any]]:
    active = CONVERSATION_STATE.get("active_request")
    return deepcopy(active) if active else None


def is_followup_request(text: str) -> bool:
    lower = text.lower().strip()
    triggers = [
        "later",
        "more late",
        "늦",
        "bob 빼",
        "without bob",
        "remove bob",
        "kst",
        "korea time",
        "seoul time",
    ]
    return any(trigger in lower for trigger in triggers)


def apply_followup_request(text: str, active: dict[str, Any]) -> Optional[dict[str, Any]]:
    lower = text.lower().strip()
    updated = deepcopy(active)
    changes = []

    if "bob 빼" in lower or "without bob" in lower or "remove bob" in lower:
        attendees = [name for name in updated.get("attendees", []) if name.lower() != "bob"]
        updated["attendees"] = attendees
        updated["participant_emails"] = participant_emails(attendees)
        if updated.get("intent") == "book":
            updated["title"] = build_title(attendees)
        changes.append("removed Bob from the attendee list")

    if "kst" in lower or "korea time" in lower or "seoul time" in lower:
        updated.setdefault("window", {})
        updated["window"]["zone"] = "Asia/Seoul"
        changes.append("changed the display/search timezone to KST")

    if "later" in lower or "more late" in lower or "늦" in lower:
        window = updated.get("window")
        if window:
            new_start = min(window["start_hour"] + 1, 20)
            new_end = min(max(window["end_hour"], new_start + 1), 22)
            window["start_hour"] = new_start
            window["end_hour"] = new_end
            updated["preferred_start"] = None
            changes.append("shifted the requested window later")

    if not changes:
        return None

    updated["_trace"] = [
        trace_item(
            "followup_transform",
            {"request": text, "based_on": active},
            {"updated_request": updated, "changes": changes},
        )
    ]
    return updated


def describe_parsed_request(parsed: dict[str, Any]) -> tuple[str, str]:
    if parsed["intent"] == "book":
        attendees = ", ".join(parsed.get("attendees", [])) or "no attendees"
        detail = (
            f'I understood this as a new meeting request for {parsed["duration_minutes"]} minutes '
            f'with {attendees}.'
        )
        if parsed.get("preferred_start"):
            detail += f" Requested exact time: {slot_summary({'start': isoformat_utc(parsed['preferred_start']), 'end': isoformat_utc(parsed['preferred_start'] + timedelta(minutes=parsed['duration_minutes']))})}."
        else:
            detail += (
                f" Search window: {parsed['window']['start_hour']}:00-{parsed['window']['end_hour']}:00 "
                f"in {parsed['window']['zone']}."
            )
        return "I parsed your booking request like this.", detail

    if parsed["intent"] == "reschedule":
        detail = (
            f'I understood this as rescheduling "{parsed["target_title"]}". '
            f"Requested window: {parsed['window']['start_hour']}:00-{parsed['window']['end_hour']}:00 "
            f"in {parsed['window']['zone']}."
        )
        return "I parsed your reschedule request like this.", detail

    if parsed["intent"] == "delete":
        return "I parsed your delete request like this.", f'I am preparing to delete "{parsed["target_title"]}".'

    return "I need a quick confirmation.", "Please confirm this interpretation before I touch the calendar."


def build_parse_confirmation(parsed: dict[str, Any]) -> dict[str, Any]:
    token = secrets.token_urlsafe(10)
    stored = sanitize(parsed)
    stored.pop("_trace", None)
    PENDING_ACTIONS[token] = {"type": "parsed_request", "parsed": stored}
    set_active_request(parsed)
    message, detail = describe_parsed_request(parsed)
    return response_json(
        status="needs_confirmation",
        message=message,
        detail=f'{detail} You can also say things like "later", "without Bob", or "show in KST".',
        tool_trace=list(parsed.get("_trace", [])),
        suggestions=[{"summary": "Yes, continue with this interpretation."}],
        confirmation_token=token,
    )


def handle_booking(parsed: dict[str, Any]) -> dict[str, Any]:
    tool_trace = list(parsed.pop("_trace", []))
    existing = find_existing_booking(parsed)
    if existing:
        tool_trace.append(trace_item("duplicate_check", parsed, existing))
        return response_json(
            status="already_exists",
            message=f'An existing matching meeting is already on the calendar: "{existing["title"]}".',
            detail=slot_summary(existing),
            tool_trace=tool_trace,
            preview=preview_for_range(existing["start"], existing["end"]),
        )

    slots = find_slots(parsed)
    tool_trace.append(trace_item("get_available_slots", parsed, slots[:5]))

    if not slots:
        return response_json(
            status="no_slots",
            message="No valid slot matched all constraints.",
            detail="The agent checked existing calendar conflicts and participant availability but could not find an opening.",
            tool_trace=tool_trace,
        )

    if parsed.get("preferred_start"):
        preferred_iso = isoformat_utc(parsed["preferred_start"])
        requested_slot = next((slot for slot in slots if slot["start"] == preferred_iso), None)
        if requested_slot:
            event = STORE.create_event(
                parsed["title"],
                requested_slot["start"],
                requested_slot["end"],
                parsed["participant_emails"],
                parsed["window"]["zone"],
            )
            tool_trace.append(trace_item("create_event", event, {"ok": True}))
            return response_json(
                message=f'Booked "{event["title"]}".',
                detail=slot_summary(event),
                tool_trace=tool_trace,
                preview=preview_for_range(event["start"], event["end"]),
            )

        suggestions = [{"summary": slot_summary(slot), **slot} for slot in slots[:3]]
        token = secrets.token_urlsafe(10)
        PENDING_ACTIONS[token] = {
            "type": "book",
            "title": parsed["title"],
            "attendees": parsed["participant_emails"],
            "timezone": parsed["window"]["zone"],
            "options": slots[:3],
        }
        return response_json(
            status="needs_confirmation",
            message="The requested exact time is busy.",
            detail="Here are the nearest open alternatives. Confirm one to create the event.",
            tool_trace=tool_trace,
            suggestions=suggestions,
            confirmation_token=token,
            preview=preview_for_range(slots[0]["start"], slots[0]["end"]),
        )

    event = STORE.create_event(
        parsed["title"],
        slots[0]["start"],
        slots[0]["end"],
        parsed["participant_emails"],
        parsed["window"]["zone"],
    )
    tool_trace.append(trace_item("create_event", event, {"ok": True}))
    return response_json(
        message=f'Booked "{event["title"]}".',
        detail=slot_summary(event),
        tool_trace=tool_trace,
        preview=preview_for_range(event["start"], event["end"]),
    )


def handle_reschedule(parsed: dict[str, Any]) -> dict[str, Any]:
    tool_trace = list(parsed.pop("_trace", []))
    event = STORE.search_event_by_title(parsed["target_title"])
    tool_trace.append(trace_item("lookup_event", {"title": parsed["target_title"]}, event or {"ok": False}))
    if not event:
        return response_json(
            status="not_found",
            message=f'Could not find "{parsed["target_title"]}" on the calendar.',
            detail="Try a more exact event title.",
            tool_trace=tool_trace,
        )

    duration_minutes = int((parse_iso(event["end"]) - parse_iso(event["start"])).total_seconds() // 60)
    participants = [name for name in event.get("attendees", []) if name != "You"]
    payload = {
        "day": parsed["day"],
        "window": parsed["window"],
        "duration_minutes": duration_minutes,
        "attendees": participants,
        "participant_emails": participant_emails(participants),
        "ignore_event_id": event["id"],
        "preferred_start": parsed.get("preferred_start"),
    }
    slots = find_slots(payload)
    tool_trace.append(trace_item("get_available_slots", payload, slots[:5]))
    if not slots:
        return response_json(
            status="no_slots",
            message="No clean reschedule target was found.",
            detail="Every slot in the requested window would create a conflict.",
            tool_trace=tool_trace,
        )

    token = secrets.token_urlsafe(10)
    PENDING_ACTIONS[token] = {"type": "reschedule", "event_id": event["id"], "title": event["title"], "options": slots[:3]}
    suggestions = [{"summary": slot_summary(slot), **slot} for slot in slots[:3]]
    return response_json(
        status="needs_confirmation",
        message=f'Found new options for "{event["title"]}".',
        detail="The agent is waiting for confirmation before modifying the existing event.",
        tool_trace=tool_trace,
        suggestions=suggestions,
        confirmation_token=token,
        preview=preview_for_range(slots[0]["start"], slots[0]["end"]),
    )


def handle_delete(parsed: dict[str, Any]) -> dict[str, Any]:
    tool_trace = list(parsed.pop("_trace", []))
    event = STORE.search_event_by_title(parsed["target_title"])
    tool_trace.append(trace_item("lookup_event", {"title": parsed["target_title"]}, event or {"ok": False}))
    if not event:
        return response_json(
            status="not_found",
            message=f'Could not find "{parsed["target_title"]}" on the calendar.',
            detail="Try a more exact event title.",
            tool_trace=tool_trace,
        )

    token = secrets.token_urlsafe(10)
    PENDING_ACTIONS[token] = {"type": "delete", "event_id": event["id"], "title": event["title"]}
    return response_json(
        status="needs_confirmation",
        message=f'Ready to delete "{event["title"]}".',
        detail="Confirm to remove the event from the calendar.",
        tool_trace=tool_trace,
        confirmation_token=token,
        preview=preview_for_range(event["start"], event["end"]),
    )


def confirm_pending(token: str, option_index: int = 0) -> dict[str, Any]:
    pending = PENDING_ACTIONS.pop(token, None)
    if not pending:
        return response_json(status="invalid", message="No pending action was found for that token.")

    tool_trace = []
    if pending["type"] == "parsed_request":
        parsed = pending["parsed"]
        set_active_request(parsed)
        parsed["_trace"] = [trace_item("parse_confirmation", {"confirmed": True}, parsed)]
        if parsed["intent"] == "book":
            return handle_booking(parsed)
        if parsed["intent"] == "reschedule":
            return handle_reschedule(parsed)
        if parsed["intent"] == "delete":
            return handle_delete(parsed)
        return response_json(status="invalid", message="Unsupported parsed request.")

    if pending["type"] == "book":
        options = pending["options"]
        choice = options[min(max(option_index, 0), len(options) - 1)]
        event = STORE.create_event(
            pending["title"],
            choice["start"],
            choice["end"],
            pending["attendees"],
            pending["timezone"],
        )
        tool_trace.append(trace_item("create_event", event, {"ok": True}))
        return response_json(
            message=f'Booked "{event["title"]}" after confirmation.',
            detail=slot_summary(event),
            tool_trace=tool_trace,
            preview=preview_for_range(event["start"], event["end"]),
        )

    if pending["type"] == "reschedule":
        options = pending["options"]
        choice = options[min(max(option_index, 0), len(options) - 1)]
        event = STORE.reschedule_event(pending["event_id"], choice["start"], choice["end"])
        tool_trace.append(trace_item("reschedule_event", {"event_id": pending["event_id"], **choice}, {"ok": True}))
        return response_json(
            message=f'Rescheduled "{event["title"]}".',
            detail=slot_summary(event),
            tool_trace=tool_trace,
            preview=preview_for_range(event["start"], event["end"]),
        )

    if pending["type"] == "delete":
        result = STORE.delete_event(pending["event_id"])
        tool_trace.append(trace_item("delete_event", {"event_id": pending["event_id"]}, result))
        return response_json(
            message=f'Deleted "{pending["title"]}".',
            detail="The event was removed from the calendar.",
            tool_trace=tool_trace,
        )

    return response_json(status="invalid", message="Unsupported pending action.")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _write_json(self, payload: dict[str, Any], status_code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, html: str, status_code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        config_payload = {
            "demo_mode": STORE.demo_mode,
            "calendar_id": CALENDAR_ID,
            "default_timezone": DEFAULT_TIMEZONE,
            "ai_enabled": LLM_ENABLED,
            "llm_model": LLM_MODEL,
            "llm_base_url": LLM_BASE_URL,
        }
        if self.path == "/api/config.json":
            self._write_json(config_payload)
            return
        if self.path == "/api/config":
            mode = "Demo Data" if STORE.demo_mode else "Google Calendar Live"
            html = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Calendar Agent Config</title>
    <style>
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #202124;
        background: #f7f7f8;
      }}
      .wrap {{
        max-width: 760px;
        margin: 48px auto;
        background: white;
        border: 1px solid #e3e3e6;
        border-radius: 14px;
        padding: 24px;
      }}
      h1 {{ margin-top: 0; }}
      .muted {{ color: #5f6368; }}
      code {{
        background: #f1f3f4;
        border-radius: 6px;
        padding: 2px 6px;
      }}
      .row {{ margin: 8px 0; }}
      a {{ color: #0b57d0; text-decoration: none; }}
    </style>
  </head>
  <body>
    <main class="wrap">
      <h1>Calendar Agent Status</h1>
      <p class="muted">This page is human-readable. The raw API JSON is at <a href="/api/config.json">/api/config.json</a>.</p>
      <p class="row"><strong>Mode:</strong> {mode}</p>
      <p class="row"><strong>demo_mode:</strong> <code>{str(STORE.demo_mode).lower()}</code></p>
      <p class="row"><strong>calendar_id:</strong> <code>{CALENDAR_ID}</code></p>
      <p class="row"><strong>default_timezone:</strong> <code>{DEFAULT_TIMEZONE}</code></p>
      <p class="row"><strong>ai_enabled:</strong> <code>{str(LLM_ENABLED).lower()}</code></p>
      <p class="row"><strong>llm_model:</strong> <code>{LLM_MODEL}</code></p>
      <p class="row"><strong>llm_base_url:</strong> <code>{LLM_BASE_URL}</code></p>
      <p class="row"><a href="/">Open app UI</a></p>
    </main>
  </body>
</html>"""
            self._write_html(html)
            return
        if self.path == "/api/calendar/state":
            self._write_json({"events": STORE.list_state()})
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            if self.path == "/api/demo/reset":
                self._write_json({"events": STORE.reset_demo()})
                return

            payload = self._read_json()

            if self.path == "/api/tools/get_available_slots":
                slots = find_slots(payload)
                self._write_json({"slots": slots, "preview": preview_for_range(slots[0]["start"], slots[0]["end"]) if slots else []})
                return

            if self.path == "/api/tools/create_event":
                event = STORE.create_event(
                    payload["title"],
                    payload["start_time"],
                    payload["end_time"],
                    payload.get("attendees", []),
                    payload.get("timezone_display", DEFAULT_TIMEZONE),
                )
                self._write_json(event)
                return

            if self.path == "/api/tools/delete_event":
                self._write_json(STORE.delete_event(payload["event_id"]))
                return

            if self.path == "/api/tools/reschedule_event":
                event = STORE.reschedule_event(payload["event_id"], payload["new_start_time"], payload["new_end_time"])
                self._write_json(event)
                return

            if self.path == "/api/agent/run":
                request_text = payload.get("request", "")
                active = get_active_request()
                if active and is_followup_request(request_text):
                    updated = apply_followup_request(request_text, active)
                    if updated:
                        self._write_json(build_parse_confirmation(updated))
                        return

                parsed = parse_request(request_text)
                parse_trace = list(parsed.get("_trace", []))
                if parsed["intent"] == "clarify":
                    self._write_json(
                        response_json(
                            status="clarify",
                            message=parsed["question"],
                            detail="The AI planner needs one more detail before it can safely use the calendar tools.",
                            tool_trace=parse_trace,
                        )
                    )
                    return
                if parsed["intent"] in {"book", "reschedule", "delete"}:
                    self._write_json(build_parse_confirmation(parsed))
                    return
                if parsed["intent"] == "book":
                    self._write_json(handle_booking(parsed))
                    return
                if parsed["intent"] == "reschedule":
                    self._write_json(handle_reschedule(parsed))
                    return
                if parsed["intent"] == "delete":
                    self._write_json(handle_delete(parsed))
                    return
                self._write_json(
                    response_json(
                        status="unknown",
                        message="I could not confidently parse that request.",
                        detail="Try booking, deleting, or rescheduling language with a day, time, or time window.",
                        tool_trace=parse_trace,
                    )
                )
                return

            if self.path == "/api/agent/confirm":
                self._write_json(confirm_pending(payload["token"], int(payload.get("option_index", 0))))
                return

            self._write_json({"detail": "Not found."}, 404)
        except Exception as exc:
            self._write_json({"detail": str(exc)}, 500)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving on http://{HOST}:{PORT}")
    server.serve_forever()
