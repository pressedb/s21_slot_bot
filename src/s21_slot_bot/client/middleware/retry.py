import asyncio
from http import HTTPStatus
from typing import override

import aiohttp
from aiohttp import ClientHandlerType, ClientRequest, ClientResponse

from s21_slot_bot.app.errors import InternalError
from s21_slot_bot.client.config import S21ClientConfig
from s21_slot_bot.client.errors import School21Error, School21LoginError
from s21_slot_bot.client.middleware.base import School21Middleware
from s21_slot_bot.common.error import get_error_description
from s21_slot_bot.common.logger import LogEntity, get_id_logger


class School21RetryMiddleware(School21Middleware):
    def __init__(self, config: S21ClientConfig):
        self._attempts = config.max_request_retries
        self._delay_sec = config.retry_delay_sec
        self._backoff = config.retry_backoff

    @override
    async def __call__(
        self,
        request: ClientRequest,
        handler: ClientHandlerType,
    ) -> ClientResponse:
        logger = get_id_logger(LogEntity.MIDDLEWARE)
        delay = self._delay_sec
        resp_status: HTTPStatus | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                response = await handler(request)
                resp_status = HTTPStatus(response.status)
                if resp_status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    response.raise_for_status()
                _ = await response.json()
                return response
            except (TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientResponseError, School21LoginError) as e:
                error_description = get_error_description(e)
                logger.warning(
                    "Request attempt %d/%d failed with %s",
                    attempt,
                    self._attempts,
                    error_description,
                )
                if attempt == self._attempts:
                    logger.error("Request failed after %d attempts", self._attempts)
                    raise School21Error(
                        f"ошибка запроса к Школе 21: {error_description}",
                        status=resp_status,
                        location={
                            "url": request.url.human_repr(),
                        },
                    ) from e
                logger.info("Retrying request in %.1f seconds", delay)
                await asyncio.sleep(delay)
                delay *= self._backoff
        raise InternalError("недостижимое состояние retry middleware")
