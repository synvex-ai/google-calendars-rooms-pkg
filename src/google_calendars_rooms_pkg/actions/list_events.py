from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import requests
from dateutil.parser import isoparse
from loguru import logger
from pydantic import BaseModel

from google_calendars_rooms_pkg.configuration import CustomAddonConfig

from .base import ActionResponse, OutputBase, TokensSchema


class ActionInput(BaseModel):
    calendarId: str
    maxResults: Optional[int] = None
    timeMin: Union[datetime, str]
    timeMax: Optional[Union[datetime, str]] = None

class ActionOutput(OutputBase):
    data: Optional[dict] = None

def _to_rfc3339_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.isoformat().replace("+00:00", "Z")

def _coerce_dt(value: Union[datetime, str, None], name: str) -> Optional[datetime]:
    """
    Accepts a datetime or an ISO-8601 string (with 'Z' or offset) and returns an aware datetime.
    If naive, assumes UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        dt = isoparse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise TypeError(f"{name} must be datetime or ISO-8601 string")

def list_events(
    config: CustomAddonConfig,
    calendarId: Optional[str] = None,
    maxResults: Optional[int] = None,
    timeMin: Union[datetime, str, None] = None,
    timeMax: Optional[Union[datetime, str]] = None
) -> ActionResponse:

    logger.debug("Template rooms package - list event executed successfully!")
    logger.debug(f"Input received: calendarId={calendarId}, maxResults={maxResults}, timeMin={timeMin}, timeMax={timeMax}")

    tokens = TokensSchema(stepAmount=2000, totalCurrentAmount=16236)

    calendarId = calendarId or getattr(config, "default_calendar_id", "primary")
    if timeMin is None:
        msg = "Missing required parameter: timeMin."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    try:
        time_min_dt = _coerce_dt(timeMin, "timeMin")
        time_max_dt = _coerce_dt(timeMax, "timeMax") if timeMax is not None else None
    except Exception as e:
        msg = f"Invalid datetime format: {e}"
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    if time_max_dt is not None and time_max_dt <= time_min_dt:
        msg = "timeMax must be strictly after timeMin."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    maxResults = maxResults or getattr(config, "default_max_results", 10)

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

    params: Dict[str, Any] = {
        "maxResults": maxResults,
        "timeMin": _to_rfc3339_utc(time_min_dt),
        "singleEvents": "true",
        "orderBy": "startTime",
    }

    if time_max_dt is not None:
        params["timeMax"] = _to_rfc3339_utc(time_max_dt)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(api_url, headers=headers, params=params, timeout=getattr(config, "request_timeout_s", 10))
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
            logger.warning(f"Calendar API error: {msg}")
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
