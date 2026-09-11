"""
tests.test_disabled_result_code_90

장애인기업 확인 API 의 **결과코드 ``90``** 처리 검증.

배경
====

2026-08-27 PM 로컬 실호출에서 ``smppCertInfo/getDspsnList`` 가 결과코드 ``90``
"매칭데이터가 존재하지 않습니다" 를 돌려주었습니다 — 창업기업·여성기업과 **같은
코드, 같은 메시지**입니다. 이 코드는 **공식 활용가이드에 없습니다.**

PM 결정(2026-08-27)에 따라 장애인기업 조회에서도 ``03`` 과 같은 **"정상 응답이지만
조회 결과 없음"** 으로 처리합니다. 이로써 SMPP 계열 3종이 모두 **각각의 실호출
근거**로 넓혀졌습니다.

이 파일이 고정하는 사실
=======================

1. 장애인기업 ``90`` 은 오류가 아니라 **빈 결과**다
2. ⛔ ``00``(데이터 있음)으로 바뀐 것이 **아니다**
3. ⛔ **공용 파서의 기본값은 여전히 ``03`` 하나뿐**이다
4. 인증 오류·한도 초과·그 밖의 모르는 코드 처리는 그대로다

.. note::
    실제 API 서버에 접속하지 않습니다. 인증키는 더미, 사업자번호는 합성값입니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    SMPP_CERT_NO_DATA_CODES,
    SOURCE_DISABLED,
    SOURCE_STARTUP_SMPP,
    SOURCE_WOMAN,
    CertificationApiClient,
    FetchResult,
)
from procurement.collectors.errors import ApiAuthError, ApiQuotaError
from procurement.collectors.models import ApiResponseError
from procurement.collectors.smpp import (
    DISABLED_NO_DATA_CODES,
    NO_DATA_CODE,
    STARTUP_NO_DATA_CODES,
    SUCCESS_CODE,
    WOMAN_NO_DATA_CODES,
    parse_cert_list,
    parse_startup_cert,
)
from procurement.collectors.transport import HttpResponse

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

STDR_DATE = date(2026, 8, 1)


def _response(code: str, message: str, body: str = "") -> str:
    """결과코드를 담은 응답을 만듭니다."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<response><header><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></header>"
        f"<body>{body}</body></response>"
    )


#: 실호출에서 실제로 돌아온 형태(코드와 메시지만 옮김).
CODE_90 = _response("90", "매칭데이터가 존재하지 않습니다.")

#: ``90`` 인데 항목이 섞여 들어온 경우 — 그것도 해석하지 않아야 한다.
CODE_90_WITH_JUNK = _response(
    "90",
    "매칭데이터가 존재하지 않습니다.",
    "<items><item><issuInstt>부스러기</issuInstt></item></items>",
)

CODE_03 = _response(NO_DATA_CODE, "NODATA_ERROR")

#: 명세서 샘플 구조를 따르는 정상 응답 (실제 API 응답이 아님).
#:
#: ⚠️ 장애인기업의 **실제 ``00`` 응답은 아직 확인되지 않았습니다.** 이 값은
#: 명세서 구조를 따른 것이며, 실제 응답이 이와 같다는 뜻이 아닙니다.
DISABLED_OK = _response(
    SUCCESS_CODE,
    "NORMAL SERVICE.",
    "<items><item>"
    "<certSeCode>04</certSeCode>"
    "<issuInstt>장애인기업종합지원센터</issuInstt>"
    "<validPdBeginDe>20240401</validPdBeginDe>"
    "<validPdEndDe>20270331</validPdEndDe>"
    "</item></items>",
)

