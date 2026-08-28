"""
tests.test_direct_production_api

**직접생산확인증명 API**(``smppCertInfo/getDPrductList``) 조회 경로 검증.

이 파일이 고정하는 사실
=======================

1. 요청이 공식 활용가이드대로 만들어진다 (페이지 파라미터 포함)
2. 응답이 **물품(세부품명번호) 단위**로 해석된다
3. ⛔ 업체 단위 인증(:class:`CertificationRecord`)과 **다른 타입**이다
4. ⛔ 판정하지 않는다 — 유효/무효도, "직접생산기업" 도 만들지 않는다
5. ⛔ 결과코드 ``90`` 은 **아직 데이터 없음이 아니다** (이 API 는 실호출 미확인)

.. note::
    응답 형태는 공식 활용가이드의 "c) 응답 메시지 명세" 와 XML 샘플을 따랐습니다.
    **실제 ``00`` 응답을 받아본 적은 없습니다.**

.. note::
    실제 API 서버에 접속하지 않습니다. 인증키는 더미, 사업자번호는 합성값입니다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    DEFAULT_NUM_OF_ROWS,
    DEFAULT_PAGE_NO,
    SOURCE_DIRECT_PRODUCTION,
    SOURCE_POLICY_CODES,
    URL_DIRECT_PRODUCTION,
    CertificationApiClient,
    DirectProductionResult,
)
from procurement.collectors.errors import ApiAuthError, ApiQuotaError
from procurement.collectors.models import (
    ApiParseError,
    ApiResponseError,
    CertificationRecord,
    DirectProductionRecord,
)
from procurement.collectors.smpp import (
    DIRECT_PRODUCTION_NO_DATA_CODES,
    NO_DATA_CODE,
    parse_direct_production_list,
)
from procurement.collectors.transport import HttpResponse

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

#: 시험용 기준일자. ⛔ 업무 기준일을 여기서 정하는 것이 아닙니다.
TEST_STDR_DATE = date(2026, 8, 1)


def _response(code: str, message: str, body: str = "") -> str:
    """결과코드를 담은 응답을 만듭니다."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<response><HeaderValueList><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></HeaderValueList>"
        f"<body>{body}</body></response>"
    )


def _item(
    *,
    cert_code: str = "01",
    begin: str = "20240401",
    end: str = "20270331",
    certified: str = "20240401",
    item_no: str = "4015150301",
    note: str | None = None,
) -> str:
    """활용가이드 샘플 구조를 따르는 항목 하나."""
    extra = f"<essntlPartclrMatter>{note}</essntlPartclrMatter>" if note else ""
    return (
        "<item>"
        f"<certSeCode>{cert_code}</certSeCode>"
        f"<validPdBeginDe>{begin}</validPdBeginDe>"
        f"<validPdEndDe>{end}</validPdEndDe>"
        f"<certfcDe>{certified}</certfcDe>"
        f"<detailPrdnmNo>{item_no}</detailPrdnmNo>"
        f"{extra}"
        "</item>"
    )


OK_ONE = _response("00", "성공", f"<items>{_item()}</items>")

#: 한 업체가 물품 세 가지에 대해 확인서를 가진 경우.
OK_THREE = _response(
    "00",
    "성공",
    "<items>"
    + _item(item_no="4015150301")
    + _item(item_no="8111189901", note="토출구경 200mm미만")
    + _item(item_no="4520159901")
    + "</items>",
)

NO_DATA = _response(NO_DATA_CODE, "데이터 없음")

#: 다른 세 API 에서 확인된 코드. **이 API 에서는 확인된 바가 없다.**
CODE_90 = _response("90", "매칭데이터가 존재하지 않습니다.")


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


def _fetch(body: str = OK_ONE, **kwargs: int) -> tuple[DirectProductionResult, StubTransport]:
    """직접생산 조회를 한 번 수행합니다."""
    transport = StubTransport(body)
    client = CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport)
    result = client.fetch_direct_production(BUSINESS_NO, stdr_date=TEST_STDR_DATE, **kwargs)
    return result, transport


# ---------------------------------------------------------------------------
# ①~⑦ 요청
# ---------------------------------------------------------------------------


