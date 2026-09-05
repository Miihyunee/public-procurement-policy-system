"""
tests.test_collector_client

외부 인증 API 호출 계층(:mod:`procurement.collectors.client`) 검증.

.. warning::
    **이 파일의 어떤 테스트도 실제 API 서버에 접속하지 않습니다.**
    모든 응답은 :class:`StubTransport` 가 돌려주는 고정 문자열이며, 공식 명세서에
    실린 샘플 구조를 따릅니다. 여기의 통과는 "코드가 명세대로 동작한다"는 뜻이지
    **"실제 API 연동이 검증되었다"는 뜻이 아닙니다.**

.. note::
    테스트에 쓰는 인증키는 ``"test-key"`` 같은 명백한 더미 값입니다. 실제 키를
    테스트 코드에 넣지 않습니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    DEFAULT_MAX_ATTEMPTS,
    SOURCE_DISABLED,
    SOURCE_POLICY_CODES,
    SOURCE_STARTUP_KISED,
    SOURCE_STARTUP_SMPP,
    SOURCE_WOMAN,
    URL_DISABLED,
    URL_STARTUP_KISED,
    URL_WOMAN,
    ApiKeyNotConfiguredError,
    CertificationApiClient,
)
from procurement.collectors.errors import (
    ApiAuthError,
    ApiNetworkError,
    ApiQuotaError,
    ApiRequestError,
    ApiServerError,
    ApiTimeoutError,
    StdrDateRequiredError,
)
from procurement.collectors.models import ApiParseError, ApiResponseError
from procurement.collectors.transport import HttpResponse

BUSINESS_NO = "4021497692"
STDR_DATE = date(2026, 8, 14)

# ---------------------------------------------------------------------------
# 명세서 샘플 구조를 따르는 응답 (실제 API 응답이 아님)
# ---------------------------------------------------------------------------

WOMAN_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <certSeCode>03</certSeCode>
    <issuInstt>한국여성경제인협회</issuInstt>
    <validPdBeginDe>20240401</validPdBeginDe>
    <validPdEndDe>20270331</validPdEndDe>
  </item></items></body>
</response>
"""

NO_DATA = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header>
</response>
"""

STARTUP_SMPP_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <bsnmNo>4021497692</bsnmNo>
    <entrpsNm>테스트기업</entrpsNm>
    <rprsntvNm>홍길동</rprsntvNm>
    <validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>
  </item></items></body>
</response>
"""

KISED_OK = """
{"data": [{"brno": "4021497692", "ntrp_nm": "테스트기업", "repr_nm": "홍길동",
"confmdoc_isu_no": "2024-0001", "confmdoc_isu_dt": "2024-04-01",
"confmdoc_expr_dt": "2027-03-31"}]}
"""


