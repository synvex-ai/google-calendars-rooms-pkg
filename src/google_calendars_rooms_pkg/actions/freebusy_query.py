from __future__ import annotations

from typing import Optional, List, Dict, Any, Annotated, Union
from datetime import datetime, timezone
import requests
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil.parser import isoparse

from loguru import logger
from pydantic import BaseModel, Field

from ..configuration import CustomAddonConfig
from ..services.credentials import CredentialsRegistry
from .base import ActionResponse, OutputBase, TokensSchema

class ActionInput(BaseModel):
    timeMin: Union[datetime, str] = Field(..., description="Window start (datetime or ISO-8601 string)")
    timeMax: Union[datetime, str] = Field(..., description="Window end (datetime or ISO-8601 string, strictly > timeMin)")
    items: Annotated[List[Union[str, Dict[str, Any]]], Field(min_length=1)] = Field(
        ..., description="Calendars: list of IDs (str) or dicts with key 'id'."
    )
    timeZone: Optional[str] = Field(
        default=None, description="IANA timezone for response (e.g., 'Europe/Paris'). If omitted, UTC."
    )
    calendarExpansionMax: Optional[int] = None
    groupExpansionMax: Optional[int] = None


class ActionOutput(OutputBase):
    data: Optional[Dict[str, Any]] = None

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

def _normalize_items(items: List[Union[str, Dict[str, Any]]]) -> List[str]:
    """Accept ['primary'] or [{'id':'primary'}], strip, dedupe, return sorted list of ids."""
    out = set()
    for it in items or []:
        if isinstance(it, str):
            s = it.strip()
        elif isinstance(it, dict):
            s = str(it.get("id", "")).strip()
        else:
            s = ""
        if s:
            out.add(s)
    return sorted(out)

def _to_rfc3339_utc(dt: datetime) -> str:
    """Convert datetime to RFC3339 string in UTC."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def freebusy_query(
    config: CustomAddonConfig,
    timeMin: Union[datetime, str],
    timeMax: Union[datetime, str],
    items: List[Union[str, Dict[str, Any]]],
    timeZone: Optional[str] = None,
    calendarExpansionMax: Optional[int] = None,
    groupExpansionMax: Optional[int] = None,
) -> ActionResponse:
    tokens = TokensSchema(stepAmount=2000, totalCurrentAmount=16236)
    logger.debug("[freebusy_query] called")

    missing = []
    if timeMin is None:
        missing.append("timeMin")
    if timeMax is None:
        missing.append("timeMax")
    if not items:
        missing.append("items")

    if missing:
        msg = f"Missing required parameters: {', '.join(missing)}."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    try:
        timeMin_dt = _coerce_dt(timeMin, "timeMin")
        timeMax_dt = _coerce_dt(timeMax, "timeMax")
    except Exception as e:
        msg = f"Invalid datetime format: {e}"
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    if timeMax_dt <= timeMin_dt:
        msg = "timeMax must be strictly after timeMin."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

    norm_items = _normalize_items(items)
    if not norm_items:
        msg = "items cannot be empty after normalization."
        logger.warning(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=400)

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

    url = "https://www.googleapis.com/calendar/v3/freeBusy"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    body: Dict[str, Any] = {
        "timeMin": _to_rfc3339_utc(timeMin_dt),
        "timeMax": _to_rfc3339_utc(timeMax_dt),
        "items": [{"id": cid} for cid in norm_items],
    }
    if timeZone:
        body["timeZone"] = timeZone
    if calendarExpansionMax:
        body["calendarExpansionMax"] = calendarExpansionMax
    if groupExpansionMax:
        body["groupExpansionMax"] = groupExpansionMax

    try:
        response = requests.post(url, headers=headers, json=body, timeout=config.config.get("request_timeout_s", 10))
        response.raise_for_status()
        data = response.json()
        return ActionResponse(
            output=ActionOutput(data=data),
            tokens=tokens,
            message="FreeBusy query successful",
            code=response.status_code,
        )
    except Exception as e:
        msg = f"FreeBusy query failed: {e}"
        logger.error(msg)
        return ActionResponse(output=ActionOutput(data={"error": msg}), tokens=tokens, message=msg, code=500)