class TestRequest:
    """요청이 공식 활용가이드대로 만들어진다."""

    def test_endpoint_is_the_spec_url(self) -> None:
        _, transport = _fetch()

        url, _ = transport.calls[0]
        assert url == URL_DIRECT_PRODUCTION
        assert url.endswith("/B550598/smppCertInfo/getDPrductList")

    def test_service_key_is_sent(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["serviceKey"] == "test-smpp-key"

    def test_business_no_is_sent(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["bsnmNo"] == BUSINESS_NO

    def test_stdr_date_is_sent_in_the_spec_format(self) -> None:
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["stdrDate"] == "20260801"

    def test_page_parameters_are_sent(self) -> None:
        """명세상 **필수**인 ``pageNo`` · ``numOfRows`` 를 보낸다."""
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert params["pageNo"] == str(DEFAULT_PAGE_NO)
        assert params["numOfRows"] == str(DEFAULT_NUM_OF_ROWS)

    def test_page_parameters_can_be_overridden(self) -> None:
        _, transport = _fetch(page_no=3, num_of_rows=7)

        _, params = transport.calls[0]
        assert (params["pageNo"], params["numOfRows"]) == ("3", "7")

    def test_only_the_documented_parameters_are_sent(self) -> None:
        """명세에 없는 파라미터를 지어내지 않는다.

        ``detailPrdnmNo``(세부품명번호)는 명세상 **선택** 항목이며, 어떤 품목으로
        좁혀 물어볼지는 업무 결정 사항이라 보내지 않는다.
        """
        _, transport = _fetch()

        _, params = transport.calls[0]
        assert set(params) == {"serviceKey", "bsnmNo", "stdrDate", "pageNo", "numOfRows"}

    def test_the_caller_must_supply_the_reference_date(self) -> None:
        """⛔ 기준일자에 기본값을 두지 않았다."""
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key", transport=StubTransport(OK_ONE)
        )

        with pytest.raises(TypeError):
            client.fetch_direct_production(BUSINESS_NO)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ⑨ 정상 응답 해석
# ---------------------------------------------------------------------------


class TestSuccessfulResponse:
    """``00`` 응답이 물품 단위로 해석된다."""

    def test_one_item_is_parsed(self) -> None:
        result, _ = _fetch()

        assert len(result.records) == 1

    def test_spec_fields_are_preserved(self) -> None:
        result, _ = _fetch()

        record = result.records[0]
        assert record.cert_code == "01"
        assert record.valid_from.isoformat() == "2024-04-01"
        assert record.valid_to.isoformat() == "2027-03-31"
        assert record.certified_date.isoformat() == "2024-04-01"
        assert record.product_item_no == "4015150301"

    def test_business_no_comes_from_the_request(self) -> None:
        """응답에 사업자번호가 없으므로 요청값을 정규화해 담는다."""
        result, _ = _fetch()

        assert result.records[0].business_no == BUSINESS_NO

    def test_optional_note_is_none_when_absent(self) -> None:
        """``essntlPartclrMatter`` 는 선택 항목이다 — 없으면 ``None``."""
        result, _ = _fetch()

        assert result.records[0].required_special_note is None

    def test_optional_note_is_kept_as_is(self) -> None:
        """⛔ 내용을 해석하지 않고 문자열 그대로 보존한다."""
        result, _ = _fetch(OK_THREE)

        notes = [record.required_special_note for record in result.records]
        assert "토출구경 200mm미만" in notes

    def test_source_is_reported(self) -> None:
        result, _ = _fetch()

        assert result.source == SOURCE_DIRECT_PRODUCTION


# ---------------------------------------------------------------------------
# 물품 단위 — 이 API 의 핵심 성질
# ---------------------------------------------------------------------------


class TestItLooksAtProductsNotCompanies:
    """⛔ 업체 단위 인증으로 뭉개지 않는다."""

    def test_every_product_item_is_kept(self) -> None:
        """세 물품이면 세 건이다 — 하나로 합치지 않는다."""
        result, _ = _fetch(OK_THREE)

        assert len(result.records) == 3

    def test_product_item_numbers_are_not_dropped(self) -> None:
        result, _ = _fetch(OK_THREE)

        assert [record.product_item_no for record in result.records] == [
            "4015150301",
            "8111189901",
            "4520159901",
        ]

    def test_the_record_type_is_not_the_company_certification_type(self) -> None:
        """타입이 달라서 업체 인증과 섞일 수 없다."""
        result, _ = _fetch()

        assert isinstance(result.records[0], DirectProductionRecord)
        assert not isinstance(result.records[0], CertificationRecord)

    def test_the_result_carries_no_policy_code(self) -> None:
        """⛔ 정책 코드를 붙이지 않았다.

        붙이는 순간 "이 업체는 그 정책에 해당한다" 는 뜻이 된다. 직접생산확인을
        어느 정책에 대응시킬지는 **확인된 바가 없다.**
        """
        result, _ = _fetch()

        assert not hasattr(result, "policy_code")
        assert SOURCE_DIRECT_PRODUCTION not in SOURCE_POLICY_CODES


# ---------------------------------------------------------------------------
# ④ 판정하지 않는다
# ---------------------------------------------------------------------------

FORBIDDEN_NAMES = (
    "is_valid",
    "valid",
    "certified",
    "is_direct_production",
    "confidence",
    "score",
)


class TestNoVerdictIsProduced:
    """⛔ 유효/무효를 담을 자리가 없다 — 타입 수준에서 확인한다."""

    def test_record_has_no_verdict_field(self) -> None:
        names = {field.name for field in dataclasses.fields(DirectProductionRecord)}

        assert names.isdisjoint(FORBIDDEN_NAMES)

    def test_result_has_no_verdict_field(self) -> None:
        names = {field.name for field in dataclasses.fields(DirectProductionResult)}

        assert names.isdisjoint(FORBIDDEN_NAMES)

    def test_dates_are_read_but_not_compared(self) -> None:
        """유효기간과 인증일을 **읽기만** 한다.

        어느 날짜와 비교할지는 확정되지 않았다.
        """
        result, _ = _fetch()

        record = result.records[0]
        assert record.valid_from < record.valid_to
        for name in FORBIDDEN_NAMES:
            assert not hasattr(record, name)


# ---------------------------------------------------------------------------
# ⑧⑩⑪⑫ 결과코드와 오류
# ---------------------------------------------------------------------------


class TestResultCodes:
    """결과코드가 명세대로 구분된다."""

    def test_no_data_returns_empty(self) -> None:
        result, _ = _fetch(NO_DATA)

        assert result.records == ()

    def test_ninety_is_not_treated_as_no_data_here(self) -> None:
        """⛔ 다른 API 에서 확인되었다는 이유로 넓히지 않았다.

        이 API 에서 ``90`` 이 오는지는 **실호출로 확인한 적이 없다.** 오면
        오류로 드러나며, 그것이 곧 "확인이 필요하다" 는 신호다.
        """
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key", transport=StubTransport(CODE_90)
        )

        with pytest.raises(ApiResponseError) as caught:
            client.fetch_direct_production(BUSINESS_NO, stdr_date=TEST_STDR_DATE)

        assert caught.value.code == "90"

    def test_the_no_data_set_holds_only_the_documented_code(self) -> None:
        assert DIRECT_PRODUCTION_NO_DATA_CODES == frozenset({NO_DATA_CODE})

    @pytest.mark.parametrize("code", ["20", "30", "32"])
    def test_auth_codes_raise(self, code: str) -> None:
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key",
            transport=StubTransport(_response(code, "AUTH")),
        )

        with pytest.raises(ApiAuthError):
            client.fetch_direct_production(BUSINESS_NO, stdr_date=TEST_STDR_DATE)

    def test_quota_code_raises(self) -> None:
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key",
            transport=StubTransport(_response("22", "LIMITED")),
        )

        with pytest.raises(ApiQuotaError):
            client.fetch_direct_production(BUSINESS_NO, stdr_date=TEST_STDR_DATE)

    @pytest.mark.parametrize("code", ["01", "10", "31", "99"])
    def test_unknown_codes_are_not_guessed(self, code: str) -> None:
        client = CertificationApiClient(
            smpp_api_key="test-smpp-key",
            transport=StubTransport(_response(code, "무언가")),
        )

        with pytest.raises(ApiResponseError):
            client.fetch_direct_production(BUSINESS_NO, stdr_date=TEST_STDR_DATE)


# ---------------------------------------------------------------------------
# ⑩ 필드 누락
# ---------------------------------------------------------------------------


class TestMissingFields:
    """명세상 필수 항목이 없으면 빈 값으로 넘기지 않고 실패시킨다."""

    @pytest.mark.parametrize(
        "tag",
        ["certSeCode", "validPdBeginDe", "validPdEndDe", "certfcDe", "detailPrdnmNo"],
    )
    def test_missing_required_field_raises(self, tag: str) -> None:
        full = _item()
        broken = full.replace(f"<{tag}>", "<removed>").replace(f"</{tag}>", "</removed>")
        body = _response("00", "성공", f"<items>{broken}</items>")

        with pytest.raises(ApiParseError):
            parse_direct_production_list(body, BUSINESS_NO)

    def test_broken_xml_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_direct_production_list("<response><unclosed>", BUSINESS_NO)