def _error_xml(code: str, message: str) -> str:
    """문서화된 결과코드를 담은 응답을 만듭니다."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<response><header><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></header></response>"
    )


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역.

    ``responses`` 를 순서대로 하나씩 돌려줍니다. 항목이 예외이면 그 예외를
    발생시킵니다. 요청 인자는 :attr:`calls` 에 기록해 검증에 씁니다.
    """

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        """대역을 초기화합니다."""
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        timeout: float,
    ) -> HttpResponse:
        """기록해 둔 응답을 순서대로 반환합니다."""
        self.calls.append((url, dict(params), timeout))
        if not self._responses:
            raise AssertionError("대역에 준비된 응답보다 많이 호출되었습니다.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(body: str) -> HttpResponse:
    """HTTP 200 응답을 만듭니다."""
    return HttpResponse(status=200, body=body)


def _client(*responses: HttpResponse | Exception, **kwargs: object) -> tuple[
    CertificationApiClient, StubTransport
]:
    """대역이 끼워진 호출기를 만듭니다."""
    transport = StubTransport(*responses)
    client = CertificationApiClient(
        smpp_api_key="test-smpp-key",
        startup_api_key="test-startup-key",
        transport=transport,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, transport


# ---------------------------------------------------------------------------
# 1. 정상 응답
# ---------------------------------------------------------------------------


def test_woman_success_returns_parsed_record() -> None:
    """여성기업 정상 응답이 기존 파서를 거쳐 레코드로 돌아온다."""
    client, transport = _client(_ok(WOMAN_OK))

    result = client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.policy_code == "WOMAN"
    assert len(result.records) == 1
    record = result.records[0]
    assert record.business_no == BUSINESS_NO
    assert record.valid_from == date(2024, 4, 1)
    assert record.valid_to == date(2027, 3, 31)
    assert record.cert_code == "03"
    assert transport.calls[0][0] == URL_WOMAN


def test_disabled_uses_its_own_endpoint() -> None:
    """장애인기업은 여성기업과 응답 구조가 같아도 엔드포인트가 다르다."""
    client, transport = _client(_ok(WOMAN_OK))

    result = client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.policy_code == "DISABLED"
    assert transport.calls[0][0] == URL_DISABLED


def test_startup_smpp_success() -> None:
    """창업기업(SMPP) 정상 응답이 범위 문자열 유효기간까지 해석된다."""
    client, _ = _client(_ok(STARTUP_SMPP_OK))

    result = client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    assert result.policy_code == "STARTUP"
    assert result.records[0].valid_from == date(2022, 4, 7)
    assert result.records[0].valid_to == date(2025, 4, 6)
    assert result.records[0].company_name == "테스트기업"


def test_startup_kised_success_requests_json() -> None:
    """창업기업(창업진흥원)은 응답 형식이 바뀌지 않도록 JSON 을 명시 요청한다."""
    client, transport = _client(_ok(KISED_OK))

    result = client.fetch(SOURCE_STARTUP_KISED, BUSINESS_NO, stdr_date=None)

    assert result.records[0].certificate_number == "2024-0001"
    url, params, _timeout = transport.calls[0]
    assert url == URL_STARTUP_KISED
    assert params["returnType"] == "JSON"
    assert params["brno"] == BUSINESS_NO


# ---------------------------------------------------------------------------
# 2. 정상 응답이지만 데이터가 없는 경우 — 오류가 아니다
# ---------------------------------------------------------------------------


def test_no_data_is_not_an_error() -> None:
    """결과코드 03(데이터 없음)은 예외가 아니라 빈 목록이다."""
    client, _ = _client(_ok(NO_DATA))

    result = client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.records == ()
    assert result.attempts == 1


# ---------------------------------------------------------------------------
# 3. 오류 범주 구분
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["20", "30", "32"])
def test_auth_error_codes_are_classified(code: str) -> None:
    """미승인·키오류·IP 미등록은 인증 실패로 구분된다."""
    client, _ = _client(_ok(_error_xml(code, "SERVICE ERROR")))

    with pytest.raises(ApiAuthError) as exc_info:
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert exc_info.value.code == code


def test_quota_error_code_is_classified() -> None:
    """일일 한도 초과(22)는 별도 범주로 구분된다."""
    client, _ = _client(_ok(_error_xml("22", "LIMITED NUMBER OF SERVICE REQUESTS")))

    with pytest.raises(ApiQuotaError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)


def test_undocumented_code_is_not_guessed() -> None:
    """명세에 없는 코드는 추측해서 분류하지 않고 일반 오류로 남는다."""
    client, _ = _client(_ok(_error_xml("99", "UNKNOWN")))

    with pytest.raises(ApiResponseError) as exc_info:
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert not isinstance(exc_info.value, ApiAuthError | ApiQuotaError)


def test_malformed_response_raises_parse_error() -> None:
    """응답 형식 오류는 파싱 오류로 구분된다."""
    client, _ = _client(_ok("이건 XML 이 아닙니다"))

    with pytest.raises(ApiParseError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)


def test_http_4xx_from_transport_is_request_error() -> None:
    """전송 계층이 상태 코드를 예외로 올리지 않아도 4xx 는 요청 오류가 된다."""
    client, _ = _client(HttpResponse(status=400, body="bad request"))

    with pytest.raises(ApiRequestError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)


# ---------------------------------------------------------------------------
# 4. 재시도 — 일시적 장애만
# ---------------------------------------------------------------------------


def test_timeout_is_retried_then_succeeds() -> None:
    """timeout 은 일시적 장애이므로 재시도한다."""
    client, transport = _client(ApiTimeoutError("시간 초과"), _ok(WOMAN_OK))

    result = client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.attempts == 2
    assert len(transport.calls) == 2


def test_network_error_is_retried() -> None:
    """네트워크 오류도 재시도 대상이다."""
    client, transport = _client(ApiNetworkError("연결 실패"), _ok(WOMAN_OK))

    client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 2


def test_server_error_is_retried() -> None:
    """HTTP 5xx 는 재시도 대상이다."""
    client, transport = _client(ApiServerError(503, "unavailable"), _ok(WOMAN_OK))

    client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 2


def test_retry_exhausted_raises_last_error() -> None:
    """재시도를 다 써도 실패하면 마지막 오류를 그대로 올린다."""
    client, transport = _client(ApiTimeoutError("1차"), ApiTimeoutError("2차"))

    with pytest.raises(ApiTimeoutError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == DEFAULT_MAX_ATTEMPTS


def test_auth_failure_is_not_retried() -> None:
    """인증 실패는 다시 보내도 결과가 같으므로 재시도하지 않는다.

    재시도하면 남은 일일 호출 한도만 소모합니다.
    """
    client, transport = _client(_ok(_error_xml("30", "SERVICE KEY IS NOT REGISTERED")))

    with pytest.raises(ApiAuthError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 1


def test_quota_error_is_not_retried() -> None:
    """한도 초과 재시도는 한도를 더 소모할 뿐이다."""
    client, transport = _client(_ok(_error_xml("22", "LIMITED")))

    with pytest.raises(ApiQuotaError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 1


def test_request_error_is_not_retried() -> None:
    """잘못된 요청은 재시도하지 않는다."""
    client, transport = _client(ApiRequestError(400, "bad request"))

    with pytest.raises(ApiRequestError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 1


def test_max_attempts_one_disables_retry() -> None:
    """max_attempts=1 이면 재시도하지 않는다."""
    client, transport = _client(ApiTimeoutError("시간 초과"), max_attempts=1)

    with pytest.raises(ApiTimeoutError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert len(transport.calls) == 1


def test_max_attempts_must_be_positive() -> None:
    """시도 횟수는 1 이상이어야 한다."""
    with pytest.raises(ValueError, match="max_attempts"):
        CertificationApiClient(max_attempts=0)


# ---------------------------------------------------------------------------
# 5. stdrDate — 코드가 임의로 정하지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [SOURCE_WOMAN, SOURCE_DISABLED])
def test_stdr_date_is_required_and_never_defaulted(source: str) -> None:
    """기준일을 주지 않으면 호출하지 않고 오류를 낸다.

    오늘 날짜·연도 말일 등을 코드가 대신 고르면, 업무적으로 결정되지 않은
    기준으로 판정한 결과가 조용히 저장됩니다.
    """
    client, transport = _client()

    with pytest.raises(StdrDateRequiredError):
        client.fetch(source, BUSINESS_NO, stdr_date=None)

    assert transport.calls == []


def test_stdr_date_is_sent_in_spec_format() -> None:
    """전달받은 기준일은 명세 형식(YYYYMMDD)으로 보낸다."""
    client, transport = _client(_ok(WOMAN_OK))

    client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=date(2026, 8, 14))

    assert transport.calls[0][1]["stdrDate"] == "20260814"


def test_client_has_no_stdr_date_default() -> None:
    """``fetch`` 는 ``stdr_date`` 기본값을 갖지 않는다(생략 시 TypeError)."""
    client, _ = _client(_ok(WOMAN_OK))

    with pytest.raises(TypeError):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO)  # type: ignore[call-arg]


def test_startup_sources_do_not_send_stdr_date() -> None:
    """창업기업 조회는 명세에 기준일 파라미터가 없으므로 보내지 않는다."""
    client, transport = _client(_ok(KISED_OK))

    client.fetch(SOURCE_STARTUP_KISED, BUSINESS_NO, stdr_date=None)

    assert "stdrDate" not in transport.calls[0][1]


# ---------------------------------------------------------------------------
# 6. 인증키
# ---------------------------------------------------------------------------


def test_missing_smpp_key_fails_before_calling() -> None:
    """키가 없으면 네트워크를 쓰지 않고 즉시 실패한다."""
    transport = StubTransport()
    client = CertificationApiClient(smpp_api_key=None, transport=transport)

    with pytest.raises(ApiKeyNotConfiguredError, match="SMPP_API_KEY"):
        client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert transport.calls == []


def test_blank_key_is_treated_as_missing() -> None:
    """공백만 있는 키는 설정되지 않은 것으로 본다."""
    transport = StubTransport()
    client = CertificationApiClient(startup_api_key="   ", transport=transport)

    with pytest.raises(ApiKeyNotConfiguredError, match="STARTUP_API_KEY"):
        client.fetch(SOURCE_STARTUP_KISED, BUSINESS_NO, stdr_date=None)


def test_key_is_sent_as_service_key() -> None:
    """인증키는 명세대로 ``serviceKey`` 파라미터로 전달된다."""
    client, transport = _client(_ok(WOMAN_OK))

    client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert transport.calls[0][1]["serviceKey"] == "test-smpp-key"


def test_client_can_be_constructed_without_keys() -> None:
    """키가 없어도 객체 생성 자체는 실패하지 않는다(구성 시점 분리)."""
    assert CertificationApiClient() is not None


# ---------------------------------------------------------------------------
# 7. 출처 정의
# ---------------------------------------------------------------------------


def test_unknown_source_is_rejected() -> None:
    """정의되지 않은 출처는 호출하지 않는다."""
    client, transport = _client()

    with pytest.raises(ValueError, match="조회 출처"):
        client.fetch("UNKNOWN", BUSINESS_NO, stdr_date=None)

    assert transport.calls == []


def test_startup_has_two_sources_and_code_does_not_choose() -> None:
    """창업기업은 확보한 API 가 2종이며, 코드가 한쪽을 기본으로 고르지 않는다."""
    startup_sources = [
        source for source, policy in SOURCE_POLICY_CODES.items() if policy == "STARTUP"
    ]

    assert sorted(startup_sources) == sorted([SOURCE_STARTUP_SMPP, SOURCE_STARTUP_KISED])
