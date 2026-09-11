"""
procurement.core.description_key

적요를 **비교용 키**로 바꿉니다.

같은 지출을 가리키는 적요가 띄어쓰기만 다른 경우가 흔합니다
(``"교육계획서  발송"`` 처럼 공백이 둘인 경우도 실데이터에 있습니다). 그런
차이 때문에 "같은 적요" 를 놓치지 않도록 한 곳에서 정규화합니다.

.. warning::
    ⛔ **업무규칙이 아닙니다.**

    이 함수는 어떤 적요가 어떤 구매유형인지 **판정하지 않습니다.** 문자열을
    비교 가능한 형태로 맞추는 일만 합니다.

.. warning::
    ⛔ **원본을 바꾸지 않습니다.** DB-1 의 ``description`` 은 그대로 남고,
    이 값은 조회·비교에만 쓰입니다.

.. note::
    운영 코드(검토 화면의 과거 이력 조회)와 실험 코드(코퍼스 충돌 분석)가
    **같은 기준**으로 묶어야 숫자가 서로 맞습니다. 그래서 experiments 쪽이
    아니라 ``core`` 에 둡니다.
"""

from __future__ import annotations

import re
from typing import Final

#: 연속된 공백류(스페이스 · 탭 · 개행)를 한 덩어리로 봅니다.
_WHITESPACE: Final = re.compile(r"\s+")


def normalize_description(description: str | None) -> str:
    """적요를 비교용 키로 정규화합니다.

    공백을 모두 지우고 소문자로 맞춥니다.

    Args:
        description: 원본 적요. ``None`` 이거나 공백만일 수 있습니다.

    Returns:
        정규화된 키. 입력이 비면 빈 문자열.

    Examples:
        >>> normalize_description("  LED  교체 공사 ")
        'led교체공사'
        >>> normalize_description(None)
        ''
    """
    if not description:
        return ""
    return _WHITESPACE.sub("", description).lower()
