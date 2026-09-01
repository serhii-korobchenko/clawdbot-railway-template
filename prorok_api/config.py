from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiSettings:
    db_path: str
    api_token: str
    host: str = "0.0.0.0"
    port: int = 18880

    @classmethod
    def from_env(cls) -> "ApiSettings":
        token = os.environ.get("PROROK_API_TOKEN", "").strip()
        if not token:
            raise RuntimeError("PROROK_API_TOKEN must be configured")

        db_path = os.environ.get(
            "PROROK_DB_PATH",
            "/data/workspace/prorok/prorok.sqlite3",
        ).strip()
        if not db_path:
            raise RuntimeError("PROROK_DB_PATH must not be empty")

        host = os.environ.get("PROROK_API_HOST", "0.0.0.0").strip() or "0.0.0.0"

        try:
            port = int(os.environ.get("PROROK_API_PORT", "18880"))
        except ValueError as exc:
            raise RuntimeError("PROROK_API_PORT must be an integer") from exc

        if not (1 <= port <= 65535):
            raise RuntimeError("PROROK_API_PORT must be between 1 and 65535")

        return cls(
            db_path=db_path,
            api_token=token,
            host=host,
            port=port,
        )
