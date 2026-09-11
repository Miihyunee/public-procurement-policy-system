"""
tests.test_collector_transport

HTTP 전송 계층(:mod:`procurement.collectors.transport`) 검증.

.. warning::
    **실제 외부 API 서버에 접속하지 않습니다.** ``urllib.request.urlopen`` 을
    대역으로 바꿔 오류 상황만 재현합니다.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from procurement.collectors.errors import (
    ApiNetworkError,
    ApiRequestError,
    ApiServerError,
    ApiTimeoutError,
)
from procurement.collectors.transport import UrllibTransport

URL = "http://example.invalid/api"


class _FakeResponse:
    """``urlopen`` 이 돌려주는 응답을 흉내 냅니다."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, _size: int | None = None) -> bytes:
        """본문을 반환합니다."""
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, behavior: object) -> None:
    """``urlopen`` 을 대역으로 교체합니다."""

    def fake_urlopen(*_args: object, **_kwargs: object) -> object:
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_success_returns_decoded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 응답은 상태 코드와 디코딩된 본문으로 돌아온다."""
    _patch_urlopen(monkeypatch, _FakeResponse(200, "안녕".encode()))

    response = UrllibTransport().get(URL, {"a": "1"}, timeout=1.0)

    assert response.status == 200
    assert response.body == "안녕"


def test_query_string_is_url_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """파라미터는 URL Encode 되어 붙는다."""
    captured: list[str] = []

    def fake_urlopen(request: urllib.request.Request, **_kwargs: object) -> object:
        captured.append(request.full_url)
        return _FakeResponse(200, b"ok")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    UrllibTransport().get(URL, {"serviceKey": "a+b/c=", "bsnmNo": "1234567890"}, timeout=1.0)

    assert "serviceKey=a%2Bb%2Fc%3D" in captured[0]
    assert "bsnmNo=1234567890" in captured[0]


def test_http_500_becomes_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 5xx 는 재시도 대상 오류로 변환된다."""
    error = urllib.error.HTTPError(URL, 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]
    _patch_urlopen(monkeypatch, error)

    with pytest.raises(ApiServerError) as exc_info:
        UrllibTransport().get(URL, {}, timeout=1.0)

    assert exc_info.value.status == 503


def test_http_400_becomes_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 4xx 는 재시도하지 않는 오류로 변환된다."""
    error = urllib.error.HTTPError(URL, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    _patch_urlopen(monkeypatch, error)

    with pytest.raises(ApiRequestError) as exc_info:
        UrllibTransport().get(URL, {}, timeout=1.0)

    assert exc_info.value.status == 401


def test_timeout_becomes_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """응답 지연은 timeout 오류로 구분된다."""
    _patch_urlopen(monkeypatch, TimeoutError())

    with pytest.raises(ApiTimeoutError):
        UrllibTransport().get(URL, {}, timeout=1.0)


def test_urlerror_with_timeout_reason_becomes_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``URLError`` 로 감싸인 timeout 도 timeout 으로 분류한다."""
    _patch_urlopen(monkeypatch, urllib.error.URLError(TimeoutError()))

    with pytest.raises(ApiTimeoutError):
        UrllibTransport().get(URL, {}, timeout=1.0)


def test_connection_failure_becomes_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """연결 실패는 네트워크 오류로 구분된다.

    폐쇄망·차단 환경에서 실제로 나타나는 경로입니다.
    """
    _patch_urlopen(monkeypatch, urllib.error.URLError("Name or service not known"))

    with pytest.raises(ApiNetworkError):
        UrllibTransport().get(URL, {}, timeout=1.0)


def test_os_error_becomes_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """소켓 수준 오류도 네트워크 오류로 구분된다."""
    _patch_urlopen(monkeypatch, OSError("connection reset"))

    with pytest.raises(ApiNetworkError):
        UrllibTransport().get(URL, {}, timeout=1.0)


def test_broken_encoding_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """디코딩 실패로 전송 계층이 죽지 않는다(형식 판단은 파서의 몫)."""
    _patch_urlopen(monkeypatch, _FakeResponse(200, b"\xff\xfe invalid"))

    response = UrllibTransport().get(URL, {}, timeout=1.0)

    assert response.status == 200
    assert response.body  # 대체 문자로라도 문자열이 나온다
