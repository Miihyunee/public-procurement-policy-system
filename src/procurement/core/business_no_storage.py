"""
procurement.core.business_no_storage

사업자등록번호를 **저장할 때** 쓰는 표기 규칙.

구매 데이터는 적재하면서 이미 숫자만 남습니다
(:func:`~procurement.matchers.business_no.normalize_business_no`). 기업 데이터가
``220-81-62517`` 로 저장되면 같은 사업자인데도 **영원히 연결되지 않고**, 오류도
나지 않은 채 정책 구매액(분자)이 조용히 0 이 됩니다(STEP 73 검수에서 발견).

왜 :mod:`procurement.matchers.business_no` 가 아닌가
=====================================================

``matchers`` 는 **저장소를 사용하는 서비스 계층**입니다. 저장소가 거꾸로
그 패키지를 가져오면 순환이 됩니다. 저장 규칙은 저장소보다 아래에 있어야 하므로
:mod:`procurement.core` 에 둡니다 — :mod:`~procurement.core.description_key` ·
:mod:`~procurement.core.performance_exclusion` 과 같은 자리입니다.

세 가지를 섞지 않습니다
=======================

===============================================  ==========  =====================
함수                                              쓰임        10자리·숫자를 요구하는가
===============================================  ==========  =====================
:func:`~procurement.matchers.business_no.normalize_business_no`   결합 키   **예**
:func:`to_storage_business_no`                    저장        아니오
:func:`~procurement.matchers.business_no.business_no_search_key`  검색 비교  아니오
===============================================  ==========  =====================

.. warning::
    ⛔ **업무적 유효/무효를 판정하지 않습니다.** 자릿수도 체크섬도 보지 않고,
    숫자를 만들어내거나 고치지도 않습니다. 없애는 것은 **표기용 구분자 하나**
    뿐입니다.
"""

from __future__ import annotations

import re

#: 제거 대상 구분자 — 하이픈 · 공백 · 마침표 등.
#:
#: :data:`procurement.matchers.business_no._SEPARATOR_PATTERN` 과 **같은 집합**
#: 입니다. 한쪽만 넓히면 저장한 값과 찾는 값이 어긋나므로 함께 고쳐야 합니다.
_SEPARATOR_PATTERN = re.compile(r"[\s\-.‐-―]")

#: 숫자형이 문자열로 변환되며 붙는 소수부 (예: ``1234567890.0``).
_TRAILING_DECIMAL_PATTERN = re.compile(r"\.0+$")


def to_storage_business_no(value: object) -> str:
    """**저장용** 사업자등록번호 — 구분자를 지운 비교 가능한 형태로 만듭니다.

    ⛔ 검색 키와 결과가 같더라도 **같은 함수가 아닙니다.** 하나는 *무엇을
    남길지*(저장), 다른 하나는 *무엇을 보여줄지*(검색)를 정하는 규칙이라,
    한쪽이 바뀔 때 다른 쪽이 딸려가면 안 됩니다.

    Args:
        value: 저장하려는 사업자등록번호. 문자열·숫자·``None``.

    Returns:
        구분자를 지운 문자열. 입력이 비면 빈 문자열.

    Examples:
        >>> to_storage_business_no("220-81-62517")
        '2208162517'
        >>> to_storage_business_no("2208162517")
        '2208162517'
        >>> to_storage_business_no(None)
        ''
    """
    if value is None:
        return ""
    if isinstance(value, float):
        # 1234567890.0 → "1234567890" (지수 표기 방지)
        text = _TRAILING_DECIMAL_PATTERN.sub("", f"{value:.1f}")
    elif isinstance(value, str):
        text = value
    else:
        text = _TRAILING_DECIMAL_PATTERN.sub("", str(value))
    return _SEPARATOR_PATTERN.sub("", text).strip()
