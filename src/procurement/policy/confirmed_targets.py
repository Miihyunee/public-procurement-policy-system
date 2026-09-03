"""
procurement.policy.confirmed_targets

고객이 확정한 **정책별 목표비율**(2026-09-03 · STEP 98 §2 · STEP 99 §1)을 한곳에
적어 둡니다.

이 모듈은 계산하지 않습니다. 확정된 값이 무엇이고, 각 값을 **무엇으로 나누어
재는지**를 적어 둘 뿐입니다. 값을 쓰는 곳은
:mod:`procurement.database.policy_target_repository` 하나입니다.

목표비율은 숫자 하나가 아니다
=============================
목표비율은 숫자 하나로 보이지만 실제로는 **두 가지**를 담고 있습니다.

1. 비율 자체 (예: 50%)
2. 그 비율을 재는 **분모** (예: 총 구매금액)

========================  ==========  ============================
정책                      목표비율    분모(scope)
========================  ==========  ============================
중소기업                  50%         총 구매금액
창업기업                  3.4%        총 구매금액
사회적기업                3%          총 구매금액
사회적협동조합            0.1%        총 구매금액
장애인기업                1%          총 구매금액
장애인표준사업장          0.8%        총 구매금액
여성기업                  3%          **공사** 구매금액
여성기업                  5%          **용역** 구매금액
여성기업                  5%          **물품** 구매금액
국가유공자자활용사촌      7%          **생산가능품목** 구매액
========================  ==========  ============================

⭐ **여덟 정책의 목표를 모두 저장합니다**(STEP 99 §1·§5). 분모까지 함께 적으므로
«여성기업 3%» 와 «중소기업 3%» 가 섞이지 않습니다.

⛔ 저장하는 것과 **달성률을 낼 수 있는 것**은 다릅니다. 계산기가 낼 수 있는 분모는
기관 전체 구매금액 하나뿐이라, 여성기업과 자활용사촌은 **목표는 보이되 달성률은
«계산 보류»** 입니다(:data:`~procurement.core.target_scope.CALCULABLE_SCOPES`).
분모를 구하는 방법이 확정되면 그때 계산이 열립니다 — ⛔ 없는 분모를 전체
구매금액으로 바꿔치기하지 않습니다.

.. note::
    **여성기업의 「용역·물품 5%」.** 고객은 용역과 물품을 묶어 5% 라고 말했습니다.
    시스템의 구매유형은 셋(공사 · 용역 · 물품)이므로 **용역 5% · 물품 5% 두 행**으로
    적습니다. ⛔ 값을 바꾼 것이 아니라 같은 값을 두 유형에 각각 적은 것입니다.

.. note::
    **장애인표준사업장의 「1000분의 8」.** 1000분의 8 = 0.8% 로 읽었습니다
    (STEP 98 §2 지시서의 §3-1 예시가 «0.8» 이었습니다). ⛔ STEP 99 §7 에 따라 이
    값을 임의로 바꾸지 않고 그대로 두며, 고객 확인 요청은 열려 있습니다(§0.24.2).

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

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.core.target_scope import PRODUCIBLE_ITEMS, TOTAL, is_calculable


@dataclass(frozen=True, kw_only=True)
class ConfirmedTarget:
    """고객이 확정한 목표비율 한 줄.

    Attributes:
        policy_code: 정책 코드.
        target_rate: 확정된 목표비율(%). ⛔ 반올림·보정하지 않은 값입니다.
        scope: 이 비율을 재는 분모 기준
            (:mod:`procurement.core.target_scope`).
        note: 이 값을 그렇게 읽은 근거. 없으면 빈 문자열.
    """

    policy_code: str
    target_rate: Decimal
    scope: str = TOTAL
    note: str = ""

    @property
    def calculable(self) -> bool:
        """지금 이 목표로 달성률까지 낼 수 있는가."""
        return is_calculable(self.scope)


#: 2026-09-03 고객 확정 목표비율. ⛔ 임의로 보정하지 않았습니다.
CONFIRMED_TARGETS: Final[tuple[ConfirmedTarget, ...]] = (
    ConfirmedTarget(policy_code="SMALL_BUSINESS", target_rate=Decimal("50")),
    ConfirmedTarget(policy_code="STARTUP", target_rate=Decimal("3.4")),
    ConfirmedTarget(policy_code="SOCIAL_ENTERPRISE", target_rate=Decimal("3")),
    ConfirmedTarget(policy_code="SOCIAL_COOPERATIVE", target_rate=Decimal("0.1")),
    ConfirmedTarget(policy_code="DISABLED", target_rate=Decimal("1")),
    ConfirmedTarget(
        policy_code="DISABLED_STANDARD_WORKPLACE",
        target_rate=Decimal("0.8"),
        note="고객 표현 「1000분의 8」 = 0.8%. 의미 확인 요청 중(확인 요청서 ⑦).",
    ),
    # ── 여성기업: 목표가 구매유형별로 나뉜다. ⛔ 하나를 고르거나 평균 내지 않는다.
    ConfirmedTarget(
        policy_code="WOMAN",
        target_rate=Decimal("3"),
        scope=CONSTRUCTION,
        note="공사 3%.",
    ),
    ConfirmedTarget(
        policy_code="WOMAN",
        target_rate=Decimal("5"),
        scope=SERVICE,
        note="고객 표현 「용역·물품 5%」 중 용역.",
    ),
    ConfirmedTarget(
        policy_code="WOMAN",
        target_rate=Decimal("5"),
        scope=GOODS,
        note="고객 표현 「용역·물품 5%」 중 물품.",
    ),
    # ── 자활용사촌: 비율은 확정, 분모는 미확보. 목표만 저장하고 달성률은 보류.
    ConfirmedTarget(
        policy_code="SELF_SUPPORT_VILLAGE",
        target_rate=Decimal("7"),
        scope=PRODUCIBLE_ITEMS,
        note="생산가능품목 목록·거래별 품목 정보가 없어 분모를 낼 수 없다(확인 요청서 ⑥).",
    ),
)

#: 달성률까지 낼 수 있는 목표 — 분모가 기관 전체 구매금액인 것들.
CALCULABLE_TARGETS: Final[tuple[ConfirmedTarget, ...]] = tuple(
    target for target in CONFIRMED_TARGETS if target.calculable
)

#: 저장은 하되 **달성률은 «계산 보류»** 인 목표.
ON_HOLD_TARGETS: Final[tuple[ConfirmedTarget, ...]] = tuple(
    target for target in CONFIRMED_TARGETS if not target.calculable
)

#: 달성률을 낼 수 있는 목표비율 ``{정책 코드: 비율}`` — 분모가 ``TOTAL`` 인 것뿐.
STORABLE_TARGET_RATES: Final[dict[str, Decimal]] = {
    target.policy_code: target.target_rate for target in CALCULABLE_TARGETS
}

#: 달성률을 낼 수 없는 정책 ``{정책 코드: 이유}``.
ON_HOLD_REASONS: Final[dict[str, str]] = {
    "WOMAN": (
        "목표가 구매유형별로 나뉘어 있고(공사 3% · 용역·물품 5%), 분모도 "
        "구매유형별 금액입니다. 지출 원본에 구매유형이 없어 담당자 확정값이 "
        "쌓이기 전에는 분모를 낼 수 없습니다(확인 요청서 ⑤)."
    ),
    "SELF_SUPPORT_VILLAGE": (
        "분모가 「생산가능품목 총 구매액」 인데 생산가능품목 목록도, 거래별 품목 "
        "정보도 없습니다. ⛔ 전체 구매금액으로 대신하지 않습니다(확인 요청서 ⑥)."
    ),
}
