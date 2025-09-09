from pydantic import Field, model_validator, ConfigDict
from .baseconfig import BaseAddonConfig
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Dict, Annotated

class CustomAddonConfig(BaseAddonConfig):
    model_config = ConfigDict(extra="allow")

    default_calendar_id: str = Field(default="primary",description="Default calendar ID used when none is provided.")
    default_max_results: Annotated[int, Field(ge=1, le=250, description="Max number of events returned")] = 10
    default_time_window_days: Annotated[int, Field(ge=0, description="Default forward time window in days from now (>= 0).")] = 7
    default_timezone: str = Field(default="Europe/Paris",description="Default timezone.")
    request_timeout_s: Annotated[int, Field(ge=1, le=60, description="HTTP request timeout).")] = 10
    enable_debug: bool = Field(default=False,description="Enable debug logs.")  

    @model_validator(mode="after")
    def validate_calendar_config(self) -> "CustomAddonConfig":
        secrets: Dict[str, Any] = getattr(self, "secrets", {}) or {}
        if "access_token" not in secrets:
            raise ValueError("`access_token` secret is required")
        
        try:
            ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Invalid IANA timezone: {self.default_timezone!r}")

        if self.default_time_window_days > 365 * 5:
            raise ValueError("default_time_window_days is too large (> 5 years).")

        return self
