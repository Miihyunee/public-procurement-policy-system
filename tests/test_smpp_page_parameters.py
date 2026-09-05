"""
tests.test_smpp_page_parameters

SMPP 계열 API 의 **필수 페이지 파라미터** 검증.

배경
====

2026-08-27 공식 활용가이드 2건을 확보해 확인한 결과, 네 상세기능 모두
``numOfRows`` · ``pageNo`` 를 **필수(항목구분 1)** 로 기재하고 있습니다.

======================================  =========================
상세기능                                  명세상 필수 파라미터
======================================  =========================
``smppCertInfo/getFnrssList``           ServiceKey · stdrDate · bsnmNo · numOfRows · pageNo
``smppCertInfo/getDspsnList``           〃
``smppCertInfo/getDPrductList``         〃 (+ detailPrdnmNo 선택)
``smppKiCertInfo/getKiCertInfo``        serviceKey · bsnmNo · numOfRows · pageNo
======================================  =========================

그런데 지금까지 코드는 **둘 다 보내지 않았습니다.** 실호출은 통했지만 명세와는
달랐고, 확인서가 여러 건인 기업에서 일부만 받을 위험이 있었습니다.

이 파일이 고정하는 사실
=======================

1. 네 API 모두 ``pageNo`` · ``numOfRows`` 를 **항상** 보낸다
2. 호출자가 값을 바꿀 수 있다
3. 명백히 잘못된 값(1 미만)은 **호출 전에** 막는다
4. ⛔ 페이지를 **자동으로 넘기지 않는다**

.. note::
    실제 API 서버에 접속하지 않습니다. 인증키는 더미, 사업자번호는 합성값입니다.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    DEFAULT_NUM_OF_ROWS,
    DEFAULT_PAGE_NO,
    SOURCE_DISABLED,
    SOURCE_STARTUP_SMPP,
    SOURCE_WOMAN,
    CertificationApiClient,
)
from procurement.collectors.transport import HttpResponse

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

STDR_DATE = date(2026, 8, 1)

CERT_OK = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<response><HeaderValueList><resultCode>00</resultCode>"
    "<resultMsg>성공</resultMsg></HeaderValueList>"
    "<body><items><item>"
    "<certSeCode>03</certSeCode><issuInstt>기관</issuInstt>"
    "<validPdBeginDe>20240401</validPdBeginDe><validPdEndDe>20270331</validPdEndDe>"
    "</item></items></body></response>"
)

STARTUP_OK = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<response><HeaderValueList><resultCode>00</resultCode>"
    "<resultMsg>NORMAL SERVICE</resultMsg></HeaderValueList>"
    "<body><items><item>"
    "<bsnmNo>1000000001</bsnmNo><entrpsNm>합성기업</entrpsNm>"
    "<validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>"
    "</item></items></body></response>"
)

DIRECT_OK = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<response><HeaderValueList><resultCode>00</resultCode>"
    "<resultMsg>성공</resultMsg></HeaderValueList>"
    "<body><items><item>"
    "<certSeCode>01</certSeCode>"
    "<validPdBeginDe>20240401</validPdBeginDe><validPdEndDe>20270331</validPdEndDe>"
    "<certfcDe>20240401</certfcDe><detailPrdnmNo>4015150301</detailPrdnmNo>"
    "</item></items></body></response>"
)


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역."""

    def __init__(self, body: str) -> None:
        """대역을 초기화합니다."""
        self._body = body
        self.calls: list[dict[str, str]] = []

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """요청 인자를 기록하고 준비된 본문을 돌려줍니다."""
        self.calls.append(dict(params))
        return HttpResponse(status=200, body=self._body)


def _client(body: str) -> tuple[CertificationApiClient, StubTransport]:
    """대역이 끼워진 호출기를 만듭니다."""
    transport = StubTransport(body)
    return (
        CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport),
        transport,
    )


def _call(source: str, **kwargs: int) -> dict[str, str]:
    """출처별로 한 번 호출하고 보낸 파라미터를 돌려줍니다."""
    if source == SOURCE_STARTUP_SMPP:
        client, transport = _client(STARTUP_OK)
        client.fetch(source, BUSINESS_NO, stdr_date=None, **kwargs)
    else:
        client, transport = _client(CERT_OK)
        client.fetch(source, BUSINESS_NO, stdr_date=STDR_DATE, **kwargs)
    return transport.calls[0]


ALL_CERT_SOURCES = [SOURCE_WOMAN, SOURCE_DISABLED, SOURCE_STARTUP_SMPP]


