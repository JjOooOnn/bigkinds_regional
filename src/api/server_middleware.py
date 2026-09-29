from __future__ import annotations

import os
import re
from typing import Mapping
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


def load_allowed_hosts(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if environ is None else environ
    hosts = tuple(
        dict.fromkeys(host.strip().lower() for host in env.get("ALLOWED_HOSTS", "").split(",") if host.strip())
    )
    if not hosts or any(not HOST_PATTERN.fullmatch(host) for host in hosts):
        raise ValueError("서버 모드에서는 정확한 도메인을 ALLOWED_HOSTS에 지정해야 합니다.")
    return hosts


class ServerRequestOriginMiddleware:
    """Reject cross-site writes while allowing public access to the server UI and API."""

    def __init__(self, app: ASGIApp):
        self.app = app

    @staticmethod
    def _valid_origin(request: Request) -> bool:
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        origin = request.headers.get("origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        host = request.headers.get("host", "").lower()
        scheme = "http" if host.split(":", 1)[0] in {"localhost", "127.0.0.1"} else "https"
        return bool(
            parsed.scheme == scheme
            and parsed.netloc.lower() == host
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not self._valid_origin(request):
            response = JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
