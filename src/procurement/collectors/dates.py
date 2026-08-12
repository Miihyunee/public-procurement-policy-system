"""
procurement.collectors.dates

외부 인증 API 의 **날짜 표기를 정규화**합니다.

명세서에 기재된 형식이 API 마다 다릅니다. 아래는 각 API 명세서(공공데이터
오픈API 활용가이드)에 실린 샘플 데이터 그대로입니다.

=========================================  ==========================================
API / 필드                                  형식
=========================================  ==========================================
``smppCertInfo`` ``validPdBeginDe``         ``20180208`` (YYYYMMDD, 단일 일자)
``kisedCertService`` ``confmdoc_isu_dt``    ``2021-01-26`` (YYYY-MM-DD, 단일 일자)
``smppKiCertInfo`` ``validPdDe``            ``2022.04.07 ~ 2025.04.06`` (**범위 문자열**)
``smppPfCertInfo`` ``validDe``              ``20220408 - 20240407`` (**범위 문자열**)
``smppWnCertInfo`` ``sportPd``              ``20201012 ~ 20231011`` (**범위 문자열**)
=========================================  ==========================================

범위 문자열은 구분자(``~`` / ``-``)와 일자 형식이 섞여 있으므로, 두 조각으로
나눈 뒤 각각 단일 일자 규칙으로 해석합니다.

.. note::
    형식을 추측해서 넓게 받아들이지 않습니다. 명세에 없는 형태가 들어오면
    :class:`ApiParseError` 로 **실패시켜** 조용히 잘못된 날짜가 저장되는 일을
    막습니다.
"""

from __future__ import annotations

import re
from datetime import date

from procurement.collectors.models import ApiParseError

#: ``20180208`` — 구분자 없는 8자리
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

#: ``2021-01-26`` / ``2022.04.07`` / ``2022/04/07``
_DELIMITED = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$")

#: 범위 문자열 구분자 — ``~`` 또는 ``-``(공백으로 둘러싸인 경우만)
_RANGE_SPLIT = re.compile(r"\s*~\s*|\s+-\s+")


def parse_day(value: str) -> date:
    """단일 일자 문자열을 :class:`datetime.date` 로 변환합니다.

    Args:
        value: ``20180208`` 또는 ``2021-01-26`` / ``2022.04.07`` 형태.

    Returns:
        변환된 날짜.

    Raises:
        ApiParseError: 명세에 없는 형식이거나 존재하지 않는 날짜인 경우.
    """
    text = (value or "").strip()
    if not text:
        raise ApiParseError("날짜 값이 비어 있습니다.")

    match = _COMPACT.match(text) or _DELIMITED.match(text)
    if match is None:
        raise ApiParseError(f"알 수 없는 날짜 형식입니다: {value!r}")

    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ApiParseError(f"존재하지 않는 날짜입니다: {value!r}") from exc


def parse_range(value: str) -> tuple[date, date]:
    """유효기간 범위 문자열을 (시작일, 종료일) 로 변환합니다.

    Args:
        value: ``2022.04.07 ~ 2025.04.06`` 또는 ``20220408 - 20240407`` 형태.

    Returns:
        ``(시작일, 종료일)``.

    Raises:
        ApiParseError: 두 조각으로 나뉘지 않거나, 각 조각이 날짜가 아니거나,
            시작일이 종료일보다 늦은 경우.
    """
    text = (value or "").strip()
    if not text:
        raise ApiParseError("유효기간 값이 비어 있습니다.")

    parts = [part for part in _RANGE_SPLIT.split(text) if part]
    if len(parts) != 2:
        raise ApiParseError(f"유효기간 범위를 두 값으로 나눌 수 없습니다: {value!r}")

    start, end = parse_day(parts[0]), parse_day(parts[1])
    if start > end:
        raise ApiParseError(f"유효기간 시작일이 종료일보다 늦습니다: {value!r}")
    return start, end
