from __future__ import annotations

import base64
import binascii
import hmac
import os
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


@dataclass(frozen=True)
class ServerAccessSettings:
    allowed_hosts: tuple[str, ...]
    password: str = field(repr=False)


def load_server_access_settings(
    environ: Mapping[str, str] | None = None,
) -> ServerAccessSettings:
    env = os.environ if environ is None else environ
    hosts = tuple(
        dict.fromkeys(host.strip().lower() for host in env.get("ALLOWED_HOSTS", "").split(",") if host.strip())
    )
    if not hosts or any(not HOST_PATTERN.fullmatch(host) for host in hosts):
        raise ValueError("서버 모드에서는 정확한 도메인을 ALLOWED_HOSTS에 지정해야 합니다.")
    password = env.get("BIGKINDS_ACCESS_PASSWORD", "")
    if not password.strip():
        raise ValueError("서버 모드에서는 BIGKINDS_ACCESS_PASSWORD를 지정해야 합니다.")
    return ServerAccessSettings(allowed_hosts=hosts, password=password)


class ServerAccessMiddleware:
    """Protect the entire server UI and API, except the exact health endpoint."""

    def __init__(self, app: ASGIApp, password: str):
        self.app = app
        self.password = password.encode("utf-8")

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

    def _authorized(self, authorization: str) -> bool:
        scheme, separator, encoded = authorization.partition(" ")
        if not separator or scheme.lower() != "basic" or len(encoded) > 8192:
            return False
        try:
            user, colon, password = base64.b64decode(encoded, validate=True).partition(b":")
        except (ValueError, binascii.Error):
            return False
        return bool(
            colon
            and hmac.compare_digest(user, b"bigkinds")
            and hmac.compare_digest(password, self.password)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if request.method == "GET" and request.url.path == "/api/health":
            await self.app(scope, receive, send)
            return
        if not self._authorized(request.headers.get("authorization", "")):
            response = JSONResponse(
                {"detail": "인증이 필요합니다."},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="BigKinds"'},
            )
        elif request.method not in {"GET", "HEAD", "OPTIONS"} and not self._valid_origin(request):
            response = JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)
