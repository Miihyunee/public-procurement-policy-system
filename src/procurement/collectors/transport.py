"""
procurement.collectors.transport

외부 API 로의 **HTTP 전송**만 담당하는 최하위 계층입니다.

이 모듈은 응답 본문을 문자열로 받아오는 것까지만 합니다. 응답의 의미를 해석하지
않으며, 인증 판정 로직도 담지 않습니다. 해석은 파서(``smpp`` · ``kised``)가 합니다.

계층
====

::

    Transport (이 모듈)   ← HTTP 요청/응답 문자열
        ↓
    ApiClient             ← 엔드포인트 · 파라미터 · 재시도 · 오류 분류
        ↓
    Parser                ← 응답 해석 (기존 코드 재사용)
        ↓
    Certification         ← 저장

:class:`Transport` 는 프로토콜(인터페이스)이므로, 테스트에서는 네트워크 없이
응답을 그대로 돌려주는 대역(stub)을 끼워 넣을 수 있습니다.

.. note::
    외부 의존성을 추가하지 않기 위해 표준 라이브러리 ``urllib`` 을 사용합니다.
    폐쇄망 배포에서 설치할 패키지가 늘어나지 않습니다.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from procurement.collectors.errors import (
    ApiNetworkError,
    ApiRequestError,
    ApiServerError,
    ApiTimeoutError,
)

#: 기본 응답 대기 시간(초).
#:
#: 명세서 기재 성능은 "평균 응답 500ms"입니다. 그 10배 이상을 기다렸는데도 응답이
#: 없으면 일시적 장애로 보는 편이 낫다고 판단해 10초로 두었습니다. 호출자가
#: 언제든 바꿀 수 있습니다.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: 응답 본문을 읽을 최대 바이트 수.
#:
#: 명세서 기재 "최대 메시지 4000 byte" 보다 넉넉하게 잡되, 응답이 비정상적으로
#: 클 때 메모리를 무한정 쓰지 않도록 상한을 둡니다.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class HttpResponse:
    """HTTP 응답 한 건.

    Attributes:
        status: HTTP 상태 코드.
        body: 응답 본문(문자열로 디코딩된 것).
    """

    status: int
    body: str


class Transport(Protocol):
    """HTTP GET 을 수행하는 최소 인터페이스.

    테스트에서는 이 프로토콜을 만족하는 대역을 넘겨 네트워크 없이 검증합니다.
    """

    def get(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        timeout: float,
    ) -> HttpResponse:
        """질의 문자열을 붙여 GET 요청을 보냅니다.

        Raises:
            ApiTimeoutError: 제한 시간 안에 응답이 없는 경우.
            ApiNetworkError: 연결 실패 등 네트워크 오류.
            ApiServerError: HTTP 5xx.
            ApiRequestError: HTTP 4xx.
        """
        ...  # pragma: no cover - 프로토콜 정의


class UrllibTransport:
    """``urllib`` 기반 기본 전송 구현.

    .. warning::
        폐쇄망·차단 환경에서는 이 구현이 :class:`ApiNetworkError` 를 발생시킵니다.
        정상 동작이며, 그 경우 "실호출 미검증" 으로 취급해야 합니다.
    """

    def get(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """질의 문자열을 붙여 GET 요청을 보냅니다.

        Args:
            url: 엔드포인트 URL.
            params: 질의 파라미터. 값은 URL Encode 되어 붙습니다.
            timeout: 응답 대기 시간(초).

        Returns:
            :class:`HttpResponse`.

        Raises:
            ApiTimeoutError: 제한 시간 안에 응답이 없는 경우.
            ApiNetworkError: 연결 실패 등 네트워크 오류.
            ApiServerError: HTTP 5xx.
            ApiRequestError: HTTP 4xx.
        """
        query = urllib.parse.urlencode(dict(params))
        full_url = f"{url}?{query}" if query else url
        request = urllib.request.Request(full_url, method="GET")  # noqa: S310

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES)
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            body = _safe_decode(exc.read())
            if exc.code >= 500:
                raise ApiServerError(exc.code, body) from exc
            raise ApiRequestError(exc.code, body) from exc
        except TimeoutError as exc:
            raise ApiTimeoutError(f"외부 API 응답 시간 초과 ({timeout}초)") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ApiTimeoutError(f"외부 API 응답 시간 초과 ({timeout}초)") from exc
            raise ApiNetworkError(f"외부 API 에 연결할 수 없습니다: {exc.reason}") from exc
        except OSError as exc:
            raise ApiNetworkError(f"외부 API 에 연결할 수 없습니다: {exc}") from exc

        return HttpResponse(status=status, body=_safe_decode(raw))


def _safe_decode(raw: bytes) -> str:
    """응답 바이트를 문자열로 디코딩합니다.

    공공데이터포털 응답은 UTF-8 이지만, 오류 본문이 다른 인코딩으로 오는 경우가
    있어 실패해도 예외를 던지지 않고 대체 문자로 넘깁니다. 형식이 깨진 응답은
    이후 파서가 :class:`~procurement.collectors.models.ApiParseError` 로 잡습니다.
    """
    return raw.decode("utf-8", errors="replace")
