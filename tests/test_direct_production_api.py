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
from xml.etree import ElementTree

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
from procurement.collectors.errors import ApiAuthError, ApiQuotaError, ApiTransportError
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
from procurement.collectors.transport import HttpResponse, UrllibTransport
from procurement.core.config import settings

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


# ---------------------------------------------------------------------------
# 실제 API 호출 — 기본은 건너뜀
# ---------------------------------------------------------------------------

_REAL_KEY = (settings.SMPP_API_KEY or "").strip()
_REAL_BUSINESS_NO = (settings.SMPP_TEST_BUSINESS_NO or "").strip()

#: 실호출에서 확인하려는 항목 필드 (활용가이드 "c) 응답 메시지 명세").
SPEC_ITEM_FIELDS = (
    "certSeCode",
    "validPdBeginDe",
    "validPdEndDe",
    "certfcDe",
    "detailPrdnmNo",
    "essntlPartclrMatter",
)

#: 페이지 관련 응답 필드.
SPEC_PAGE_FIELDS = ("totalCount", "numOfRows", "pageNo")


def _describe(xml_text: str) -> str:
    """응답의 **구조만** 요약합니다 — 값은 담지 않습니다.

    .. warning::
        ⛔ 인증키·사업자번호는 물론, 확인서의 실제 값(유효기간·품명번호 등)도
        담지 않습니다. 필드가 **있는지**, 항목이 **몇 개인지**만 봅니다. 이
        문자열은 실패 메시지로 화면에 그대로 찍힙니다.
    """
    root = ElementTree.fromstring(xml_text)

    code = next((n.text for n in root.iter("resultCode") if n.text), "(없음)")
    message = next((n.text for n in root.iter("resultMsg") if n.text), "(없음)")

    lines = [f"resultCode={code} / resultMsg={message}"]

    for tag in SPEC_PAGE_FIELDS:
        node = next((n for n in root.iter(tag)), None)
        # 페이지 정보는 값 자체가 구조 정보다 — 식별정보가 아니므로 그대로 본다.
        lines.append(f"{tag}={node.text if node is not None else '(없음)'}")

    items = list(root.iter("item"))
    lines.append(f"item 수={len(items)}")
    if items:
        present = [tag for tag in SPEC_ITEM_FIELDS if items[0].find(tag) is not None]
        missing = [tag for tag in SPEC_ITEM_FIELDS if items[0].find(tag) is None]
        lines.append(f"첫 항목에 있는 명세 필드={present}")
        lines.append(f"첫 항목에 없는 명세 필드={missing}")
        unexpected = sorted({child.tag for child in items[0]} - set(SPEC_ITEM_FIELDS))
        lines.append(f"명세에 없는 필드={unexpected}")
    return " | ".join(lines)


@pytest.mark.skipif(
    not (_REAL_KEY and _REAL_BUSINESS_NO),
    reason=(
        "실제 API 호출 시험은 SMPP_API_KEY 와 SMPP_TEST_BUSINESS_NO 가 "
        "둘 다 설정된 환경에서만 수행합니다(.env 또는 환경변수). "
        "값이 없으면 실패가 아니라 건너뜁니다."
    ),
)
class TestRealApiCall:
    """실제 엔드포인트 연결 및 응답 구조 확인.

    .. warning::
        인증키·사업자번호를 로그·실패 메시지에 출력하지 않습니다. 실패
        메시지에는 **결과코드·페이지 정보·필드 이름·항목 수**만 담깁니다 —
        그것이 이 시험에서 알아내려는 값이기 때문입니다.

    .. note::
        네트워크에 닿지 못하면 **실패가 아니라 skip** 입니다. "연결이 안 된다"
        와 "응답이 명세와 다르다" 는 전혀 다른 사실이므로 섞지 않습니다.
    """

    def _raw_body(self) -> str:
        """실제 응답 본문을 받아옵니다.

        구조(``totalCount`` 등)를 보려면 파서를 거치기 전의 원문이 필요합니다.
        요청 파라미터는 호출 계층과 **같은 이름·같은 형식**으로 만듭니다.
        """
        params = {
            "serviceKey": _REAL_KEY,
            "bsnmNo": _REAL_BUSINESS_NO,
            "stdrDate": TEST_STDR_DATE.strftime("%Y%m%d"),
            "pageNo": str(DEFAULT_PAGE_NO),
            "numOfRows": str(DEFAULT_NUM_OF_ROWS),
        }
        try:
            response = UrllibTransport().get(URL_DIRECT_PRODUCTION, params, timeout=15.0)
        except ApiTransportError as exc:
            pytest.skip(f"네트워크로 API 에 닿지 못했습니다: {type(exc).__name__}")
        if response.status != 200:
            pytest.fail(f"HTTP status={response.status} (본문은 출력하지 않습니다)")
        return response.body

    def test_the_response_structure_matches_the_spec(self) -> None:
        """실제 응답의 **구조**를 확인합니다.

        문서화되지 않은 결과코드이거나 명세와 다른 구조이면, 무엇이 달랐는지를
        드러내며 실패합니다. 조용히 넘기면 확인한 것이 없습니다.
        """
        body = self._raw_body()
        summary = _describe(body)

        root = ElementTree.fromstring(body)
        code = next((n.text for n in root.iter("resultCode") if n.text), None)

        if code == "00":
            items = list(root.iter("item"))
            required = [tag for tag in SPEC_ITEM_FIELDS if tag != "essntlPartclrMatter"]
            for index, item in enumerate(items):
                missing = [tag for tag in required if item.find(tag) is None]
                assert not missing, f"{index}번째 항목에 필수 필드 없음: {missing} | {summary}"
            assert items, f"resultCode 는 00 인데 항목이 없습니다 | {summary}"
        elif code == NO_DATA_CODE:
            pytest.skip(f"확인서 없음(03) — 정상 응답 구조는 확인하지 못했습니다 | {summary}")
        else:
            pytest.fail(
                "직접생산 API 가 현재 코드가 모르는 결과코드로 답했습니다. "
                f"{summary} — 이 값을 그대로 보고하세요. 코드를 임의로 바꾸지 않습니다."
            )

    def test_the_parser_reads_the_real_response(self) -> None:
        """실제 응답이 현재 파서를 통과합니다.

        ⛔ 확인서가 있든 없든 둘 다 정상입니다. "있으면 직접생산기업" 이라고
        하지 않으며, 유효기간을 어떤 날짜와도 비교하지 않습니다.
        """
        body = self._raw_body()
        summary = _describe(body)

        try:
            records = parse_direct_production_list(body, _REAL_BUSINESS_NO)
        except ApiResponseError as exc:
            pytest.fail(
                f"현재 코드가 모르는 결과코드입니다: resultCode={exc.code} "
                f"/ resultMsg={exc.message} | {summary}"
            )

        for record in records:
            # 물품 단위임을 실제 데이터로 확인한다 — 값은 출력하지 않는다.
            assert record.product_item_no
            assert record.valid_from <= record.valid_to
