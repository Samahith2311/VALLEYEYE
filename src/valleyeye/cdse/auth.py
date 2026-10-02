from __future__ import annotations

import asyncio
import time

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from valleyeye.core.errors import ErrorCode, ValleyeyeError
from valleyeye.core.settings import Settings


class TokenPayload(BaseModel):
    access_token: SecretStr
    expires_in: int
    refresh_token: SecretStr | None = None
    refresh_expires_in: int | None = None


class CDSEAuth:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._token: TokenPayload | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < (
                self._expires_at - self._settings.token_refresh_skew_seconds
            ):
                return self._token.access_token.get_secret_value()
            payload: dict[str, str]
            if self._token and self._token.refresh_token:
                payload = {
                    "grant_type": "refresh_token",
                    "refresh_token": self._token.refresh_token.get_secret_value(),
                }
            else:
                payload = self._password_payload()
            token = await self._request_token(payload)
            self._token = token
            self._expires_at = time.monotonic() + token.expires_in
            return token.access_token.get_secret_value()

    def _password_payload(self) -> dict[str, str]:
        if not self._settings.cdse_username or not self._settings.cdse_password:
            raise ValleyeyeError(
                ErrorCode.CDSE_AUTH_FAILED,
                "CDSE credentials are required for authenticated product access",
                "DISCOVERY",
            )
        payload = {
            "grant_type": "password",
            "client_id": self._settings.cdse_client_id,
            "username": self._settings.cdse_username,
            "password": self._settings.cdse_password.get_secret_value(),
        }
        if self._settings.cdse_totp:
            payload["totp"] = self._settings.cdse_totp.get_secret_value()
        return payload

    async def _request_token(self, payload: dict[str, str]) -> TokenPayload:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.retry_max_attempts),
                wait=wait_random_exponential(multiplier=0.1, max=1),
                retry=retry_if_exception_type((httpx.TransportError, CDSETransientError)),
                reraise=True,
            ):
                with attempt:
                    response = await self._client.post(
                        self._settings.cdse_identity_url,
                        data={"client_id": self._settings.cdse_client_id, **payload},
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=self._settings.http_timeout_seconds,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise CDSETransientError(response.status_code)
                    if response.is_error:
                        raise ValleyeyeError(
                            ErrorCode.CDSE_AUTH_FAILED,
                            "CDSE rejected the authentication request",
                            "DISCOVERY",
                            details={"status_code": response.status_code},
                        )
                    try:
                        raw = response.json()
                        return TokenPayload.model_validate(raw)
                    except (ValueError, ValidationError) as exc:
                        raise ValleyeyeError(
                            ErrorCode.CDSE_AUTH_FAILED,
                            "CDSE returned an invalid token response",
                            "DISCOVERY",
                        ) from exc
        except httpx.TransportError as exc:
            raise ValleyeyeError(
                ErrorCode.CDSE_UNAVAILABLE,
                "CDSE authentication service could not be reached",
                "DISCOVERY",
                retryable=True,
            ) from exc
        raise AssertionError("retry loop exited without a result")


class CDSETransientError(ValleyeyeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(
            ErrorCode.CDSE_UNAVAILABLE,
            "CDSE authentication service is temporarily unavailable",
            "DISCOVERY",
            retryable=True,
            details={"status_code": status_code},
        )
