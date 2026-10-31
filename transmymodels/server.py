"""Local OpenAI-compatible proxy that routes to configured upstreams.

Key lifecycle:
  - Each request picks an enabled upstream key (weighted random).
  - If the upstream answers 401/403 for a key, that key is marked disabled and
    the request is retried on another enabled key (immediate failover).
  - A background task re-enables all disabled keys every hour so they get
    retried automatically.
"""
from __future__ import annotations

import asyncio
import random
import time
from threading import Lock
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import ApiKey, Config, ModelMapping, Upstream, load_config


app = FastAPI(title="transmymodels", docs_url=None, redoc_url=None, openapi_url=None)

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)

# Status codes that mean "this key is bad" (not upstream outage, not rate limit).
KEY_BAD_STATUS = {401, 403}

# How long to wait before re-enabling disabled keys automatically.
RETRY_INTERVAL_SEC = 3600


# ---------- in-memory key circuit state ----------

_state_lock = Lock()
# api_key_string -> {"enabled": bool, "disabled_at": float}
_key_state: Dict[str, dict] = {}


def _is_enabled(key: str) -> bool:
    with _state_lock:
        return _key_state.get(key, {}).get("enabled", True)


def _disable_key(key: str) -> None:
    with _state_lock:
        _key_state[key] = {"enabled": False, "disabled_at": time.time()}


def _reenable_all() -> int:
    with _state_lock:
        n = 0
        for k, st in _key_state.items():
            if not st["enabled"]:
                st["enabled"] = True
                n += 1
        return n


def _disabled_count() -> int:
    with _state_lock:
        return sum(1 for st in _key_state.values() if not st["enabled"])


def _choose_keys(upstream: Upstream) -> List[ApiKey]:
    """Return enabled keys; if all are disabled, return all (probe the oldest)."""
    enabled = [k for k in upstream.api_keys if _is_enabled(k.key)]
    return enabled or list(upstream.api_keys)


# ---------- auth + routing ----------

def _check_auth(request: Request, cfg: Config) -> bool:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    return auth.split(" ", 1)[1].strip() == cfg.local.api_key


def _resolve(cfg: Config, routed_name: str) -> Optional[Tuple[Upstream, ModelMapping]]:
    for u in cfg.upstreams:
        for m in u.models:
            if m.routed_name == routed_name:
                return u, m
    return None


async def _proxy_upstream(
    cfg: Config,
    upstream: Upstream,
    mapping: ModelMapping,
    path: str,
    body: dict,
    request: Request,
) -> Response:
    """Forward with automatic key failover on 401/403.

    Non-streaming: short-lived httpx client per attempt.
    Streaming: the httpx client and upstream response are owned by the
    StreamingResponse generator and closed when the stream ends or the client
    disconnects. ReadError / RemoteProtocolError / CancelledError are normal
    (client went away or upstream dropped mid-stream) and are swallowed.
    """
    candidates = _choose_keys(upstream)
    keys = [k.key for k in candidates]
    weights = [max(1, k.weight) for k in candidates]
    want_stream = bool(body.get("stream"))

    body["model"] = mapping.upstream_model
    url = upstream.base_url.rstrip("/") + path
    accept = request.headers.get("accept", "application/json")

    last_response: Optional[Response] = None
    tried = set()

    for _ in range(len(candidates)):
        pool = [k for k in keys if k not in tried]
        if not pool:
            break
        pool_weights = [weights[keys.index(k)] for k in pool]
        chosen = random.choices(pool, weights=pool_weights, k=1)[0]
        tried.add(chosen)

        headers = {
            "Authorization": f"Bearer {chosen}",
            "Content-Type": "application/json",
            "Accept": accept,
        }

        if not want_stream:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                r = await client.post(url, json=body, headers=headers)
            if r.status_code in KEY_BAD_STATUS:
                _disable_key(chosen)
                continue
            return Response(
                content=r.content,
                status_code=r.status_code,
                media_type=r.headers.get("content-type", "application/json"),
            )

        # Streaming: open a client that lives inside the generator.
        client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        try:
            req = client.build_request("POST", url, json=body, headers=headers)
            resp = await client.send(req, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            await client.aclose()
            continue

        if resp.status_code in KEY_BAD_STATUS:
            _disable_key(chosen)
            await resp.aclose()
            await client.aclose()
            continue

        media_type = resp.headers.get("content-type", "text/event-stream")
        status = resp.status_code

        async def gen(resp=resp, client=client) -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except (httpx.ReadError, httpx.RemoteProtocolError,
                    httpx.ReadTimeout, asyncio.CancelledError):
                pass  # client disconnected or upstream dropped; normal
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(gen(), status_code=status, media_type=media_type)

    return last_response or JSONResponse(
        {"error": {"message": "all upstream keys failed"}}, status_code=502
    )


# ---------- hourly retry task ----------

@app.on_event("startup")
async def _startup_retry_loop():
    async def loop():
        while True:
            await asyncio.sleep(RETRY_INTERVAL_SEC)
            n = _reenable_all()
            if n:
                print(f"[transmymodels] retry: re-enabled {n} disabled key(s)", flush=True)

    asyncio.create_task(loop())


# ---------- routes ----------

@app.get("/v1/models")
async def list_models(request: Request):
    cfg = load_config()
    if not _check_auth(request, cfg):
        return JSONResponse({"error": {"message": "invalid local api key"}}, status_code=401)

    data = []
    for u in cfg.upstreams:
        for m in u.models:
            data.append(
                {
                    "id": m.routed_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": u.name,
                    "_upstream": u.name,
                    "_upstream_model": m.upstream_model,
                }
            )
    return {"object": "list", "data": data}


@app.get("/health")
async def health(request: Request):
    cfg = load_config()
    return {
        "ok": True,
        "upstreams": len(cfg.upstreams),
        "disabled_keys": _disabled_count(),
    }


async def _handle_chat(request: Request, path: str):
    cfg = load_config()
    if not _check_auth(request, cfg):
        return JSONResponse({"error": {"message": "invalid local api key"}}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "request body must be JSON"}}, status_code=400)

    routed = body.get("model")
    if not isinstance(routed, str) or not routed:
        return JSONResponse({"error": {"message": "missing 'model' field"}}, status_code=400)

    resolved = _resolve(cfg, routed)
    if resolved is None:
        return JSONResponse(
            {"error": {"message": f"unknown model '{routed}' - see GET /v1/models"}},
            status_code=404,
        )

    upstream, mapping = resolved
    return await _proxy_upstream(cfg, upstream, mapping, path, body, request)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _handle_chat(request, "/chat/completions")


@app.post("/v1/completions")
async def completions(request: Request):
    return await _handle_chat(request, "/completions")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    return await _handle_chat(request, "/embeddings")


@app.post("/v1/responses")
async def responses_api(request: Request):
    return await _handle_chat(request, "/responses")


def serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
