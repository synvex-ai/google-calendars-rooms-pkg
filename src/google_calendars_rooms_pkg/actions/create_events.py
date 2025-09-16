from __future__ import annotations

from typing import Optional, List, Dict, Any, Literal, Annotated, Union
from datetime import datetime, date, timezone
import re
import requests
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from google_calendars_rooms_pkg.configuration import CustomAddonConfig
from google_calendars_rooms_pkg.services.credentials import CredentialsRegistry
from .base import ActionResponse, OutputBase, TokensSchema
from dateutil.parser import isoparse


class ActionInput(BaseModel):
    calendarId: str = Field(..., description="Target calendar ID")
    summary: str = Field(..., description="Event title")

    start_dt: Optional[datetime] = Field(None, description="Event start (datetime)")
    end_dt: Optional[datetime] = Field(None, description="Event end (datetime)")

    start_date: Optional[date] = Field(None, description="All-day start date (YYYY-MM-DD)")
    end_date: Optional[date] = Field(None, description="All-day end date (exclusive; J+1)")

    description: Optional[str] = None
    location: Optional[str] = None

    attendees: Optional[Annotated[List[str], Field(min_length=1)]] = Field(
        default=None, description="List of attendee emails"
    )

    colorId: Optional[str] = Field(default=None, description="Event colorId as string (1..11)")
    sendUpdates: Optional[Literal["all", "externalOnly", "none"]] = Field(default=None, description="Who to notify about the change")
    create_conference: bool = Field(default=False, description="If true, create a Google Meet link")
    reminders_overrides: Optional[List[Dict[str, Any]]] = Field(default=None, description="List of reminders overrides; set useDefault=false if provided")


class ActionOutput(OutputBase):
    data: Optional[Dict[str, Any]] = None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s or ""))

def _to_rfc3339_utc(dt: datetime) -> str:
    """Convert a datetime to RFC3339 UTC with 'Z' suffix."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _coerce_dt(value: Union[datetime, str, None], name: str) -> Optional[datetime]:
    """Accept datetime or ISO-8601 string (with 'Z' or offset), return aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        dt = isoparse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise TypeError(f"{name} must be datetime or ISO-8601 string")

