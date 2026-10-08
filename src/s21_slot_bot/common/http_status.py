from http import HTTPStatus

from aiohttp import ClientResponse


def get_http_status(response: ClientResponse) -> HTTPStatus | int:
    try:
        return HTTPStatus(response.status)
    except ValueError:
        return response.status


def get_http_status_description(status: HTTPStatus | int) -> str:
    match status:
        case HTTPStatus():
            return status.phrase
        case int() if 500 <= status <= 599:
            return "ошибка сервера"
        case int():
            return "неизвестная ошибка"
