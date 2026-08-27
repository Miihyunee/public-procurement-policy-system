"""
tests.test_woman_result_code_90

여성기업 확인 API 의 **결과코드 ``90``** 처리 검증.

배경
====

2026-08-27 PM 로컬 실호출에서 ``smppCertInfo/getFnrssList`` 가 결과코드 ``90``
"매칭데이터가 존재하지 않습니다" 를 돌려주었습니다 — 창업기업과 **같은 코드,
같은 메시지**입니다. 이 코드는 **공식 활용가이드에 없습니다.**

PM 결정(2026-08-27)에 따라 여성기업 조회에서도 ``03`` 과 같은 **"정상 응답이지만
조회 결과 없음"** 으로 처리합니다.

이 파일이 고정하는 사실
=======================

1. 여성기업 ``90`` 은 오류가 아니라 **빈 결과**다
2. ⛔ ``00``(데이터 있음)으로 바뀐 것이 **아니다**
3. ⛔ **장애인기업에는 적용되지 않는다** — 실호출로 확인한 적이 없다
4. 인증 오류·한도 초과·그 밖의 모르는 코드 처리는 그대로다

.. note::
    실제 API 서버에 접속하지 않습니다. 인증키는 더미, 사업자번호는 합성값이며,
    실제 키·실제 사업자번호를 출력하지 않습니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    SOURCE_DISABLED,
    SOURCE_WOMAN,
    CertificationApiClient,
    FetchResult,
)
from procurement.collectors.errors import ApiAuthError, ApiQuotaError
from procurement.collectors.models import ApiResponseError
from procurement.collectors.smpp import (
    NO_DATA_CODE,
    SUCCESS_CODE,
    WOMAN_NO_DATA_CODES,
    parse_cert_list,
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
WOMAN_OK = _response(
    SUCCESS_CODE,
    "NORMAL SERVICE.",
    "<items><item>"
    "<certSeCode>03</certSeCode>"
    "<issuInstt>한국여성경제인협회</issuInstt>"
    "<validPdBeginDe>20240401</validPdBeginDe>"
    "<validPdEndDe>20270331</validPdEndDe>"
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


def _fetch_woman(body: str) -> tuple[FetchResult, StubTransport]:
    """여성기업 조회를 한 번 수행합니다."""
    client, transport = _client(body)
    return client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE), transport


# ---------------------------------------------------------------------------
# ① 결과코드 90 — 빈 결과
# ---------------------------------------------------------------------------


class TestCode90IsEmptyNotAnError:
    """여성기업 ``90`` 은 오류가 아니라 "조회 결과 없음" 이다."""

    def test_parser_returns_an_empty_list(self) -> None:
        assert parse_cert_list(CODE_90, BUSINESS_NO, no_data_codes=WOMAN_NO_DATA_CODES) == []

    def test_client_returns_an_empty_result_without_raising(self) -> None:
        result, _ = _fetch_woman(CODE_90)

        assert result.records == ()

    def test_the_call_still_reports_which_source_answered(self) -> None:
        result, _ = _fetch_woman(CODE_90)

        assert (result.source, result.policy_code) == (SOURCE_WOMAN, "WOMAN")

    def test_it_is_not_retried(self) -> None:
        """정상 응답이므로 다시 부르지 않는다."""
        _, transport = _fetch_woman(CODE_90)

        assert transport.calls == 1

    def test_no_certification_is_fabricated(self) -> None:
        """⛔ 데이터 없음이 "확인서 한 건" 으로 바뀌지 않는다."""
        result, _ = _fetch_woman(CODE_90)

        assert len(result.records) == 0

    def test_ninety_is_not_treated_as_the_success_code(self) -> None:
        """⛔ ``90`` 을 ``00`` 으로 바꾼 것이 아니다.

        ``00`` 은 항목을 **해석**하고, ``90`` 은 해석하지 않는다.
        """
        assert len(_fetch_woman(WOMAN_OK)[0].records) == 1
        assert _fetch_woman(CODE_90)[0].records == ()
        assert SUCCESS_CODE not in WOMAN_NO_DATA_CODES

    def test_leftover_items_are_not_parsed(self) -> None:
        """부스러기가 섞여 있어도 해석하지 않는다.

        해석했다면 ``validPdBeginDe`` 가 없어 파싱 오류가 났을 것이다.
        """
        result, _ = _fetch_woman(CODE_90_WITH_JUNK)

        assert result.records == ()


# ---------------------------------------------------------------------------
# ②③ 03 · 00 — 기존 동작 유지
# ---------------------------------------------------------------------------


class TestExistingCodesAreUnchanged:
    """``03`` 과 ``00`` 의 동작은 그대로다."""

    def test_code_03_still_returns_empty(self) -> None:
        result, _ = _fetch_woman(CODE_03)

        assert result.records == ()

    def test_code_00_still_parses_the_certificate(self) -> None:
        result, _ = _fetch_woman(WOMAN_OK)

        record = result.records[0]
        assert (record.valid_from.isoformat(), record.valid_to.isoformat()) == (
            "2024-04-01",
            "2027-03-31",
        )
        assert (record.cert_code, record.issuing_agency) == ("03", "한국여성경제인협회")

    def test_code_00_still_does_not_invent_company_fields(self) -> None:
        """이 API 는 기업명·대표자명을 주지 않는다 — 지어내지 않는다."""
        result, _ = _fetch_woman(WOMAN_OK)

        record = result.records[0]
        assert record.company_name is None
        assert record.representative_name is None


# ---------------------------------------------------------------------------
# ④⑤⑥ 오류 코드 — 기존 처리 유지
# ---------------------------------------------------------------------------


class TestErrorCodesAreUnchanged:
    """인증·한도 오류는 그대로 오류다."""

    @pytest.mark.parametrize("code", ["20", "30", "32"])
    def test_auth_codes_still_raise(self, code: str) -> None:
        client, transport = _client(_response(code, "AUTH"))

        with pytest.raises(ApiAuthError):
            client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)
        # ⛔ 인증 오류는 재시도하지 않는다 — 한도만 소모한다
        assert transport.calls == 1

    def test_quota_code_still_raises(self) -> None:
        client, _ = _client(_response("22", "LIMITED NUMBER OF SERVICE REQUESTS"))

        with pytest.raises(ApiQuotaError):
            client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    @pytest.mark.parametrize("code", ["01", "10", "31", "99"])
    def test_other_unknown_codes_are_still_not_guessed(self, code: str) -> None:
        """⛔ ``90`` 하나만 열었다 — 나머지 모르는 코드는 여전히 오류다."""
        client, _ = _client(_response(code, "무언가"))

        with pytest.raises(ApiResponseError):
            client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)


# ---------------------------------------------------------------------------
# ⑦ 장애인기업 회귀 — 넓히지 않았다
# ---------------------------------------------------------------------------


class TestDisabledIsUntouched:
    """⛔ 장애인기업에는 ``90`` 을 적용하지 않았다.

    여성기업과 응답 구조가 같고 **파서도 같지만**, 장애인기업은 실호출로 확인한
    적이 없다. 구조가 같다는 이유로 넓히면 그것은 확인이 아니라 추정이다.
    """

    def test_disabled_still_raises_on_90(self) -> None:
        client, _ = _client(CODE_90)

        with pytest.raises(ApiResponseError) as caught:
            client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)

        assert caught.value.code == "90"

    def test_the_shared_parser_default_stays_narrow(self) -> None:
        """공용 파서의 **기본값**은 명세에 있는 ``03`` 하나뿐이다.

        기본값을 넓혔다면 장애인기업도 함께 넓어졌을 것이다.
        """
        with pytest.raises(ApiResponseError):
            parse_cert_list(CODE_90, BUSINESS_NO)

    def test_disabled_still_returns_empty_on_03(self) -> None:
        client, _ = _client(CODE_03)

        result = client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)

        assert result.records == ()

    def test_disabled_still_parses_a_normal_response(self) -> None:
        client, _ = _client(WOMAN_OK)

        result = client.fetch(SOURCE_DISABLED, BUSINESS_NO, stdr_date=STDR_DATE)

        assert len(result.records) == 1


# ---------------------------------------------------------------------------
# 어느 출처가 무엇을 확인했는지
# ---------------------------------------------------------------------------


class TestWhatWasConfirmedPerSource:
    """확인된 것과 추정한 것을 구분해 둔다."""

    def test_woman_set_is_widened_and_the_documented_code_is_kept(self) -> None:
        assert WOMAN_NO_DATA_CODES == frozenset({"03", "90"})
        assert NO_DATA_CODE == "03"

    def test_startup_and_woman_are_separate_constants(self) -> None:
        """창업기업과 여성기업이 **각각** 확인되었다는 사실을 상수로 남긴다.

        하나로 합치면 "어느 API 에서 확인되었는지" 가 사라진다.
        """
        from procurement.collectors.smpp import STARTUP_NO_DATA_CODES

        assert STARTUP_NO_DATA_CODES == WOMAN_NO_DATA_CODES  # 값은 같지만
        assert STARTUP_NO_DATA_CODES is not WOMAN_NO_DATA_CODES  # 출처는 다르다
