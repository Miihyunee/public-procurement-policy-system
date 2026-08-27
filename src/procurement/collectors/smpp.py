"""
procurement.collectors.smpp

**공공구매종합정보망(SMPP)** 계열 인증 API 의 XML 응답을 파싱합니다.

제공기관: 주식회사 한국중소벤처기업유통원 (공공데이터포털 ``B550598``).
아래 내용은 각 API 의 "공공데이터 오픈API 활용가이드" 명세서를 근거로 하며,
추측해서 채운 항목은 없습니다. **한 가지 예외**는 결과코드 ``90`` 으로, 명세에는
없고 **실호출로 확인**했습니다(:data:`STARTUP_NO_DATA_CODES` 참조).

=========================  =====================================  ==================
서비스                      상세기능 URL                            대응 정책
=========================  =====================================  ==================
``smppCertInfo``           ``/getFnrssList`` 여성기업확인          ``WOMAN``
``smppCertInfo``           ``/getDspsnList`` 장애인기업확인        ``DISABLED``
``smppKiCertInfo``         ``/getKiCertInfo`` 창업기업확인서       ``STARTUP``
=========================  =====================================  ==================

.. warning::
    **여성기업·장애인기업 확인 응답에는 기업명·대표자명이 없습니다.**
    사업자번호도 응답에 없어, 요청에 사용한 값을 정규화해 넣습니다.
    ``Company`` 저장에 필요한 값을 어디서 채울지는 별도 결정 사항입니다.

.. note::
    이 모듈은 **파싱만** 합니다. HTTP 호출은 포함하지 않습니다. 네트워크 계층을
    분리해 두면 실제 응답 샘플만으로 검증할 수 있습니다.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from procurement.collectors.dates import parse_day, parse_range
from procurement.collectors.models import (
    ApiParseError,
    ApiResponseError,
    CertificationRecord,
    resolve_business_no,
)

#: 정상 응답 코드
SUCCESS_CODE = "00"

#: 데이터 없음 — 오류가 아니라 "해당 사업자번호로 유효한 확인서가 없음"
NO_DATA_CODE = "03"

#: 창업기업확인서 조회에서 **실호출로 확인된** "매칭데이터 없음" 코드.
#:
#: .. warning::
#:     ⚠️ **공식 활용가이드에 없는 코드입니다.**
#:
#:     2026-08-27 실호출에서 ``90`` "매칭데이터가 존재하지 않습니다" 가
#:     돌아왔습니다. 명세서 어디에도 기재되어 있지 않아 코드는 이를 "모르는
#:     코드" 로 보고 오류를 냈고, 그 결과 **확인서가 없는 기업을 조회할 때마다
#:     오류가 나 대량 조회가 불가능**했습니다.
#:
#:     PM 결정(2026-08-27)에 따라 ``03`` 과 같은 "정상 응답이지만 조회 결과
#:     없음" 으로 처리합니다. ⛔ ``00``(데이터 있음)으로 바꾸는 것이 아닙니다 —
#:     빈 목록을 돌려줄 뿐 확인서를 만들지 않습니다.
#:
#: .. warning::
#:     ⛔ **창업기업 조회에만 적용합니다.** 여성기업·장애인기업 응답에서 같은
#:     숫자가 어떤 뜻인지는 확인된 바가 없습니다. 확인하지 않은 API 까지
#:     넓히면 진짜 오류를 조용히 삼키게 됩니다.
STARTUP_NO_DATA_CODES: frozenset[str] = frozenset({NO_DATA_CODE, "90"})

#: 확인서 구분 코드 (명세서 기재값)
CERT_CODE_DIRECT_PRODUCTION = "01"
CERT_CODE_WOMAN = "03"
CERT_CODE_DISABLED = "04"


def _text(node: ET.Element, tag: str) -> str | None:
    """자식 노드의 텍스트를 반환합니다. 없거나 비어 있으면 ``None``."""
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _require(node: ET.Element, tag: str) -> str:
    """필수 자식 노드의 텍스트를 반환합니다.

    Raises:
        ApiParseError: 값이 없는 경우.
    """
    value = _text(node, tag)
    if value is None:
        raise ApiParseError(f"응답에 필수 항목이 없습니다: {tag}")
    return value


def _check_result(
    root: ET.Element,
    no_data_codes: frozenset[str] = frozenset({NO_DATA_CODE}),
) -> bool:
    """결과 코드를 확인합니다.

    Args:
        root: 응답 XML 루트.
        no_data_codes: "데이터 없음" 으로 볼 결과코드. **기본값은 명세에 기재된
            ``03`` 하나뿐**이며, 호출하는 쪽이 명시적으로 넓힐 때만 넓어집니다.
            어느 API 에서 무엇이 확인되었는지를 호출부에 드러내기 위한 것입니다.

    Returns:
        데이터를 파싱해야 하면 ``True``, "데이터 없음"이면 ``False``.

    Raises:
        ApiResponseError: 정상·데이터없음 이외의 코드인 경우.
    """
    code = None
    message = None
    for node in root.iter():
        if node.tag == "resultCode" and node.text:
            code = node.text.strip()
        elif node.tag == "resultMsg" and node.text:
            message = node.text.strip()

    if code is None:
        raise ApiParseError("응답에 resultCode 가 없습니다.")
    if code == SUCCESS_CODE:
        return True
    if code in no_data_codes:
        return False
    raise ApiResponseError(code, message or "")


def _items(root: ET.Element) -> list[ET.Element]:
    """``<items><item>...`` 목록을 반환합니다."""
    return list(root.iter("item"))


def parse_cert_list(xml_text: str, business_no: str) -> list[CertificationRecord]:
    """``smppCertInfo`` 계열 응답을 파싱합니다 (여성기업·장애인기업 공통).

    두 상세기능(``getFnrssList`` · ``getDspsnList``)의 응답 구조가 동일하므로
    같은 파서를 사용합니다. 어느 정책인지는 ``certSeCode`` 로 구분됩니다
    (여성 ``03`` / 장애인 ``04``).

    응답에는 사업자번호가 없으므로, **요청에 사용한 값**을 정규화해 담습니다.

    Args:
        xml_text: 응답 XML 문자열.
        business_no: 요청에 사용한 사업자등록번호.

    Returns:
        :class:`CertificationRecord` 목록. 유효한 확인서가 없으면 빈 목록.

    Raises:
        ApiResponseError: API 가 오류 코드를 반환한 경우.
        ApiParseError: 응답 구조가 명세와 다른 경우.
    """
    root = _parse_xml(xml_text)
    if not _check_result(root):
        return []

    normalized, original, warnings = resolve_business_no(business_no)

    records: list[CertificationRecord] = []
    for item in _items(root):
        records.append(
            CertificationRecord(
                business_no=normalized,
                business_no_original=original,
                business_no_warnings=warnings,
                valid_from=parse_day(_require(item, "validPdBeginDe")),
                valid_to=parse_day(_require(item, "validPdEndDe")),
                cert_code=_text(item, "certSeCode"),
                issuing_agency=_text(item, "issuInstt"),
                # 이 API 는 기업명·대표자명을 제공하지 않는다(명세 확인).
                company_name=None,
                representative_name=None,
            )
        )
    return records


def parse_startup_cert(xml_text: str) -> list[CertificationRecord]:
    """``smppKiCertInfo/getKiCertInfo`` (창업기업확인서) 응답을 파싱합니다.

    이 API 는 기업명·대표자명·주소를 함께 제공하며, 유효기간은
    ``2022.04.07 ~ 2025.04.06`` 형태의 **범위 문자열** 하나로 옵니다.

    ``earlyValidPdDe``(초기창업자기간)는 별도 기간이며, 창업기업 판정에 어느
    기간을 쓸지는 확정되지 않았으므로 **사용하지 않습니다**.

    .. note::
        이 API 만 :data:`STARTUP_NO_DATA_CODES` 를 씁니다 — 명세에 있는 ``03``
        과, 실호출로 확인된 ``90``("매칭데이터가 존재하지 않습니다")을 함께
        "조회 결과 없음" 으로 봅니다. 둘 다 **빈 목록**을 돌려주며, 확인서를
        만들지 않습니다.

    Args:
        xml_text: 응답 XML 문자열.

    Returns:
        :class:`CertificationRecord` 목록. 없으면 빈 목록.

    Raises:
        ApiResponseError: API 가 오류 코드를 반환한 경우.
        ApiParseError: 응답 구조가 명세와 다른 경우.
    """
    root = _parse_xml(xml_text)
    # ⛔ 데이터 없음이면 **여기서 끝난다.** 아래 항목 순회로 내려가지 않으므로,
    #    응답에 어중간한 항목이 섞여 있어도 해석을 시도하지 않는다.
    if not _check_result(root, STARTUP_NO_DATA_CODES):
        return []

    records: list[CertificationRecord] = []
    for item in _items(root):
        valid_from, valid_to = parse_range(_require(item, "validPdDe"))
        normalized, original, warnings = resolve_business_no(_require(item, "bsnmNo"))
        records.append(
            CertificationRecord(
                business_no=normalized,
                business_no_original=original,
                business_no_warnings=warnings,
                valid_from=valid_from,
                valid_to=valid_to,
                company_name=_text(item, "entrpsNm"),
                representative_name=_text(item, "rprsntvNm"),
                address=_text(item, "adres"),
            )
        )
    return records


def _parse_xml(xml_text: str) -> ET.Element:
    """XML 문자열을 파싱합니다.

    Raises:
        ApiParseError: XML 로 해석할 수 없는 경우.
    """
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ApiParseError(f"응답을 XML 로 해석할 수 없습니다: {exc}") from exc
