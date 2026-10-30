"""Local OpenAI-compatible proxy that routes to configured upstreams."""
from __future__ import annotations

import json
from typing import AsyncGenerator, Dict

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Config, ModelMapping, Upstream, load_config


app = FastAPI(title="transmymodels", docs_url=None, redoc_url=None, openapi_url=None)

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


def _check_auth(request: Request, cfg: Config) -> bool:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    return auth.split(" ", 1)[1].strip() == cfg.local.api_key


def _resolve(cfg: Config, routed_name: str):
    """Return (upstream, mapping) for a routed model name, or None."""
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
    """Forward the request to the upstream, swapping model + auth."""
    body["model"] = mapping.upstream_model
    url = upstream.base_url.rstrip("/") + path
    chosen_key = upstream.pick_key()
    headers = {
        "Authorization": f"Bearer {chosen_key}",
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }

    want_stream = bool(body.get("stream"))

    if not want_stream:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.post(url, json=body, headers=headers)
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
        )

    # Streaming: open a long-lived client and pump SSE bytes through.
    client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    req = client.build_request("POST", url, json=body, headers=headers)
    resp = await client.send(req, stream=True)

    async def gen() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        gen(),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "text/event-stream"),
    )


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
    return {"ok": True, "upstreams": len(cfg.upstreams)}


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
            {"error": {"message": f"unknown model '{routed}' — see GET /v1/models"}},
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
