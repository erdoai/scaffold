"""Reverse proxy — forwards authenticated requests to the upstream service."""

from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import Response


async def proxy_request(request: Request, upstream_url: str) -> Response:
    """Forward a request to the upstream service and return the response."""
    # Build upstream URL
    path = request.url.path
    query = request.url.query
    url = f"{upstream_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"

    # Forward headers (strip hop-by-hop)
    skip_headers = {"host", "connection", "keep-alive", "transfer-encoding"}
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in skip_headers
    }

    body = await request.body()

    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            timeout=30,
            follow_redirects=False,
        )

    # Forward response headers (strip hop-by-hop)
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in skip_headers and k.lower() != "content-encoding"
    }

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
    )