STARTUP_OK = _response(
    SUCCESS_CODE,
    "NORMAL SERVICE.",
    "<items><item>"
    "<bsnmNo>1000000001</bsnmNo>"
    "<entrpsNm>합성기업</entrpsNm>"
    "<validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>"
    "</item></items>",
)


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역."""

    def __init__(self, body: str) -> None:
        """대역을 초기화합니다."""
        self._body = body
        self.calls = 0

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """준비된 본문을 돌려줍니다."""
        self.calls += 1
        return HttpResponse(status=200, body=self._body)


def _client(body: str) -> tuple[CertificationApiClient, StubTransport]:
    """대역이 끼워진 호출기를 만듭니다."""
    transport = StubTransport(body)
    return (
        CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport),
        transport,
    )


def _fetch_disabled(body: str) -> tuple[FetchResult, StubTransport]:
    """장애인기업 조회를 한 번 수행합니다."""
    client, transport = _client(body)
    return client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE), transport


# ---------------------------------------------------------------------------
# ①②③ 결과코드 90 — 빈 결과 · 재시도 없음 · 확인서 미생성
# ---------------------------------------------------------------------------


class TestCode90IsEmptyNotAnError:
    """장애인기업 ``90`` 은 오류가 아니라 "조회 결과 없음" 이다."""

    def test_parser_returns_an_empty_list(self) -> None:
        assert parse_cert_list(CODE_90, BUSINESS_NO, no_data_codes=DISABLED_NO_DATA_CODES) == []

    def test_client_returns_an_empty_result_without_raising(self) -> None:
        result, _ = _fetch_disabled(CODE_90)

        assert result.records == ()

    def test_the_call_still_reports_which_source_answered(self) -> None:
        result, _ = _fetch_disabled(CODE_90)

        assert (result.source, result.policy_code) == (SOURCE_DISABLED, "DISABLED")

    def test_it_is_not_retried(self) -> None:
        """정상 응답이므로 다시 부르지 않는다 — HTTP·네트워크 오류가 아니다."""
        _, transport = _fetch_disabled(CODE_90)

        assert transport.calls == 1

    def test_no_certification_is_fabricated(self) -> None:
        """⛔ 데이터 없음이 "확인서 한 건" 으로 바뀌지 않는다."""
        result, _ = _fetch_disabled(CODE_90)

        assert len(result.records) == 0


# ---------------------------------------------------------------------------
# ④ 00 과 구분 · 부스러기 미해석
# ---------------------------------------------------------------------------


class TestCode90IsNotTheSuccessCode:
    """⛔ ``90`` 을 ``00`` 으로 바꾼 것이 아니다."""

    def test_success_parses_items_but_ninety_does_not(self) -> None:
        assert len(_fetch_disabled(DISABLED_OK)[0].records) == 1
        assert _fetch_disabled(CODE_90)[0].records == ()

    def test_the_success_code_is_not_in_the_widened_set(self) -> None:
        assert SUCCESS_CODE not in DISABLED_NO_DATA_CODES

    def test_leftover_items_are_not_parsed(self) -> None:
        """부스러기가 섞여 있어도 해석하지 않는다.

        해석했다면 ``validPdBeginDe`` 가 없어 파싱 오류가 났을 것이다.
        """
        result, _ = _fetch_disabled(CODE_90_WITH_JUNK)

        assert result.records == ()


# ---------------------------------------------------------------------------
# ⑤ 03 회귀
# ---------------------------------------------------------------------------


class TestCode03IsUnchanged:
    """명세에 있던 ``03`` 의 동작은 그대로다."""

    def test_disabled_still_returns_empty(self) -> None:
        result, _ = _fetch_disabled(CODE_03)

        assert result.records == ()

    def test_success_response_still_parses(self) -> None:
        result, _ = _fetch_disabled(DISABLED_OK)

        record = result.records[0]
        assert (record.cert_code, record.issuing_agency) == ("04", "장애인기업종합지원센터")


# ---------------------------------------------------------------------------
# ⑥⑦⑧ 오류 코드 회귀
# ---------------------------------------------------------------------------


class TestErrorCodesAreUnchanged:
    """인증·한도 오류는 그대로 오류다."""

    @pytest.mark.parametrize("code", ["20", "30", "32"])
    def test_auth_codes_still_raise(self, code: str) -> None:
        client, transport = _client(_response(code, "AUTH"))

        with pytest.raises(ApiAuthError):
            client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)
        # ⛔ 인증 오류는 재시도하지 않는다 — 한도만 소모한다
        assert transport.calls == 1

    def test_quota_code_still_raises(self) -> None:
        client, _ = _client(_response("22", "LIMITED NUMBER OF SERVICE REQUESTS"))

        with pytest.raises(ApiQuotaError):
            client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)

    @pytest.mark.parametrize("code", ["01", "10", "31", "99"])
    def test_other_unknown_codes_are_still_not_guessed(self, code: str) -> None:
        """⛔ ``90`` 하나만 열었다 — 나머지 모르는 코드는 여전히 오류다."""
        client, _ = _client(_response(code, "무언가"))

        with pytest.raises(ApiResponseError):
            client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)


# ---------------------------------------------------------------------------
# ⑩⑪ 여성기업·창업기업 회귀
# ---------------------------------------------------------------------------


class TestOtherSourcesAreUnchanged:
    """여성기업(STEP 48) · 창업기업(STEP 46) 동작이 그대로다."""

    def test_woman_ninety_still_returns_empty(self) -> None:
        client, _ = _client(CODE_90)

        assert client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE).records == ()

    def test_woman_no_data_still_returns_empty(self) -> None:
        client, _ = _client(CODE_03)

        assert client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE).records == ()

    def test_woman_success_still_parses(self) -> None:
        client, _ = _client(DISABLED_OK)

        assert len(client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE).records) == 1

    def test_startup_ninety_still_returns_empty(self) -> None:
        client, _ = _client(CODE_90)

        assert client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None).records == ()

    def test_startup_no_data_still_returns_empty(self) -> None:
        assert parse_startup_cert(CODE_03) == []

    def test_startup_success_still_parses(self) -> None:
        assert len(parse_startup_cert(STARTUP_OK)) == 1


# ---------------------------------------------------------------------------
# ⑫ 범위 분리 — 가장 중요한 검사
# ---------------------------------------------------------------------------


class TestScopeSeparation:
    """넓힌 것은 **호출부**이지 공용 파서가 아니다."""

    def test_the_shared_parser_default_still_raises_on_90(self) -> None:
        """공용 파서를 그냥 부르면 ``90`` 은 여전히 오류다.

        이 검사가 이번 변경의 핵심 안전장치다. 기본값을 넓혔다면 아직 확인되지
        않은 API 까지 조용히 바뀐다.
        """
        with pytest.raises(ApiResponseError) as caught:
            parse_cert_list(CODE_90, BUSINESS_NO)

        assert caught.value.code == "90"

    def test_the_documented_default_is_still_one_code(self) -> None:
        assert NO_DATA_CODE == "03"

    def test_each_source_keeps_its_own_constant(self) -> None:
        """⛔ 세 상수를 하나로 합치지 않았다.

        값이 같아도 **각각 하나의 실호출 근거**를 가리킨다. 합치면 "어느 API
        에서 확인되었는가" 가 코드에서 사라진다.
        """
        assert STARTUP_NO_DATA_CODES == WOMAN_NO_DATA_CODES == DISABLED_NO_DATA_CODES
        assert STARTUP_NO_DATA_CODES is not WOMAN_NO_DATA_CODES
        assert WOMAN_NO_DATA_CODES is not DISABLED_NO_DATA_CODES
        assert STARTUP_NO_DATA_CODES is not DISABLED_NO_DATA_CODES

    def test_the_mapping_covers_only_confirmed_sources(self) -> None:
        """호출부 표에는 **실호출로 확인된 출처만** 들어 있다."""
        assert set(SMPP_CERT_NO_DATA_CODES) == {SOURCE_WOMAN, SOURCE_DISABLED}
        assert SMPP_CERT_NO_DATA_CODES[SOURCE_WOMAN] is WOMAN_NO_DATA_CODES
        assert SMPP_CERT_NO_DATA_CODES[SOURCE_DISABLED] is DISABLED_NO_DATA_CODES