def _coerce_date(value: Union[date, str, None], name: str) -> Optional[date]:
    """Accept date or 'YYYY-MM-DD' string, return a date object."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return isoparse(value).date()
    raise TypeError(f"{name} must be date or 'YYYY-MM-DD' string")

def create_events(
    config: CustomAddonConfig,
    calendarId: str,
    summary: str,

    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,

    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    colorId: Optional[str] = None,
    sendUpdates: Optional[Literal["all", "externalOnly", "none"]] = None,
    create_conference: bool = False,
    reminders_overrides: Optional[List[Dict[str, Any]]] = None,
) -> ActionResponse:

    tokens = TokensSchema(stepAmount=2000, totalCurrentAmount=16236)
    logger.debug("[create_event] called")

    missing = []
    if not calendarId:
        missing.append("calendarId")
    if not summary:
        missing.append("summary")

    try:
        start_dt = _coerce_dt(start_dt, "start_dt")
        end_dt = _coerce_dt(end_dt, "end_dt")
    except Exception as e:
        msg = f"Invalid datetime format: {e}"
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    if (start_dt is not None and end_dt is not None) and end_dt <= start_dt:
        msg = "end_dt must be strictly after start_dt."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    try:
        start_date = _coerce_date(start_date, "start_date")
        end_date   = _coerce_date(end_date, "end_date")
    except Exception as e:
        msg = f"Invalid date format: {e}"
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    datetime_pair = start_dt is not None and end_dt is not None
    date_pair = start_date is not None and end_date is not None
    mixed = (start_dt is not None or end_dt is not None) and (start_date is not None or end_date is not None)

    if mixed:
        msg = "Provide either datetime pair (start_dt & end_dt) OR all-day pair (start_date & end_date), not both."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    if not datetime_pair and not date_pair:
        missing.extend(["start_dt&end_dt OR start_date&end_date"])

    if missing:
        msg = f"Missing required parameters: {', '.join(missing)}."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    if datetime_pair:
        if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
            msg = "start_dt and end_dt must be datetime instances."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)
        if end_dt <= start_dt:
            msg = "end_dt must be strictly after start_dt."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)
    else:
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            msg = "start_date and end_date must be date instances (YYYY-MM-DD)."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)
        if end_date <= start_date:
            msg = "end_date must be strictly after start_date (end is exclusive)."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    attendees_block: Optional[List[Dict[str, str]]] = None
    if attendees:
        cleaned = sorted({e.strip() for e in attendees if e and _valid_email(e.strip())})
        if len(cleaned) == 0:
            msg = "All provided attendee emails are invalid."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)
        attendees_block = [{"email": e} for e in cleaned]

    reminders_block: Optional[Dict[str, Any]] = None
    if reminders_overrides:
        invalid = [
            r for r in reminders_overrides
            if not isinstance(r, dict) or "method" not in r or "minutes" not in r
        ]
        if invalid:
            msg = "Invalid reminders_overrides: each item must include 'method' and 'minutes'."
            logger.warning(msg)
            return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)
        reminders_block = {"useDefault": False, "overrides": reminders_overrides}

    body: Dict[str, Any] = {"summary": summary}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if colorId:
        body["colorId"] = colorId
    if attendees_block:
        body["attendees"] = attendees_block
    if reminders_block:
        body["reminders"] = reminders_block

    if datetime_pair:
        body["start"] = {"dateTime": _to_rfc3339_utc(start_dt)}
        body["end"] = {"dateTime": _to_rfc3339_utc(end_dt)}
    else:
        body["start"] = {"date": start_date.isoformat()}
        body["end"] = {"date": end_date.isoformat()}

    params: Dict[str, Any] = {}
    if create_conference:
        body["conferenceData"] = {"createRequest": {"requestId": str(uuid4())}}
        params["conferenceDataVersion"] = 1

    if sendUpdates:
        params["sendUpdates"] = sendUpdates

    required = config.get_required_secrets()
    secret_key_name = getattr(required, "google_calendars_api_key", "google_calendars_api_key")
    access_token = config.secrets.get(secret_key_name) or config.secrets.get("google_calendars_api_key")

    if not access_token:
        msg = "Missing OAuth access_token in secrets."
        logger.error(msg)
        return ActionResponse(
            output=ActionOutput(data={"error": msg}),
            tokens=TokensSchema(stepAmount=0, totalCurrentAmount=0),
            message=msg,
            code=401,
        )

    api_url = f"https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    timeout_s = getattr(config, "request_timeout_s", 10)
    logger.debug(f"[create_event] POST {api_url} params={params} timeout={timeout_s}s")

    try:
        resp = requests.post(api_url, headers=headers, params=params, json=body, timeout=timeout_s)
        status = resp.status_code
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}

        if 200 <= status < 300:
            return ActionResponse(
                output=ActionOutput(data=payload),
                tokens=tokens,
                message="OK",
                code=status,
            )
        else:
            err_msg = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
            msg = err_msg or f"HTTP {status}"
            logger.warning(f"[create_event] Calendar API error: {msg}")
            return ActionResponse(
                output=ActionOutput(data=payload if isinstance(payload, dict) else {"error": msg}),
                tokens=TokensSchema(stepAmount=0, totalCurrentAmount=0),
                message=msg,
                code=status,
            )

    except requests.exceptions.RequestException as e:
        msg = f"Request failed: {e.__class__.__name__}: {e}"
        logger.error(msg)
        return ActionResponse(
            output=ActionOutput(data={"error": str(e)}),
            tokens=TokensSchema(stepAmount=0, totalCurrentAmount=0),
            message=msg,
            code=503,
        )
