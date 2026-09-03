"""
procurement.policy.confirmed_targets

고객이 확정한 **정책별 목표비율**(2026-09-03 · STEP 98 §2)을 한곳에 적어 둡니다.

이 모듈은 계산하지 않습니다. 확정된 값이 무엇이고, 그중 **어떤 것이 지금 구조로
저장 가능한지**를 구분해 둘 뿐입니다. 값을 쓰는 곳은
:mod:`procurement.database.policy_target_repository` 하나입니다.

왜 나누어 적는가
================
목표비율은 숫자 하나로 보이지만 실제로는 **두 가지**를 담고 있습니다.

1. 비율 자체 (예: 50%)
2. 그 비율을 재는 **분모** (예: 총 구매금액)

현재 저장 구조 ``PolicyTarget(year, policy_id, target_rate)`` 는 ①만 담고,
계산기는 분모로 **언제나 기관 전체 구매금액**을 씁니다
(:meth:`~procurement.calculators.ProcurementAchievementCalculator.calculate_total_purchase`).

따라서 분모가 전체 구매금액이 아닌 정책은 비율만 저장해서는 **틀린 달성률**이
나옵니다. ⛔ 그래서 저장 가능한 것과 아닌 것을 갈라 놓았습니다. 숫자를 넣어 두고
잘못된 달성률을 보여 주는 것보다, 넣지 않고 «계산 보류» 라고 말하는 편이 낫습니다.

========================  ==========  ================================
정책                      목표비율    분모
========================  ==========  ================================
중소기업                  50%         총 구매금액          → 저장 가능
창업기업                  3.4%        총 구매금액          → 저장 가능
사회적기업                3%          총 구매금액          → 저장 가능
사회적협동조합            0.1%        총 구매금액          → 저장 가능
장애인기업                1%          총 구매금액          → 저장 가능
장애인표준사업장          0.8%        총 구매금액          → 저장 가능
여성기업                  3% / 5%     **구매유형별** 금액  → ⛔ 저장 불가
국가유공자자활용사촌      7%          **생산가능품목** 금액 → ⛔ 저장 불가
========================  ==========  ================================

.. note::
    **장애인표준사업장의 「1000분의 8%」 표기.** 작업지시서 §2 는 «1000분의 8%»
    로, §3-1 예시는 «0.8» 로 적었습니다. 1000분의 8 = 0.8% 이므로 §3-1 예시와
    같은 값인 ``0.8`` 로 읽었습니다. ⛔ 임의로 정한 것이 아니라 지시서 안의 두
    표기가 가리키는 같은 값입니다. 만약 «0.008%» 를 뜻한 것이라면 고객 확인이
    필요합니다(§0.24.2).

.. note::
    ⛔ 이 값들을 seed 에 넣지 않습니다. 목표비율의 정본은 **연도별**
    ``policy_target`` 이고(§0.20), 등록 시점은 운영자가 정합니다. 여기 있는 것은
    "고객이 확정한 값이 무엇인가" 라는 **기록**이며, 등록은
    ``python -m procurement targets --year 2026`` 이 수행합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final


@dataclass(frozen=True, kw_only=True)
class ConfirmedTarget:
    """고객이 확정한 목표비율 하나.

    Attributes:
        policy_code: 정책 코드.
        target_rate: 확정된 목표비율(%). 저장 가능한 정책에만 있습니다.
        denominator: 고객이 말한 분모를 **그대로** 옮긴 문장.
        storable: 현재 구조로 저장해도 올바른 달성률이 나오는가.
        blocked_reason: 저장할 수 없는 이유. 저장 가능하면 빈 문자열.
    """

    policy_code: str
    target_rate: Decimal | None
    denominator: str
    storable: bool
    blocked_reason: str = ""


#: 총 구매금액을 분모로 쓰는 일반 정책 — 지금 구조로 그대로 저장됩니다.
_TOTAL = "총 구매금액"

#: 2026-09-03 고객 확정 목표비율 (STEP 98 §2). ⛔ 임의로 보정하지 않았습니다.
CONFIRMED_TARGETS: Final[tuple[ConfirmedTarget, ...]] = (
    ConfirmedTarget(
        policy_code="SMALL_BUSINESS",
        target_rate=Decimal("50"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        policy_code="STARTUP",
        target_rate=Decimal("3.4"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        policy_code="SOCIAL_ENTERPRISE",
        target_rate=Decimal("3"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        policy_code="SOCIAL_COOPERATIVE",
        target_rate=Decimal("0.1"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        policy_code="DISABLED",
        target_rate=Decimal("1"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        # 「1000분의 8%」 = 0.8% — 모듈 docstring 의 표기 주석 참조.
        policy_code="DISABLED_STANDARD_WORKPLACE",
        target_rate=Decimal("0.8"),
        denominator=_TOTAL,
        storable=True,
    ),
    ConfirmedTarget(
        policy_code="WOMAN",
        target_rate=None,
        denominator="구매유형별 총 구매금액 (공사 3% / 용역·물품 5%)",
        storable=False,
        blocked_reason=(
            "목표가 구매유형별로 **둘**이라 단일 target_rate 로 담을 수 없고, "
            "분모도 전체 구매금액이 아니라 구매유형별 금액입니다. "
            "⛔ 한쪽만 저장하면 나머지 유형의 달성률이 틀립니다."
        ),
    ),
    ConfirmedTarget(
        policy_code="SELF_SUPPORT_VILLAGE",
        target_rate=None,
        denominator="자활용사촌 생산가능품목 총 구매액",
        storable=False,
        blocked_reason=(
            "비율 7% 자체는 담을 수 있으나 분모가 「생산가능품목 총 구매액」 이라 "
            "계산기가 쓰는 전체 구매금액과 다릅니다. 저장하면 **틀린 달성률**이 "
            "나오므로 넣지 않습니다."
        ),
    ),
)

#: 지금 구조로 등록 가능한 목표비율 ``{정책 코드: 비율}``.
STORABLE_TARGET_RATES: Final[dict[str, Decimal]] = {
    target.policy_code: target.target_rate
    for target in CONFIRMED_TARGETS
    if target.storable and target.target_rate is not None
}

#: 확정은 받았으나 지금 구조로 등록할 수 없는 정책 ``{정책 코드: 이유}``.
BLOCKED_TARGETS: Final[dict[str, str]] = {
    target.policy_code: target.blocked_reason for target in CONFIRMED_TARGETS if not target.storable
}
