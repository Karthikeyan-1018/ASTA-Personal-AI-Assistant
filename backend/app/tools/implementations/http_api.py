"""Allowlisted HTTP integration tool.

Arbitrary LLM-generated URLs are never accepted. The request is limited to an
explicit host allowlist, must use HTTPS unless HTTP is separately enabled,
redirects are not followed, private/loopback/link-local addresses are rejected
after DNS resolution, and credentials are attached server-side per host. The
tool is sensitive, so it always requires explicit confirmation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from app.core.config import settings
from app.tools.base import BaseTool, ToolContext
from app.tools.enums import ToolCategory
from app.tools.exceptions import (
    ToolConfigurationError,
    ToolProviderError,
    ToolSizeLimitError,
    ToolValidationError,
)
from app.tools.result import success_result
from app.tools.schemas import StrictModel, ToolResult


logger = logging.getLogger(__name__)

ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE"}
MAX_TIMEOUT_SECONDS = 60


class HttpRequestInput(StrictModel):
    method: str = Field(
        description="HTTP method: GET, POST, PUT, or DELETE.",
    )
    url: str = Field(
        max_length=2048,
        description=(
            "Absolute URL whose host is on the server-side allowlist."
        ),
    )
    query: dict[str, str] | None = Field(
        default=None,
        description="Optional query parameters.",
    )
    json_body: Any = Field(
        default=None,
        description="Optional JSON request body.",
    )


class HttpRequestTool(BaseTool):
    name: ClassVar[str] = "http_request"
    description: ClassVar[str] = (
        "Call an allowlisted external HTTP API with a server-side credential. "
        "Only pre-approved hosts are reachable. Requires user confirmation."
    )
    category: ClassVar[ToolCategory] = ToolCategory.SENSITIVE
    requires_confirmation: ClassVar[bool] = True
    allowed_in_private: ClassVar[bool] = False
    provider_label: ClassVar[str | None] = "http_api"
    input_model: ClassVar[type[StrictModel]] = HttpRequestInput

    @property
    def enabled(self) -> bool:
        return bool(
            settings.TOOL_HTTP_ENABLED and settings.tool_http_allowed_hosts
        )

    async def execute(
        self,
        context: ToolContext,
        arguments: HttpRequestInput,
    ) -> ToolResult:
        method = (arguments.method or "").strip().upper()

        if method not in ALLOWED_METHODS:
            raise ToolValidationError(
                "Only GET, POST, PUT, and DELETE are allowed."
            )

        host = await _validate_target(arguments.url, method=method)

        credentials = settings.tool_http_credentials.get(host, {})
        timeout = max(
            1,
            min(
                int(settings.TOOL_HTTP_TIMEOUT_SECONDS),
                MAX_TIMEOUT_SECONDS,
            ),
        )
        max_bytes = max(1, int(settings.TOOL_HTTP_MAX_RESPONSE_BYTES))

        headers = dict(credentials)
        headers.setdefault("Accept", "application/json, text/plain;q=0.9")
        content: bytes | None = None

        if method != "GET" and arguments.json_body is not None:
            try:
                content = json.dumps(
                    arguments.json_body,
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")

            except (TypeError, ValueError) as error:
                raise ToolValidationError(
                    "The JSON request body is invalid."
                ) from error

            if len(content) > max_bytes:
                raise ToolSizeLimitError(
                    "The request body exceeds the configured limit."
                )

            headers.setdefault("Content-Type", "application/json")

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    arguments.url,
                    params=arguments.query,
                    content=content,
                    headers=headers,
                ) as response:
                    body, truncated = await _read_bounded(
                        response,
                        max_bytes,
                    )
                    status_code = response.status_code
                    content_type = response.headers.get(
                        "content-type",
                        "",
                    )

        except httpx.TimeoutException as error:
            raise ToolProviderError(
                "The external API timed out."
            ) from error

        except httpx.HTTPError as error:
            raise ToolProviderError(
                "The external API is unavailable."
            ) from error

        if 300 <= status_code < 400:
            raise ToolProviderError(
                "The external API returned a redirect, which is not followed."
            )

        parsed_body: Any
        text = body.decode("utf-8", errors="replace")

        if "application/json" in content_type.lower():
            try:
                parsed_body = json.loads(text) if text.strip() else None

            except ValueError:
                parsed_body = text

        else:
            parsed_body = text

        return success_result(
            tool_name=self.name,
            data={
                "status_code": status_code,
                "content_type": content_type.split(";")[0].strip() or None,
                "body": parsed_body,
                "truncated": truncated,
                "untrusted": True,
                "host": host,
            },
            provider=host,
            metadata={"untrusted_content": True},
        )

    def summarize(self, arguments: HttpRequestInput) -> str:
        return f"{arguments.method.upper()} {arguments.url}"


async def _validate_target(url: str, *, method: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ToolValidationError("A URL is required.")

    if len(url) > 2048:
        raise ToolValidationError("The URL is too long.")

    try:
        parsed = urlsplit(url.strip())

    except ValueError as error:
        raise ToolValidationError("The URL is invalid.") from error

    scheme = parsed.scheme.lower()

    if scheme not in {"https", "http"}:
        raise ToolValidationError("Only HTTP and HTTPS URLs are allowed.")

    if scheme == "http" and not settings.TOOL_HTTP_ALLOW_HTTP:
        raise ToolValidationError(
            "Plain HTTP is disabled. Use HTTPS or enable "
            "TOOL_HTTP_ALLOW_HTTP explicitly."
        )

    if parsed.username or parsed.password:
        raise ToolValidationError(
            "Credentials must not be embedded in a URL."
        )

    host = (parsed.hostname or "").lower().rstrip(".")

    if not host:
        raise ToolValidationError("The URL must include a host name.")

    allowed = settings.tool_http_allowed_hosts

    if host not in allowed:
        raise ToolValidationError(
            "That host is not on the configured allowlist."
        )

    # Resolve the host and reject internal/link-local destinations. This is
    # checked on every call so a DNS change cannot turn an allowed name into
    # an internal address.
    try:
        port = parsed.port

    except ValueError as error:
        raise ToolValidationError("The URL has an invalid port.") from error

    addresses = await _resolve_host(
        host,
        port if port is not None else (443 if scheme == "https" else 80),
    )

    if not addresses:
        raise ToolProviderError("The external host could not be resolved.")

    for address in addresses:
        if _is_forbidden_address(address):
            raise ToolValidationError(
                "That host resolves to a non-public address."
            )

    return host


async def _resolve_host(host: str, port: int) -> list[ipaddress._BaseAddress]:
    loop = asyncio.get_running_loop()

    try:
        infos = await loop.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )

    except (socket.gaierror, UnicodeError, OSError):
        return []

    addresses: list[ipaddress._BaseAddress] = []

    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])

        except ValueError:
            continue

        if address not in addresses:
            addresses.append(address)

    return addresses


def _is_forbidden_address(address: ipaddress._BaseAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped

    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def _read_bounded(
    response: httpx.Response,
    limit: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False

    async for chunk in response.aiter_bytes():
        if total >= limit:
            truncated = True
            break

        remaining = limit - total

        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            total += remaining
            truncated = True
            break

        chunks.append(chunk)
        total += len(chunk)

    return b"".join(chunks), truncated


__all__ = ["HttpRequestInput", "HttpRequestTool"]
