from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from valleyeye.cdse.auth import CDSEAuth
from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "CDSE_USERNAME": "user@example.test",
        "CDSE_PASSWORD": SecretStr("not-a-real-password"),
    }
    values.update(updates)
    return Settings(**values)


def test_auth_success_and_cached_access_token() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/token")
        assert "client_id=cdse-public" in request.content.decode()
        return httpx.Response(
            200,
            json={"access_token": "access-one", "expires_in": 3600, "refresh_token": "refresh-one"},
        )

    async def run() -> tuple[str, str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            auth = CDSEAuth(_settings(), client)
            return await auth.access_token(), await auth.access_token()

    first, second = asyncio.run(run())

    assert first == second == "access-one"
    assert calls == 1


def test_expired_access_token_uses_refresh_grant() -> None:
    grants: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = request.content.decode()
        grants.append("refresh_token" if "grant_type=refresh_token" in form else "password")
        if len(grants) == 1:
            return httpx.Response(
                200,
                json={"access_token": "first", "expires_in": 60, "refresh_token": "refresh"},
            )
        return httpx.Response(
            200,
            json={"access_token": "second", "expires_in": 3600, "refresh_token": "refresh2"},
        )

    async def run() -> tuple[str, str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            auth = CDSEAuth(_settings(token_refresh_skew_seconds=60), client)
            first = await auth.access_token()
            second = await auth.access_token()
            return first, second

    assert asyncio.run(run()) == ("first", "second")
    assert grants == ["password", "refresh_token"]


def test_auth_retries_transient_503() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"access_token": "ok", "expires_in": 3600})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await CDSEAuth(_settings(retry_max_attempts=2), client).access_token()

    assert asyncio.run(run()) == "ok"
    assert calls == 2


def test_auth_does_not_retry_401() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid_grant"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await CDSEAuth(_settings(retry_max_attempts=3), client).access_token()

    with pytest.raises(ValleyeyeError) as caught:
        asyncio.run(run())

    assert caught.value.code == ErrorCode.CDSE_AUTH_FAILED
    assert calls == 1
