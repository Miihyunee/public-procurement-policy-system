"""
외부 인증 API 응답 파서 테스트.

응답 예제는 각 API **공식 명세서에 실린 샘플 그대로**입니다.

- 공공구매종합정보망 인증서 정보 제공 서비스 (``smppCertInfo``) — 여성·장애인
- 공공구매정보망 창업기업 확인서 정보 제공 서비스 (``smppKiCertInfo``)
- 창업진흥원 창업기업확인서발급기업정보 조회서비스 (``kisedCertService``)
"""

from __future__ import annotations

from datetime import date

import pytest

from procurement.collectors import (
    ApiParseError,
    ApiResponseError,
    parse_cert_list,
    parse_corporate_information_json,
    parse_corporate_information_xml,
    parse_day,
    parse_range,
    parse_startup_cert,
)

# --- 명세서 샘플 응답 ---------------------------------------------------------

WOMAN_XML = """<response>
 <HeaderValueList><resultCode>00</resultCode><resultMsg>성공</resultMsg></HeaderValueList>
 <body><items><item>
   <certSeCode>03</certSeCode>
   <issuInstt>서울지방중소벤처기업청</issuInstt>
   <validPdBeginDe>20180208</validPdBeginDe>
   <validPdEndDe>20200207</validPdEndDe>
   <certfcDe>20180208</certfcDe>
 </item></items></body>
 <numOfRows>10</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount>
</response>"""

DISABLED_XML = """<response>
 <HeaderValueList><resultCode>00</resultCode><resultMsg>성공</resultMsg></HeaderValueList>
 <body><items><item>
   <certSeCode>04</certSeCode>
   <issuInstt>경남지방중소벤처기업청</issuInstt>
   <validPdBeginDe>20180208</validPdBeginDe>
   <validPdEndDe>20200207</validPdEndDe>
   <certfcDe>20180208</certfcDe>
 </item></items></body>
 <numOfRows>10</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount>
</response>"""

NO_DATA_XML = """<response>
 <HeaderValueList><resultCode>03</resultCode><resultMsg>데이터 없음</resultMsg></HeaderValueList>
 <body><items/></body>
</response>"""

DENIED_XML = """<response>
 <HeaderValueList>
   <resultCode>20</resultCode><resultMsg>서비스 접근 거부</resultMsg>
 </HeaderValueList>
</response>"""

STARTUP_SMPP_XML = """<response>
 <HeaderValueList><resultCode>00</resultCode><resultMsg>NORMAL SERVICE</resultMsg></HeaderValueList>
 <body><items><item>
   <entrpsNm>중소기업유통센터</entrpsNm>
   <bsnmNo>1078153660</bsnmNo>
   <minduty>서비스업</minduty>
   <rprsntvNm>정진수</rprsntvNm>
   <adres>서울특별시 목동동로 309 중소기업유통센터</adres>
   <validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>
   <earlyValidPdDe>2020.04.07 ~ 2023.04.06</earlyValidPdDe>
 </item></items></body>
</response>"""

KISED_XML = """<items><item>
  <confmdoc_isu_no>202101259240000195</confmdoc_isu_no>
  <ntrp_type_nm>개인기업</ntrp_type_nm>
  <ntrp_nm>대성테크</ntrp_nm>
  <brno>4674300461</brno>
  <repr_nm>안재득 외2명</repr_nm>
  <unin_repr_nm>오완식, 조삼식</unin_repr_nm>
  <crno></crno>
  <confmdoc_isu_dt>2021-01-26</confmdoc_isu_dt>
  <confmdoc_expr_dt>2024-01-26</confmdoc_expr_dt>
</item></items>"""

KISED_JSON = """{"data": [{
  "confmdoc_isu_no": "202310284220030253",
  "ntrp_type_nm": "법인기업",
  "ntrp_nm": "유한회사 바름",
  "brno": "2428602983",
  "repr_nm": "홍길동",
  "crno": "",
  "confmdoc_isu_dt": "2023-10-06",
  "confmdoc_expr_dt": "2026-10-06"
}]}"""


class TestWomanAndDisabled:
    """``smppCertInfo`` — 여성기업 · 장애인기업 (응답 구조 동일)."""

    def test_parses_single_record(self) -> None:
        records = parse_cert_list(WOMAN_XML, business_no="4021497692")
        assert len(records) == 1

    def test_valid_period(self) -> None:
        record = parse_cert_list(WOMAN_XML, business_no="4021497692")[0]
        assert record.valid_from == date(2018, 2, 8)
        assert record.valid_to == date(2020, 2, 7)

    def test_business_no_comes_from_request(self) -> None:
        """응답에 사업자번호가 없으므로 요청값을 그대로 담는다."""
        record = parse_cert_list(WOMAN_XML, business_no="4021497692")[0]
        assert record.business_no == "4021497692"

    def test_issuing_agency(self) -> None:
        record = parse_cert_list(WOMAN_XML, business_no="4021497692")[0]
        assert record.issuing_agency == "서울지방중소벤처기업청"

    def test_woman_cert_code(self) -> None:
        assert parse_cert_list(WOMAN_XML, business_no="1")[0].cert_code == "03"

    def test_disabled_cert_code(self) -> None:
        assert parse_cert_list(DISABLED_XML, business_no="1")[0].cert_code == "04"

    def test_company_name_is_not_provided(self) -> None:
        """이 API 는 기업명을 제공하지 않는다 — 지어내지 않는다."""
        assert parse_cert_list(WOMAN_XML, business_no="1")[0].company_name is None

    def test_representative_name_is_not_provided(self) -> None:
        """이 API 는 대표자명을 제공하지 않는다."""
        assert parse_cert_list(WOMAN_XML, business_no="1")[0].representative_name is None

    def test_no_data_returns_empty(self) -> None:
        """결과코드 03(데이터 없음)은 오류가 아니라 '유효한 확인서 없음'."""
        assert parse_cert_list(NO_DATA_XML, business_no="1") == []

    def test_error_code_raises(self) -> None:
        with pytest.raises(ApiResponseError) as exc:
            parse_cert_list(DENIED_XML, business_no="1")
        assert exc.value.code == "20"

    def test_broken_xml_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_cert_list("<response>", business_no="1")

    def test_missing_required_field_raises(self) -> None:
        broken = WOMAN_XML.replace("<validPdEndDe>20200207</validPdEndDe>", "")
        with pytest.raises(ApiParseError):
            parse_cert_list(broken, business_no="1")


