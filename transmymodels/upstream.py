"""Talk to an upstream OpenAI-compatible API to list its models."""
from __future__ import annotations

from typing import List

import httpx


class UpstreamError(Exception):
    pass


def fetch_upstream_models(base_url: str, api_key: str, timeout: float = 15.0) -> List[str]:
    """GET {base_url}/models and return the list of model ids.

    Raises UpstreamError on any non-2xx or on a malformed payload.
    """
    url = base_url.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise UpstreamError(f"network error: {e}") from e

    if r.status_code == 401:
        raise UpstreamError("401 Unauthorized — check the API key.")
    if r.status_code == 404:
        raise UpstreamError(f"404 Not Found — does {base_url}/models exist?")
    if r.status_code >= 400:
        raise UpstreamError(f"HTTP {r.status_code}: {r.text[:300]}")

    try:
        data = r.json()
    except ValueError as e:
        raise UpstreamError(f"upstream returned non-JSON: {e}") from e

    # OpenAI shape: {"object": "list", "data": [{"id": "gpt-4o", ...}, ...]}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise UpstreamError("unexpected /models payload (no 'data' list).")

    ids: List[str] = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            ids.append(str(it["id"]))
    if not ids:
        raise UpstreamError("upstream returned an empty model list.")
    return ids
