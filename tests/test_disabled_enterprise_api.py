"""
tests.test_disabled_enterprise_api

**장애인기업 확인 API**(``smppCertInfo/getDspsnList``) 조회 경로 검증.

이 파일의 목적은 **실제 응답을 확인하는 것 하나**입니다. 호출 계층과 파서는 이미
있으므로 새로 만들지 않았고, 소스 코드도 바꾸지 않았습니다.

.. note::
    이 파일의 실호출 시험이 2026-08-27 PM 로컬에서 ``resultCode=90`` 을 드러냈고,
    PM 결정으로 장애인기업도 ``90`` 을 "데이터 없음" 으로 처리하게 되었습니다
    (STEP 50). 그 동작은 ``tests/test_disabled_result_code_90.py`` 가 검사합니다.
    공용 파서의 **기본값**은 지금도 명세에 있는 ``03`` 하나뿐입니다.

.. warning::
    **이 파일의 어떤 테스트도 실제 API 서버에 접속하지 않습니다** —
    :class:`TestRealApiCall` 만 예외이며, 인증키와 시험용 사업자번호가 **둘 다**
    설정(``.env`` 또는 환경변수)으로 주어졌을 때만 실행됩니다.

.. note::
    ⚠️ 시험용 사업자번호(``SMPP_TEST_BUSINESS_NO``)가 **장애인기업 확인서를
    보유한 기업인지는 알 수 없습니다.** 이 시험의 목적은 "확인서가 있다" 를
    확인하는 것이 아니라 **API 가 어떤 결과코드로 답하는지**를 보는 것입니다.

.. note::
    인증키는 더미 값이고 사업자번호는 합성값입니다. 실제 키·실제 사업자번호를
    이 파일에 적지 않으며, 실패 메시지에도 넣지 않습니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    SOURCE_DISABLED,
    URL_DISABLED,
    CertificationApiClient,
    FetchResult,
)
from procurement.collectors.errors import StdrDateRequiredError
from procurement.collectors.models import ApiResponseError
from procurement.collectors.smpp import NO_DATA_CODE, parse_cert_list
from procurement.collectors.transport import HttpResponse
from procurement.core.config import settings

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

#: 시험용 기준일자.
#:
#: PM 결정(2026-08-27)에 따르면 인증 유효성 판정 기준일은 **결의일자**입니다.
#: 이 값은 그 규칙에 따라 고른 **시험용 결의일자 한 건**이며, ⛔ 업무 기준일을
#: 여기서 정하는 것이 아닙니다.
TEST_STDR_DATE = date(2026, 8, 1)

#: 명세서 샘플 구조를 따르는 정상 응답 (실제 API 응답이 아님).
#: 장애인기업은 ``certSeCode`` 가 ``04`` 입니다(명세 기재값).
DISABLED_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <certSeCode>04</certSeCode>
    <issuInstt>장애인기업종합지원센터</issuInstt>
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

#: 실호출에서 실제로 돌아온 형태(코드와 메시지만 옮김).
CODE_90 = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>90</resultCode><resultMsg>매칭데이터가 존재하지 않습니다.</resultMsg></header>
</response>
"""


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역."""

    def __init__(self, body: str) -> None:
        """대역을 초기화합니다."""
        self._body = body
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """준비된 본문을 돌려주고 요청 인자를 기록합니다."""
        self.calls.append((url, dict(params)))
        return HttpResponse(status=200, body=self._body)


def _fetch(body: str = DISABLED_OK) -> tuple[FetchResult, StubTransport]:
    """장애인기업 조회를 한 번 수행합니다."""
    transport = StubTransport(body)
    client = CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport)
    return client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=TEST_STDR_DATE), transport


class TestRequest:
    """요청이 명세대로 만들어진다."""

    def test_endpoint_is_the_spec_url(self) -> None:
        _, transport = _fetch()

        url, _ = transport.calls[0]
        assert url == URL_DISABLED
        assert url.endswith("/B550598/smppCertInfo/getDspsnList")

    def test_business_no_is_sent_as_bsnm_no(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["bsnmNo"] == BUSINESS_NO

    def test_service_key_is_sent(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["serviceKey"] == "test-smpp-key"

    def test_stdr_date_is_sent_in_the_spec_format(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["stdrDate"] == "20260801"

    def test_only_the_documented_parameters_are_sent(self) -> None:
        """명세에 있는 파라미터만, 그리고 **전부** 보낸다.

        변경 사유(STEP 51): 공식 활용가이드를 확보해 보니 ``numOfRows`` ·
        ``pageNo`` 가 **필수(항목구분 1)** 였고, 코드가 그 둘을 보내지 않고
        있었다. 이 검사가 지키던 사실은 "지어낸 파라미터를 보내지 않는다" 이며
        그대로다 — 기대 집합을 **명세대로** 넓혔다.
        """
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert set(params) == {"serviceKey", "bsnmNo", "stdrDate", "pageNo", "numOfRows"}

    def test_the_caller_must_supply_the_reference_date(self) -> None:
        """⛔ 코드가 날짜를 임의로 고르지 않는다."""
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key", transport=StubTransport(DISABLED_OK)
        )

        with pytest.raises(StdrDateRequiredError):
            client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=None)


class TestResponseFields:
    """명세 구조를 따르는 응답이 그대로 담긴다."""

    def test_valid_period_is_read(self) -> None:
        result, _ = _fetch()

        record = result.records[0]
        assert (record.valid_from.isoformat(), record.valid_to.isoformat()) == (
            "2024-04-01",
            "2027-03-31",
        )

    def test_cert_code_and_agency(self) -> None:
        result, _ = _fetch()

        record = result.records[0]
        assert (record.cert_code, record.issuing_agency) == ("04", "장애인기업종합지원센터")

    def test_company_name_is_not_provided_by_this_api(self) -> None:
        """이 API 는 기업명·대표자명을 주지 않는다 — 지어내지 않는다."""
        result, _ = _fetch()

        record = result.records[0]
        assert record.company_name is None
        assert record.representative_name is None

    def test_policy_code_is_disabled(self) -> None:
        result, _ = _fetch()

        assert (result.source, result.policy_code) == (SOURCE_DISABLED, "DISABLED")

    def test_no_data_code_returns_empty(self) -> None:
        result, _ = _fetch(NO_DATA)

        assert result.records == ()


class TestCode90IsStillUnconfirmedHere:
    """공용 파서의 **기본값**은 여전히 좁다.

    변경 사유(STEP 50): 이 클래스는 원래 "장애인기업에서도 ``90`` 은 오류" 를
    지켰다. 그것은 **당시 장애인기업 응답을 확인한 적이 없었기 때문**이며,
    "영원히 오류여야 한다" 는 뜻이 아니었다. 바로 이 파일의 실호출 시험이
    PM 로컬에서 ``resultCode=90`` 을 드러냈고, PM 결정으로 넓혔다. 장애인기업
    ``90`` 의 동작은 이제 ``tests/test_disabled_result_code_90.py`` 가 검사한다.

    여기서는 그때 함께 지키던 다른 사실 — **공용 파서의 기본값을 넓히지 않았다**
    — 만 남긴다. 기본값이 넓어지면 확인되지 않은 API 까지 조용히 바뀐다.
    """

    def test_the_shared_parser_default_still_raises(self) -> None:
        """인자를 주지 않으면 ``03`` 하나만 "데이터 없음" 이다."""
        with pytest.raises(ApiResponseError) as caught:
            parse_cert_list(CODE_90, BUSINESS_NO)

        assert caught.value.code == "90"

    def test_the_widening_is_explicit_at_the_call_site(self) -> None:
        """장애인기업이 넓어진 것은 **호출부가 명시적으로 넘겼기** 때문이다."""
        transport = StubTransport(CODE_90)
        client = CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport)

        result = client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=TEST_STDR_DATE)

        assert result.records == ()

    def test_only_the_documented_code_counts_as_no_data(self) -> None:
        assert NO_DATA_CODE == "03"


# ---------------------------------------------------------------------------
# 실제 API 호출 — 기본은 건너뜀
# ---------------------------------------------------------------------------

_REAL_KEY = (settings.SMPP_API_KEY or "").strip()
_REAL_BUSINESS_NO = (settings.SMPP_TEST_BUSINESS_NO or "").strip()


@pytest.mark.skipif(
    not (_REAL_KEY and _REAL_BUSINESS_NO),
    reason=(
        "실제 API 호출 시험은 SMPP_API_KEY 와 SMPP_TEST_BUSINESS_NO 가 "
        "둘 다 설정된 환경에서만 수행합니다(.env 또는 환경변수). "
        "값이 없으면 실패가 아니라 건너뜁니다."
    ),
)
class TestRealApiCall:
    """실제 엔드포인트 연결 확인.

    .. warning::
        인증키와 사업자번호를 로그·실패 메시지에 출력하지 않습니다. 실패
        메시지에는 **결과코드와 결과메시지만** 담습니다 — 그것이 이 시험에서
        알아내려는 값이기 때문입니다.
    """

    def test_the_endpoint_answers_and_the_response_parses(self) -> None:
        """응답이 오고, 그 응답이 파서를 통과한다.

        ⛔ 확인서가 있든 없든 둘 다 정상입니다. "있으면 장애인기업" 이라고 하지
        않으며, 유효기간을 결의일자와 비교하지도 않습니다.

        문서화되지 않은 결과코드가 오면 **그 코드와 메시지를 그대로 드러내며**
        실패합니다. 조용히 넘기면 무엇이 왔는지 알 수 없습니다.
        """
        client = CertificationApiClient(smpp_api_key=_REAL_KEY)

        try:
            result = client.fetch(SOURCE_DISABLED, _REAL_BUSINESS_NO, stdr_date=TEST_STDR_DATE)
        except ApiResponseError as exc:
            pytest.fail(
                "장애인기업 API 가 현재 코드가 모르는 결과코드로 답했습니다. "
                f"resultCode={exc.code} / resultMsg={exc.message} — "
                "이 값을 그대로 보고하세요. 코드를 임의로 바꾸지 않습니다."
            )

        assert isinstance(result.records, tuple)
        assert result.source == SOURCE_DISABLED
        assert result.policy_code == "DISABLED"
