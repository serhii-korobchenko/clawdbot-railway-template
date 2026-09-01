from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardSettings:
    api_base_url: str
    api_token: str
    password: str
    session_secret: str

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        api_base_url = os.environ.get("PROROK_API_BASE_URL", "").strip().rstrip("/")
        api_token = os.environ.get("PROROK_API_TOKEN", "").strip()
        password = os.environ.get("PROROK_DASHBOARD_PASSWORD", "").strip()
        session_secret = os.environ.get(
            "PROROK_DASHBOARD_SESSION_SECRET", ""
        ).strip()

        missing = []
        if not api_base_url:
            missing.append("PROROK_API_BASE_URL")
        if not api_token:
            missing.append("PROROK_API_TOKEN")
        if not password:
            missing.append("PROROK_DASHBOARD_PASSWORD")
        if not session_secret:
            missing.append("PROROK_DASHBOARD_SESSION_SECRET")

        if missing:
            raise RuntimeError(
                "Missing required dashboard configuration: "
                + ", ".join(missing)
            )

        return cls(
            api_base_url=api_base_url,
            api_token=api_token,
            password=password,
            session_secret=session_secret,
        )
