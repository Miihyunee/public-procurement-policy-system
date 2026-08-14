"""
procurement.core.purchase_type

구매유형(공사 · 용역 · 물품)과 **고객이 확정한 예산과목 매핑**을 정의합니다.

구매유형이 필요한 이유는 **여성기업 목표율이 유형별로 다르기** 때문입니다
(공사 3% / 용역·물품 5%). 다만 이 모듈은 **값과 매핑만** 제공하며, 목표율 판정이나
계산에는 아직 사용되지 않습니다. 그 구조 변경은 별도 작업입니다.

.. warning::
    **자동 분류 범위를 넓히지 않습니다.**

    고객이 확정한 것은 :data:`CONFIRMED_BUDGET_ACCOUNT_TYPES` 의 **3건뿐**입니다.
    ``외주용역비`` · ``통신비`` · ``수도광열비`` · ``각종수수료`` 등 나머지 예산과목은
    **분류하지 않고** ``None``(미분류)로 둡니다.

    "도서가 들어가면 물품", "임대가 들어가면 용역" 같은 **부분 문자열 규칙을 만들지
    않습니다.** 추측으로 분류하면 달성률이 조용히 왜곡됩니다.

.. note::
    ``None`` 은 "구매유형이 아직 확인되지 않았다"는 뜻이며, 오류가 아닙니다.
    샘플 기준으로 확정 3건이 덮는 범위는 **매입 금액의 약 34%** 이고, 금액이 가장 큰
    ``외주용역비``(약 42.4억)는 **미분류로 남습니다.**
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

#: 공사
CONSTRUCTION: Final = "CONSTRUCTION"

#: 용역
SERVICE: Final = "SERVICE"

#: 물품
GOODS: Final = "GOODS"

#: 허용되는 구매유형 값. 이 밖의 값은 사용하지 않습니다.
PURCHASE_TYPES: Final[frozenset[str]] = frozenset({CONSTRUCTION, SERVICE, GOODS})

#: 화면 표시용 한글 라벨.
PURCHASE_TYPE_LABELS: Final[dict[str, str]] = {
    CONSTRUCTION: "공사",
    SERVICE: "용역",
    GOODS: "물품",
}

#: 🟢 **고객이 확정한 예산과목 → 구매유형 매핑 (2026-08-14).**
#:
#: ``docs/DECISIONS.md`` §0.5.3 의 확정 내용을 그대로 옮긴 것입니다.
#: **정확히 이 3건만** 확정이며, 여기에 항목을 추가하는 것은 고객 확인 사항입니다.
CONFIRMED_BUDGET_ACCOUNT_TYPES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "도서인쇄비": GOODS,
        "소모성물품구입비": GOODS,
        "임차료": SERVICE,
    }
)


def classify_budget_account(budget_account: str | None) -> str | None:
    """예산과목을 구매유형으로 분류합니다.

    **완전 일치**로만 판정합니다. 부분 문자열·접두사·유사어 매칭을 하지 않습니다.

    Args:
        budget_account: 지출데이터의 예산과목 값. ``None`` 이거나 공백일 수
            있습니다(샘플 기준 매입행의 **15.4%** 가 결측입니다).

    Returns:
        :data:`GOODS` · :data:`SERVICE` · :data:`CONSTRUCTION` 중 하나.
        **고객이 확정하지 않은 값이면 ``None``**(미분류)을 반환합니다.

    Examples:
        >>> classify_budget_account("도서인쇄비")
        'GOODS'
        >>> classify_budget_account("임차료")
        'SERVICE'
        >>> classify_budget_account("외주용역비") is None   # 미확정 → 분류하지 않음
        True
    """
    if budget_account is None:
        return None
    key = budget_account.strip()
    if not key:
        return None
    return CONFIRMED_BUDGET_ACCOUNT_TYPES.get(key)


def is_valid_purchase_type(value: str | None) -> bool:
    """구매유형 값으로 사용할 수 있는지 확인합니다.

    ``None`` 은 "미분류"라는 정상 상태이므로 ``True`` 입니다.

    Args:
        value: 검사할 값.

    Returns:
        허용 값이거나 ``None`` 이면 ``True``.
    """
    return value is None or value in PURCHASE_TYPES
