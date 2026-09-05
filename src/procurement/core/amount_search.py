"""
procurement.core.amount_search

검토 화면 검색칸에 **금액**이 들어왔을 때 그것을 금액으로 읽는 규칙.

2026-08-31 고객 최종 회신(``DECISIONS.md`` §0.12.5 · Q71-C):

    있으면 좋을 것 같아. 검토화면에서 금액, 사업자등록번호, 적요 정도는
    검색기능이 있으면 좋겠어.

셋 중 적요·사업자등록번호는 이미 있었고, **금액이 없었습니다.**

.. warning::
    ⛔ **범위 검색이 아닙니다.** ``1000000`` 을 넣으면 금액이 정확히 1,000,000
    인 건만 찾습니다. "이상 · 이하 · 근사" 같은 기준을 만들지 않았습니다 —
    고객이 말한 것은 "검색" 이지 조건식이 아닙니다.

.. warning::
    ⛔ **금액으로 읽히지 않는 검색어는 금액 조건이 되지 않습니다.** 그때는
    :data:`None` 을 돌려주며, 검색은 적요·거래처명·사업자등록번호만 봅니다.
    담당자가 어느 칸에 넣을지 고르지 않아도 되게 하기 위한 것입니다.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: 금액을 읽기 전에 떼어 내는 것 — 자릿점 · 공백 · 원화 표기.
#:
#: ⚠️ 담당자는 화면·지출결의서에 보이는 그대로 옮겨 적습니다
#: (``1,000,000`` · ``1000000원``). 그대로 두면 0건이 나오고, **0건은 "그런
#: 거래가 없다" 로 읽힙니다**(STEP 73 검수에서 같은 일이 사업자등록번호에서
#: 발견되었습니다).
_NOISE_PATTERN = re.compile(r"[,\s￦₩]|원$")


def amount_search_key(text: str | None) -> Decimal | None:
    """검색어를 금액으로 읽습니다. 금액이 아니면 ``None``.

    Args:
        text: 담당자가 검색칸에 넣은 글자.

    Returns:
        금액으로 읽힌 :class:`~decimal.Decimal`, 또는 읽을 수 없으면 ``None``.

    Examples:
        >>> amount_search_key("1000000")
        Decimal('1000000')
        >>> amount_search_key("1,000,000원")
        Decimal('1000000')
        >>> amount_search_key("복사용지") is None
        True
        >>> amount_search_key("") is None
        True
        >>> amount_search_key("-500") is None
        True
    """
    if text is None:
        return None
    cleaned = _NOISE_PATTERN.sub("", text).strip()
    if not cleaned:
        return None
    # ⛔ 부호·지수 표기(``1e6`` · ``-500``)를 금액으로 읽지 않는다. 화면에
    #    보이는 금액은 숫자와 소수점뿐이다.
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:  # pragma: no cover - 위 검사를 통과하면 오지 않는다
        return None
