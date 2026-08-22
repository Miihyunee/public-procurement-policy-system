"""
procurement.collectors.errors

외부 인증 API 호출 과정에서 발생하는 오류를 **범주별로 구분**합니다.

오류를 하나로 뭉뚱그리면 "재시도해도 되는 오류"와 "재시도하면 안 되는 오류"를
구분할 수 없습니다. 인증키가 틀렸는데 계속 재시도하면 일일 호출 한도만 소모합니다.

범주
====

============================  ==========================  ==========
예외                            의미                          재시도
============================  ==========================  ==========
:class:`ApiTimeoutError`      응답 시간 초과                  ✅ 대상
:class:`ApiNetworkError`      연결 실패 등 네트워크 오류        ✅ 대상
:class:`ApiServerError`       HTTP 5xx                    ✅ 대상
:class:`ApiAuthError`         인증키 오류 · 미승인 · IP 미등록   ❌ 금지
:class:`ApiRequestError`      잘못된 요청 (HTTP 4xx 등)       ❌ 금지
:class:`ApiQuotaError`        일일 호출 한도 초과              ❌ 금지
``ApiResponseError``          그 밖의 문서화된 오류 코드        ❌ 금지
``ApiParseError``             응답 형식 오류                  ❌ 금지
============================  ==========================  ==========

"정상 응답인데 데이터가 없음"(결과코드 ``03``)은 **오류가 아닙니다.** 파서가 빈
목록을 반환하며 이 모듈의 예외를 발생시키지 않습니다.

.. note::
    각 결과코드의 의미는 공공데이터 오픈API 활용가이드에 기재된 값을 그대로
    사용했습니다(``docs/DATA_ACQUISITION_PLAN.md`` §2.2.0). 명세에 없는 코드는
    추측해서 분류하지 않고 :class:`~procurement.collectors.models.ApiResponseError`
    로 남깁니다.
"""

from __future__ import annotations

from procurement.collectors.models import ApiResponseError


class ApiTransportError(RuntimeError):
    """HTTP 계층에서 발생한 오류의 공통 상위 타입."""


class ApiTimeoutError(ApiTransportError):
    """응답이 제한 시간 안에 오지 않았습니다. **재시도 대상**입니다."""


class ApiNetworkError(ApiTransportError):
    """연결 실패 등 네트워크 오류입니다. **재시도 대상**입니다."""


class ApiServerError(ApiTransportError):
    """서버가 HTTP 5xx 를 반환했습니다. **재시도 대상**입니다.

    Attributes:
        status: HTTP 상태 코드.
    """

    def __init__(self, status: int, message: str = "") -> None:
        """오류를 초기화합니다."""
        super().__init__(f"외부 API 서버 오류 (HTTP {status}): {message}".rstrip(": "))
        self.status = status


class ApiRequestError(ApiTransportError):
    """요청이 거부되었습니다(HTTP 4xx). **재시도하지 않습니다.**

    같은 요청을 다시 보내도 같은 결과가 나오므로, 재시도는 호출 한도만 소모합니다.

    Attributes:
        status: HTTP 상태 코드.
    """

    def __init__(self, status: int, message: str = "") -> None:
        """오류를 초기화합니다."""
        super().__init__(f"외부 API 요청 오류 (HTTP {status}): {message}".rstrip(": "))
        self.status = status


class ApiAuthError(ApiResponseError):
    """인증에 실패했습니다. **재시도하지 않습니다.**

    다음 결과코드가 여기에 해당합니다(명세 기재값).

    - ``20`` 서비스 접근 거부 (활용 승인 안 됨)
    - ``30`` 등록되지 않은 서비스키
    - ``32`` 등록되지 않은 IP · 도메인

    .. warning::
        ``32`` 는 **키가 맞아도** 호출 서버의 IP 가 활용신청 내역과 다르면
        발생합니다. 배포 환경의 IP 를 포털에 등록해야 합니다.
    """


class ApiQuotaError(ApiResponseError):
    """일일 호출 한도를 초과했습니다(결과코드 ``22``). **재시도하지 않습니다.**

    재시도하면 남은 한도를 더 소모할 뿐입니다. 다음 갱신 주기까지 기다려야 합니다.
    """


class StdrDateRequiredError(ValueError):
    """``stdrDate`` 없이 기준일이 필요한 API 를 호출했습니다.

    ``stdrDate``(기준일자)의 업무적 의미는 **아직 확정되지 않았습니다**(D-24 관련).
    따라서 코드가 오늘 날짜·연도 말일·지급일·계약일 중 **어느 것도 임의로 고르지
    않습니다.** 호출자가 명시적으로 전달해야 합니다.
    """


#: 재시도해도 되는 예외 (일시적 장애로 분류된 것만)
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ApiTimeoutError,
    ApiNetworkError,
    ApiServerError,
)

#: 인증 실패로 분류하는 결과코드 (명세 기재값)
AUTH_ERROR_CODES: frozenset[str] = frozenset({"20", "30", "32"})

#: 호출 한도 초과로 분류하는 결과코드 (명세 기재값)
QUOTA_ERROR_CODES: frozenset[str] = frozenset({"22"})


def classify_result_code(error: ApiResponseError) -> ApiResponseError:
    """결과코드를 근거로 오류를 더 구체적인 범주로 바꿉니다.

    파서는 문서화된 결과코드를 :class:`ApiResponseError` 로 올립니다. 이 함수는
    그 코드를 명세에 기재된 의미대로 재분류해, 호출 계층이 "재시도 금지" 여부를
    판단할 수 있게 합니다.

    Args:
        error: 파서가 발생시킨 오류.

    Returns:
        재분류된 오류. 명세에 없는 코드는 **그대로 반환**합니다(추측하지 않음).
    """
    if error.code in AUTH_ERROR_CODES:
        return ApiAuthError(error.code, error.message)
    if error.code in QUOTA_ERROR_CODES:
        return ApiQuotaError(error.code, error.message)
    return error