class TestStartupSmpp:
    """``smppKiCertInfo`` — 창업기업확인서 (유효기간이 범위 문자열)."""

    def test_parses_range_string(self) -> None:
        record = parse_startup_cert(STARTUP_SMPP_XML)[0]
        assert record.valid_from == date(2022, 4, 7)
        assert record.valid_to == date(2025, 4, 6)

    def test_provides_company_and_representative(self) -> None:
        record = parse_startup_cert(STARTUP_SMPP_XML)[0]
        assert record.company_name == "중소기업유통센터"
        assert record.representative_name == "정진수"

    def test_business_no_from_response(self) -> None:
        assert parse_startup_cert(STARTUP_SMPP_XML)[0].business_no == "1078153660"

    def test_address(self) -> None:
        record = parse_startup_cert(STARTUP_SMPP_XML)[0]
        assert record.address is not None
        assert "서울특별시" in record.address

    def test_early_period_is_ignored(self) -> None:
        """초기창업자기간은 판정 기준이 확정되지 않아 사용하지 않는다."""
        record = parse_startup_cert(STARTUP_SMPP_XML)[0]
        assert record.valid_from != date(2020, 4, 7)

    def test_no_data_returns_empty(self) -> None:
        assert parse_startup_cert(NO_DATA_XML) == []


class TestKised:
    """``kisedCertService`` — 창업진흥원."""

    def test_parses_xml(self) -> None:
        records = parse_corporate_information_xml(KISED_XML)
        assert len(records) == 1

    def test_xml_fields(self) -> None:
        record = parse_corporate_information_xml(KISED_XML)[0]
        assert record.business_no == "4674300461"
        assert record.company_name == "대성테크"
        assert record.representative_name == "안재득 외2명"
        assert record.certificate_number == "202101259240000195"

    def test_xml_dates(self) -> None:
        record = parse_corporate_information_xml(KISED_XML)[0]
        assert record.valid_from == date(2021, 1, 26)
        assert record.valid_to == date(2024, 1, 26)

    def test_parses_json(self) -> None:
        record = parse_corporate_information_json(KISED_JSON)[0]
        assert record.business_no == "2428602983"
        assert record.valid_to == date(2026, 10, 6)

    def test_empty_optional_field_becomes_none(self) -> None:
        """빈 문자열(``crno``)을 빈 값으로 저장하지 않는다."""
        record = parse_corporate_information_xml(KISED_XML)[0]
        assert record.certificate_number == "202101259240000195"

    def test_missing_business_no_raises(self) -> None:
        broken = KISED_JSON.replace('"brno": "2428602983",', "")
        with pytest.raises(ApiParseError):
            parse_corporate_information_json(broken)

    def test_reversed_dates_raise(self) -> None:
        """발급일이 만료일보다 늦으면 조용히 저장하지 않는다."""
        broken = KISED_JSON.replace(
            '"confmdoc_expr_dt": "2026-10-06"', '"confmdoc_expr_dt": "2020-01-01"'
        )
        with pytest.raises(ApiParseError):
            parse_corporate_information_json(broken)

    def test_broken_json_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_corporate_information_json("{")

    def test_empty_list_returns_empty(self) -> None:
        assert parse_corporate_information_json('{"data": []}') == []


class TestDateFormats:
    """명세서에 실린 4가지 날짜 형식."""

    def test_compact(self) -> None:
        assert parse_day("20180208") == date(2018, 2, 8)

    def test_hyphen(self) -> None:
        assert parse_day("2021-01-26") == date(2021, 1, 26)

    def test_dot(self) -> None:
        assert parse_day("2022.04.07") == date(2022, 4, 7)

    def test_range_with_tilde(self) -> None:
        assert parse_range("2022.04.07 ~ 2025.04.06") == (date(2022, 4, 7), date(2025, 4, 6))

    def test_range_with_hyphen(self) -> None:
        """성능인증 ``validDe`` 형식 — ``20220408 - 20240407``."""
        assert parse_range("20220408 - 20240407") == (date(2022, 4, 8), date(2024, 4, 7))

    def test_range_compact_with_tilde(self) -> None:
        """상생협력 ``sportPd`` 형식 — ``20201012 ~ 20231011``."""
        assert parse_range("20201012 ~ 20231011") == (date(2020, 10, 12), date(2023, 10, 11))

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_day("2021년 1월 26일")

    def test_impossible_date_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_day("20210230")

    def test_empty_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_day("")

    def test_range_without_separator_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_range("20220408")

    def test_reversed_range_raises(self) -> None:
        with pytest.raises(ApiParseError):
            parse_range("20240407 ~ 20220408")
