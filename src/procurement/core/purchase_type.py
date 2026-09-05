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


#: 🟢 **자동판정에 쓰는 예산과목 (STEP 122).**
#:
#: :data:`CONFIRMED_BUDGET_ACCOUNT_TYPES` 의 3건 중 **실측으로 한 유형만 나온**
#: 둘입니다(``PURCHASE_TYPE_CLASSIFICATION_ANALYSIS.md`` §165~167).
#:
#: ==================  ======  ======  ======  ==========
#: 예산과목              물품    용역    공사    판정
#: ==================  ======  ======  ======  ==========
#: 소모성물품구입비        214       0       0   단일 ✅
#: 도서인쇄비              122       0       0   단일 ✅
#: **임차료**                3     210       0   🔴 혼재
#: ==================  ======  ======  ======  ==========
#:
#: .. warning::
#:     ⛔ **``임차료`` 는 빠져 있습니다.** 213건 중 3건이 물품이라
#:     ``DECISIONS.md`` §0.9.4 가 **계산 연결을 보류**했고 §0.9.5 가 그 보류를
#:     유지했습니다. 자동으로 용역이라고 확정하면 그 3건이 조용히 틀립니다.
#:     보류를 푸는 것은 **고객 확인 사항**입니다.
#:
#: .. warning::
#:     ⛔ **여기에 항목을 더하지 않습니다.** 판정 원칙 2 — 「예산과목 단독으로도
#:     확정하지 않는다」(§0.9.5) — 가 그대로 살아 있습니다. ``외주용역비``(용역
#:     201 / 공사 69) · ``각종수수료`` · ``행사운영비`` · ``자산취득비`` 는
#:     어느 것도 한 유형으로 모이지 않습니다.
RULE_CLASSIFIABLE_BUDGET_ACCOUNTS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "도서인쇄비": GOODS,
        "소모성물품구입비": GOODS,
    }
)


def classify_by_confirmed_rule(budget_account: str | None) -> str | None:
    """**자동으로 확정해도 되는** 구매유형을 돌려줍니다(STEP 122).

    :func:`classify_budget_account` 와 달리 **보류된 매핑을 빼고** 봅니다.
    지금은 ``도서인쇄비`` · ``소모성물품구입비`` 둘뿐이며, 둘 다 실측에서
    **다른 유형이 한 건도 나오지 않은** 항목입니다.

    **완전 일치**로만 판정합니다. ⛔ 「도서가 들어가면 물품」 같은 부분 문자열
    규칙을 만들지 않습니다 — 그렇게 하면 고객이 직접 부정한 사례(기념품
    KC인증 → 용역, 나라장터 물품 수수료 → 물품)를 그대로 틀리게 됩니다.

    Args:
        budget_account: 지출데이터의 예산과목 값.

    Returns:
        자동 확정할 구매유형. 확정 근거가 없으면 ``None`` — 그 거래는
        **담당자 검토 대상**으로 남습니다.

    Examples:
        >>> classify_by_confirmed_rule("도서인쇄비")
        'GOODS'
        >>> classify_by_confirmed_rule("임차료") is None      # 보류(§0.9.4)
        True
        >>> classify_by_confirmed_rule("외주용역비") is None  # 혼재 — 미확정
        True
    """
    if budget_account is None:
        return None
    key = budget_account.strip()
    if not key:
        return None
    return RULE_CLASSIFIABLE_BUDGET_ACCOUNTS.get(key)


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
