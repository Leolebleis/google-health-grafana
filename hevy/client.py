"""Minimal client for the official Hevy API (requires a Hevy Pro API key)."""

import json
import logging
import urllib.request
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger(__name__)

API_BASE = "https://api.hevyapp.com/v1"
_MAX_WORKOUT_PAGE_SIZE = 10  # API maximum for /workouts
_MAX_TEMPLATE_PAGE_SIZE = 100  # API maximum for /exercise_templates


class HevyClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urlencode(params)
        req = urllib.request.Request(url, headers={"api-key": self._api_key})  # noqa: S310
        with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310
            return json.loads(res.read())

    def workout_count(self) -> int:
        return int(self._get("/workouts/count")["workout_count"])

    def workouts(self, page: int) -> tuple[list[dict[str, Any]], int]:
        """Return (workouts, page_count) for one page, newest first."""
        data = self._get("/workouts", {"page": page, "pageSize": _MAX_WORKOUT_PAGE_SIZE})
        return data.get("workouts", []), int(data.get("page_count", 0))

    def exercise_templates(self) -> list[dict[str, Any]]:
        """Return all exercise templates (paginated fetch)."""
        templates: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get("/exercise_templates", {"page": page, "pageSize": _MAX_TEMPLATE_PAGE_SIZE})
            templates.extend(data.get("exercise_templates", []))
            if page >= int(data.get("page_count", 0)):
                return templates
            page += 1
