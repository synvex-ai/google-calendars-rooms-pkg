from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from .baseconfig import BaseAddonConfig, RequiredSecretsBase


class CustomRequiredSecrets(RequiredSecretsBase):
    google_calendars_api_key: str = Field(..., description="Google Calendar API key environment variable name (key name expected in `secrets`).")


class CustomAddonConfig(BaseAddonConfig):
    model_config = ConfigDict(extra="allow")

    default_calendar_id: str = Field(default="primary", description="Default calendar ID used when none is provided.")
    default_max_results: Annotated[int, Field(ge=1, le=250, description="Max number of events returned.")] = 10
    default_time_window_days: Annotated[int, Field(ge=0, description="Default forward time window in days from now (>= 0).")] = 7
    default_timezone: str = Field(default="Europe/Paris", description="Default timezone.")
    request_timeout_s: Annotated[int, Field(ge=1, le=60, description="HTTP request timeout (seconds).")] = 10
    enable_debug: bool = Field(default=False, description="Enable debug logs.")

    @classmethod
    def get_required_secrets(cls) -> CustomRequiredSecrets:
        return CustomRequiredSecrets(google_calendars_api_key="google_calendars_api_key")

    @model_validator(mode="after")
    def validate_google_calendar_secrets(self):
        required = self.get_required_secrets()
        required_secret_keys = [required.google_calendars_api_key]

        missing = [k for k in required_secret_keys if not self.secrets.get(k)]
        if missing:
            raise ValueError("Missing Google Calendar secrets: "f"{missing}. Put your OAuth access token under these keys in `secrets`.")
        return self
