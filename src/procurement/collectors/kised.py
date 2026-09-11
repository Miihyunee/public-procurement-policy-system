"""
procurement.collectors.kised

**창업진흥원 창업기업확인서발급기업정보 조회서비스**(``kisedCertService``)의
응답을 파싱합니다.

제공기관: 중소벤처기업부 / 창업진흥원 (공공데이터포털 ``B552735``).
근거: 서비스설계서 v1.0. 추측해서 채운 항목은 없습니다.

이 API 는 다른 API 와 달리 **JSON 과 XML 을 모두 제공**하며(기본
``returnType=JSON``), 기업명·대표자명·발급번호를 함께 줍니다.

.. note::
    ``getCorporateInformation`` 의 요청 파라미터 ``brno``(사업자등록번호)는
    **옵션**입니다. 즉 사업자번호로 한 건씩 조회할 수도, 전체 목록을 페이지
    단위로 받아올 수도 있습니다.

.. note::
    ``getProductInformation``(제품 정보)은 파싱하지 않습니다. 제품 단위 판정은
    현재 정책 범위에 없습니다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from xml.etree import ElementTree as ET

from procurement.collectors.dates import parse_day
from procurement.collectors.models import (
    ApiParseError,
    CertificationRecord,
    resolve_business_no,
)


def _clean(value: object) -> str | None:
    """값을 문자열로 정리합니다. 비어 있으면 ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _record_from_fields(fields: Mapping[str, object]) -> CertificationRecord:
    """필드 매핑 하나를 :class:`CertificationRecord` 로 변환합니다.

    Raises:
        ApiParseError: 필수 항목이 없거나 날짜 형식이 명세와 다른 경우.
    """
    raw_business_no = _clean(fields.get("brno"))
    if raw_business_no is None:
        raise ApiParseError("응답에 필수 항목이 없습니다: brno")
    business_no, original, warnings = resolve_business_no(raw_business_no)

    issued = _clean(fields.get("confmdoc_isu_dt"))
    expires = _clean(fields.get("confmdoc_expr_dt"))
    if issued is None:
        raise ApiParseError("응답에 필수 항목이 없습니다: confmdoc_isu_dt")
    if expires is None:
        raise ApiParseError("응답에 필수 항목이 없습니다: confmdoc_expr_dt")

    valid_from = parse_day(issued)
    valid_to = parse_day(expires)
    if valid_from > valid_to:
        raise ApiParseError(
            f"발급일이 만료일보다 늦습니다: {issued} > {expires} (brno={business_no})"
        )

    return CertificationRecord(
        business_no=business_no,
        business_no_original=original,
        business_no_warnings=warnings,
        valid_from=valid_from,
        valid_to=valid_to,
        certificate_number=_clean(fields.get("confmdoc_isu_no")),
        company_name=_clean(fields.get("ntrp_nm")),
        representative_name=_clean(fields.get("repr_nm")),
    )


def parse_corporate_information_json(payload: str) -> list[CertificationRecord]:
    """``getCorporateInformation`` 의 JSON 응답을 파싱합니다.

    응답 본문에서 ``item`` 목록을 찾아 변환합니다. 감싸는 키 이름이 응답에 따라
    다를 수 있으므로, ``data`` · ``items`` · ``item`` 순으로 탐색하고 최상위가
    바로 배열인 경우도 처리합니다.

    Args:
        payload: 응답 JSON 문자열.

    Returns:
        :class:`CertificationRecord` 목록. 항목이 없으면 빈 목록.

    Raises:
        ApiParseError: JSON 으로 해석할 수 없거나 필수 항목이 없는 경우.
    """
    try:
        parsed: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiParseError(f"응답을 JSON 으로 해석할 수 없습니다: {exc}") from exc

    rows = _find_rows(parsed)
    return [_record_from_fields(row) for row in rows]


def parse_corporate_information_xml(payload: str) -> list[CertificationRecord]:
    """``getCorporateInformation`` 의 XML 응답을 파싱합니다.

    Args:
        payload: 응답 XML 문자열.

    Returns:
        :class:`CertificationRecord` 목록. 항목이 없으면 빈 목록.

    Raises:
        ApiParseError: XML 로 해석할 수 없거나 필수 항목이 없는 경우.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ApiParseError(f"응답을 XML 로 해석할 수 없습니다: {exc}") from exc

    records: list[CertificationRecord] = []
    for item in root.iter("item"):
        fields = {child.tag: (child.text or "").strip() for child in item if child.tag is not None}
        records.append(_record_from_fields(fields))
    return records


def _find_rows(parsed: object) -> list[Mapping[str, object]]:
    """JSON 구조에서 항목 목록을 찾아냅니다."""
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, Mapping)]

    if not isinstance(parsed, Mapping):
        raise ApiParseError("응답 최상위가 객체나 배열이 아닙니다.")

    node: Any = parsed
    for _ in range(5):  # 중첩 깊이 방어
        if isinstance(node, list):
            return [row for row in node if isinstance(row, Mapping)]
        if not isinstance(node, Mapping):
            break
        for key in ("data", "items", "item", "body", "response"):
            if key in node:
                node = node[key]
                break
        else:
            break

    if isinstance(node, list):
        return [row for row in node if isinstance(row, Mapping)]
    if isinstance(node, Mapping) and "brno" in node:
        return [node]
    return []
