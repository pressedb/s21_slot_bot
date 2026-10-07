import asyncio
import html
import re
import time
import uuid
from http import HTTPStatus
from typing import Any, override
from urllib.parse import parse_qs, quote, urljoin, urlparse

import aiohttp
from aiohttp import ClientHandlerType, ClientRequest, ClientResponse

from s21_slot_bot.client.config import S21ClientConfig
from s21_slot_bot.client.consts import (
    AUTH_URL,
    CLIENT_ID,
    DEFAULT_TOKEN_EXPIRATION_SEC,
    PLATFORM_URL,
)
from s21_slot_bot.client.errors import (
    School21LoginError,
)
from s21_slot_bot.client.middleware.base import School21Middleware
from s21_slot_bot.client.models import (
    ContentType,
    GrantType,
    Tokens,
)
from s21_slot_bot.common.http_status import get_http_status


class School21AuthMiddleware(School21Middleware):
    def __init__(self, config: S21ClientConfig):
        self._username = config.username
        self._password = config.password
        self._tokens: Tokens | None = None
        self._auth_lock = asyncio.Lock()

    @property
    def _token_endpoint(self) -> str:
        return f"{AUTH_URL}/token"

    @property
    def _auth_endpoint(self) -> str:
        state = str(uuid.uuid4())
        nonce = str(uuid.uuid4())

        return (
            f"{AUTH_URL}/auth"
            f"?client_id={CLIENT_ID}"
            f"&redirect_uri={quote(PLATFORM_URL, safe='')}"
            f"&state={state}"
            f"&response_mode=fragment"
            f"&response_type=code"
            f"&scope=openid"
            f"&nonce={nonce}"
        )

    @property
    def _tokens_valid(self) -> bool:
        return self._tokens is not None and time.time() < self._tokens.expires_at_epoch

    @override
    async def __call__(self, request: ClientRequest, handler: ClientHandlerType) -> ClientResponse:
        await self._ensure_authenticated(request.session)
        self._apply_auth(request)
        response = await handler(request)
        if response.status not in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            return response

        response.release()
        await self._force_reauthenticate(request.session)
        self._apply_auth(request)
        return await handler(request)

    def _apply_auth(self, request: ClientRequest) -> None:
        if not self._tokens:
            raise School21LoginError("токен авторизации отсутствует")

        cookies = request.session.cookie_jar.filter_cookies(request.url)
        cookies["tokenId"] = self._tokens.access_token
        request.headers["Cookie"] = cookies.output(header="", sep=";").strip()

    async def _ensure_authenticated(self, session: aiohttp.ClientSession) -> None:
        async with self._auth_lock:
            if self._tokens_valid or await self._try_refresh(session):
                return
            await self._login(session)

    async def _force_reauthenticate(self, session: aiohttp.ClientSession) -> None:
        async with self._auth_lock:
            await self._login(session)

    async def _login(self, session: aiohttp.ClientSession) -> None:
        async with session.get(self._auth_endpoint, middlewares=()) as auth_resp:
            auth_text = await auth_resp.text()
            if not auth_resp.ok:
                raise School21LoginError(f"ошибка авторизации: `{auth_resp.reason}`", status=get_http_status(auth_resp))

        action_url = self._extract_login_action(auth_text, AUTH_URL)
        async with session.post(
            action_url,
            data={
                "username": self._username,
                "password": self._password.get_secret_value(),
            },
            allow_redirects=True,
            middlewares=(),
        ) as action_resp:
            code = self._extract_code_from_redirect_history([*action_resp.history, action_resp])
            if not code:
                raise School21LoginError("не удалось извлечь код авторизации", status=get_http_status(action_resp))

        async with session.post(
            self._token_endpoint,
            data={
                "code": code,
                "grant_type": GrantType.AUTHORIZATION_CODE,
                "client_id": CLIENT_ID,
                "redirect_uri": PLATFORM_URL,
            },
            headers={
                "Content-Type": ContentType.APPLICATION_FORM_URL_ENCODED,
            },
            middlewares=(),
        ) as token_resp:
            if not token_resp.ok:
                body = await token_resp.text()
                raise School21LoginError(
                    f"ошибка при запросе токена: `{token_resp.reason}`",
                    status=get_http_status(token_resp),
                    location={"body": body},
                )
            payload = await token_resp.json()

        self._set_tokens(payload)

    async def _try_refresh(self, session: aiohttp.ClientSession) -> bool:
        if not self._tokens or not self._tokens.refresh_token:
            return False

        async with session.post(
            self._token_endpoint,
            data={
                "grant_type": GrantType.REFRESH_TOKEN,
                "client_id": CLIENT_ID,
                "refresh_token": self._tokens.refresh_token,
            },
            headers={
                "Content-Type": ContentType.APPLICATION_FORM_URL_ENCODED,
            },
            middlewares=(),
        ) as resp:
            if not resp.ok:
                return False
            payload = await resp.json()

        self._set_tokens(payload)
        return True

    def _set_tokens(self, payload: dict[str, Any]) -> None:
        access = payload.get("access_token")
        refresh = payload.get("refresh_token", self._tokens.refresh_token if self._tokens else None)
        if not access or not refresh:
            raise School21LoginError("не удалось извлечь токены из ответа")
        expires_in = float(payload.get("expires_in", DEFAULT_TOKEN_EXPIRATION_SEC))
        now = time.time()
        self._tokens = Tokens(
            access_token=access,
            refresh_token=refresh,
            expires_at_epoch=now + expires_in,
        )

    def _extract_login_action(self, html_text: str, base_url: str) -> str:
        match = re.search(
            r'action\s*=\s*"([^"]*login-actions/authenticate[^"]*)"',
            html_text,
            flags=re.IGNORECASE,
        )

        if not match:
            raise School21LoginError("не удалось извлечь информацию для авторизации")

        joined = urljoin(base_url, html.unescape(match.group(1)))
        return joined

    def _extract_code_from_redirect_history(self, history: list[aiohttp.ClientResponse]) -> str | None:
        for resp in reversed(history):
            location = resp.headers.get("Location")
            if not location or "code=" not in location:
                continue

            parsed = urlparse(location)
            if not parsed.fragment:
                continue

            query = parse_qs(parsed.fragment)
            code = (query.get("code") or [""])[0]
            if code:
                return code

        return None
