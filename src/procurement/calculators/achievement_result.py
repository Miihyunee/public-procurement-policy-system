"""
procurement.calculators.achievement_result

정책별 우선구매 달성률 계산 결과를 담는 데이터 객체를 정의합니다.

Dashboard 등 상위 계층에 계산 결과를 전달하기 위한 순수 데이터 컨테이너이며,
비즈니스 로직을 포함하지 않습니다. 값은
:class:`procurement.calculators.procurement_achievement.ProcurementAchievementCalculator`
가 채웁니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(kw_only=True)
class AchievementResult:
    """정책 하나에 대한 우선구매 달성률 계산 결과.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명.
        purchase_amount: 해당 정책 인증기업으로부터의 구매금액.
        total_purchase_amount: 기관 전체 구매금액.
        achievement_rate: 목표 대비 달성률(%).
    """

    policy_id: int
    policy_code: str
    policy_name: str
    purchase_amount: Decimal
    total_purchase_amount: Decimal
    achievement_rate: Decimal