class TestPageParametersAreAlwaysSent:
    """네 API 모두 페이지 파라미터를 **항상** 보낸다."""

    @pytest.mark.parametrize("source", ALL_CERT_SOURCES)
    def test_page_no_is_sent(self, source: str) -> None:
        assert _call(source)["pageNo"] == str(DEFAULT_PAGE_NO)

    @pytest.mark.parametrize("source", ALL_CERT_SOURCES)
    def test_num_of_rows_is_sent(self, source: str) -> None:
        assert _call(source)["numOfRows"] == str(DEFAULT_NUM_OF_ROWS)

    def test_direct_production_sends_them_too(self) -> None:
        client, transport = _client(DIRECT_OK)

        client.fetch_direct_production(BUSINESS_NO, stdr_date=STDR_DATE)

        params = transport.calls[0]
        assert params["pageNo"] == str(DEFAULT_PAGE_NO)
        assert params["numOfRows"] == str(DEFAULT_NUM_OF_ROWS)

    def test_the_defaults_are_the_documented_shape(self) -> None:
        """기본값은 1쪽부터, 한 번에 여러 건."""
        assert DEFAULT_PAGE_NO == 1
        assert DEFAULT_NUM_OF_ROWS >= 1


class TestPageParametersCanBeChosen:
    """호출자가 값을 정할 수 있다."""

    @pytest.mark.parametrize("source", ALL_CERT_SOURCES)
    def test_values_are_passed_through(self, source: str) -> None:
        params = _call(source, page_no=2, num_of_rows=50)

        assert (params["pageNo"], params["numOfRows"]) == ("2", "50")

    def test_direct_production_accepts_them(self) -> None:
        client, transport = _client(DIRECT_OK)

        client.fetch_direct_production(BUSINESS_NO, stdr_date=STDR_DATE, page_no=4, num_of_rows=25)

        params = transport.calls[0]
        assert (params["pageNo"], params["numOfRows"]) == ("4", "25")


class TestInvalidPageParametersAreBlocked:
    """명백히 잘못된 값은 **호출 전에** 막는다.

    그대로 보내면 API 는 결과코드 ``07``(입력범위값 초과)로 답할 뿐이고,
    일일 호출 한도만 소모한다.
    """

    @pytest.mark.parametrize(("page_no", "num_of_rows"), [(0, 10), (-1, 10), (1, 0), (1, -5)])
    def test_values_below_one_are_rejected(self, page_no: int, num_of_rows: int) -> None:
        client, transport = _client(CERT_OK)

        with pytest.raises(ValueError):
            client.fetch(
                SOURCE_WOMAN,
                BUSINESS_NO,
                stdr_date=STDR_DATE,
                page_no=page_no,
                num_of_rows=num_of_rows,
            )
        # ⛔ 요청이 나가지 않았다
        assert transport.calls == []

    def test_direct_production_is_guarded_too(self) -> None:
        client, transport = _client(DIRECT_OK)

        with pytest.raises(ValueError):
            client.fetch_direct_production(BUSINESS_NO, stdr_date=STDR_DATE, page_no=0)
        assert transport.calls == []


class TestNoAutomaticPaging:
    """⛔ 페이지를 자동으로 넘기지 않는다.

    반복 조회가 필요한지는 응답의 ``totalCount`` 를 실제로 본 뒤 정한다. 지금
    자동 반복을 넣으면 근거 없이 호출 한도를 소모하고, 어디까지 받았는지도
    설명할 수 없다.
    """

    @pytest.mark.parametrize("source", ALL_CERT_SOURCES)
    def test_only_one_request_is_made(self, source: str) -> None:
        if source == SOURCE_STARTUP_SMPP:
            client, transport = _client(STARTUP_OK)
            client.fetch(source, BUSINESS_NO, stdr_date=None)
        else:
            client, transport = _client(CERT_OK)
            client.fetch(source, BUSINESS_NO, stdr_date=STDR_DATE)

        assert len(transport.calls) == 1

    def test_direct_production_makes_one_request(self) -> None:
        client, transport = _client(DIRECT_OK)

        client.fetch_direct_production(BUSINESS_NO, stdr_date=STDR_DATE)

        assert len(transport.calls) == 1

    def test_no_paging_loop_exists_in_the_client(self) -> None:
        """호출 계층에 "전체를 다 가져오는" 함수를 두지 않았다."""
        names = {name for name in dir(CertificationApiClient) if not name.startswith("_")}

        assert not {name for name in names if "all" in name.lower()}
        assert not {name for name in names if "page" in name.lower()}


class TestExistingCallersKeepWorking:
    """기존 호출 코드를 깨뜨리지 않았다."""

    @pytest.mark.parametrize("name", ["page_no", "num_of_rows"])
    def test_page_arguments_have_defaults(self, name: str) -> None:
        parameter = inspect.signature(CertificationApiClient.fetch).parameters[name]

        assert parameter.default is not inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_fetch_still_works_without_page_arguments(self) -> None:
        client, transport = _client(CERT_OK)

        result = client.fetch(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

        assert len(result.records) == 1
        assert transport.calls[0]["pageNo"] == str(DEFAULT_PAGE_NO)
