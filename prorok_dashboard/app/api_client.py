from __future__ import annotations

from typing import Any

import httpx

from .config import DashboardSettings


class UpstreamError(RuntimeError):
    pass


class UpstreamUnavailable(UpstreamError):
    pass


class UpstreamNotFound(UpstreamError):
    pass


class ProrokApiClient:
    def __init__(self, settings: DashboardSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._settings.api_base_url,
            headers={
                "Authorization": f"Bearer {self._settings.api_token}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(5.0),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PROROK API client has not been started")
        return self._client

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._require_client()

        try:
            response = await client.get(path, params=params)
        except httpx.RequestError as exc:
            raise UpstreamUnavailable(
                "PROROK data service is unavailable"
            ) from exc

        if response.status_code == 404:
            raise UpstreamNotFound("Event not found")

        if response.status_code >= 500:
            raise UpstreamUnavailable(
                "PROROK data service is unavailable"
            )

        if response.status_code >= 400:
            raise UpstreamError(
                f"PROROK data service returned HTTP {response.status_code}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "PROROK data service returned invalid JSON"
            ) from exc

    async def health(self) -> bool:
        client = self._require_client()
        try:
            response = await client.get("/healthz")
        except httpx.RequestError:
            return False
        return response.status_code == 200

    async def list_events(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        return await self._get("/api/v1/events", params=params)

    async def list_evidence(
        self,
        *,
        event_id: str | None = None,
        direction: str | None = None,
        strength: str | None = None,
        source: str | None = None,
        q: str | None = None,
        sort: str = "newest",
        prorok_app: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if event_id:
            params["event_id"] = event_id
        if direction:
            params["direction"] = direction
        if strength:
            params["strength"] = strength
        if source:
            params["source"] = source
        if q:
            params["q"] = q
        if prorok_app:
            params["prorok_app"] = prorok_app
        params["sort"] = sort
        return await self._get("/api/v1/evidence", params=params)

    async def get_evidence_activity(self, *, status: str = "active", window: str = "7d") -> dict[str, Any]:
        return await self._get(
            "/api/v1/evidence/activity",
            params={"status": status, "window": window},
        )

    async def list_candidate_evidence(
        self,
        *,
        event_id: str | None = None,
        direction: str | None = None,
        strength: str | None = None,
        validation_state: str | None = None,
        source: str | None = None,
        q: str | None = None,
        sort: str = "newest",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if event_id:
            params["event_id"] = event_id
        if direction:
            params["direction"] = direction
        if strength:
            params["strength"] = strength
        if validation_state:
            params["validation_state"] = validation_state
        if source:
            params["source"] = source
        if q:
            params["q"] = q
        params["sort"] = sort
        return await self._get("/api/v1/evidence/candidates", params=params)

    async def get_candidate_activity(self, *, event_id: str | None = None, window: str = "7d", metric: str = "accepted") -> dict[str, Any]:
        params: dict[str, Any] = {"window": window, "metric": metric}
        if event_id:
            params["event_id"] = event_id
        return await self._get("/api/v1/evidence/candidates/activity", params=params)

    async def get_latest_refresh(self) -> dict[str, Any]:
        return await self._get("/api/v1/refresh/latest")

    async def get_event(self, event_id: str) -> dict[str, Any]:
        return await self._get(f"/api/v1/events/{event_id}")
