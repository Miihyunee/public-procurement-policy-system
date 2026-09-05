"""
procurement.core.target_scope

목표비율이 **무엇을 분모로 재는가**(scope)를 나타내는 값들입니다.

왜 필요한가
===========
STEP 98 에서 드러난 사실 하나: 목표비율은 숫자 하나로 보이지만 실제로는 **비율과
그 비율을 재는 분모** 두 가지를 담고 있습니다.

======================  ==========================  ==================
정책                    비율                        분모
======================  ==========================  ==================
중소기업                50%                         총 구매금액
여성기업                공사 3% · 용역·물품 5%      **구매유형별** 금액
국가유공자자활용사촌    7%                          **생산가능품목** 금액
======================  ==========================  ==================

분모를 적어 두지 않으면 «여성기업 3%» 와 «중소기업 3%» 가 같은 뜻이 되어 버립니다.
그래서 목표비율 한 행마다 분모 기준을 함께 저장합니다.

계산 가능 여부와의 관계
=======================
분모 기준을 저장하는 것과 **그 분모를 실제로 구할 수 있는가**는 다른 문제입니다.
현재 계산기가 낼 수 있는 분모는 기관 전체 구매금액 하나뿐이므로
(``ProcurementAchievementCalculator.calculate_total_purchase``),
:data:`TOTAL` 만 달성률까지 계산됩니다. 나머지는 **목표는 보이되 달성률은
«계산 보류»** 입니다 — ⛔ 없는 분모를 전체 구매금액으로 바꿔치기하지 않습니다.

:data:`CALCULABLE_SCOPES` 가 그 경계이며, 분모를 구하는 방법이 확정되면 이곳에
값을 더하는 것으로 확장됩니다.
"""

from __future__ import annotations

from typing import Final

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE

#: 기관 **전체 구매금액**을 분모로 쓴다 — 일반 정책의 기본값.
TOTAL: Final = "TOTAL"

#: 자활용사촌 **생산가능품목** 총 구매액을 분모로 쓴다.
#:
#: ⚠️ 생산가능품목 목록도, 거래별 품목 정보도 아직 없습니다. 그래서 목표비율은
#: 저장하되 달성률은 내지 않습니다(확인 요청서 ⑥).
PRODUCIBLE_ITEMS: Final = "PRODUCIBLE_ITEMS"

#: 목표비율의 분모 기준으로 허용되는 값.
#:
#: 구매유형(``CONSTRUCTION`` · ``SERVICE`` · ``GOODS``)은 :mod:`.purchase_type`
#: 의 값을 **그대로** 씁니다. ⛔ 같은 개념에 이름을 두 벌 만들지 않습니다.
TARGET_SCOPES: Final[frozenset[str]] = frozenset(
    {TOTAL, CONSTRUCTION, SERVICE, GOODS, PRODUCIBLE_ITEMS}
)

#: 지금 **달성률까지 계산할 수 있는** 분모 기준.
#:
#: ⛔ 여기에 값을 더하는 것은 "그 분모를 실제로 구하는 코드가 생겼다" 는 뜻입니다.
#: 분모를 구하지 못하는 채로 값을 더하면 틀린 달성률이 화면에 나갑니다.
#:
#: ⚠️ **2026-09-03 · STEP 103 으로 구매유형 셋이 열렸습니다.** 담당자가 확정한
#: ``purchase_review.final_purchase_type`` 을 분모·분자에 함께 적용하는 경로가
#: 생겼기 때문입니다(``calculate_total_purchase(period, scope)``).
#: ⛔ 유형을 자동 판정하게 된 것이 **아닙니다** — 확정된 행만 셉니다.
#:
#: :data:`PRODUCIBLE_ITEMS` 는 여전히 빠져 있습니다. 거래별 품목 식별정보가
#: 원본에도 시스템에도 없어 분모를 만들 수 없습니다(STEP 101 LEVEL 3).
CALCULABLE_SCOPES: Final[frozenset[str]] = frozenset({TOTAL, CONSTRUCTION, SERVICE, GOODS})

#: 화면에 보여 줄 한글 이름.
TARGET_SCOPE_LABELS: Final[dict[str, str]] = {
    TOTAL: "총 구매금액",
    CONSTRUCTION: "공사",
    SERVICE: "용역",
    GOODS: "물품",
    PRODUCIBLE_ITEMS: "생산가능품목 구매액",
}


def is_calculable(scope: str) -> bool:
    """이 분모 기준으로 달성률을 낼 수 있는가.

    Args:
        scope: 분모 기준.

    Returns:
        낼 수 있으면 ``True``. ``False`` 면 목표는 보여 주되 달성률은
        «계산 보류» 입니다.
    """
    return scope in CALCULABLE_SCOPES


def scope_label(scope: str) -> str:
    """분모 기준의 한글 이름. 모르는 값이면 받은 값을 그대로 돌려줍니다."""
    return TARGET_SCOPE_LABELS.get(scope, scope)
