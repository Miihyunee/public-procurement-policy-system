"""
procurement.dashboard.data_service

Calculator 계산 결과를 대시보드 화면이 바로 사용할 수 있는 요약 DTO 로 변환하는
서비스 계층입니다.

:class:`ProcurementAchievementCalculator` 를 그대로 주입받아 사용하며, 계산
로직을 다시 구현하지 않습니다. 계산기가 산출한 :class:`AchievementResult` 에
목표율·부족률·상태를 덧붙여 :class:`DashboardSummary` 로 조합합니다.

.. note::
    본 서비스는 데이터 생성 계층입니다. UI·API·차트는 이번 범위에 포함하지
    않습니다. 목표율(``target_rate``)은 두 방식으로 공급할 수 있습니다.

    - :meth:`DashboardDataService.build_summary` — 호출 시 목표율 dict 를 직접 입력(하위호환).
    - :meth:`DashboardDataService.build_summary_from_registered_targets` — 시스템에
      등록된(활성·목표율 설정) 정책의 목표율을 조회해 사용(Issue #20-2).
"""

from __future__ import annotations

from decimal import Decimal

from procurement.calculators.achievement_result import AchievementResult
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.dashboard.models import (
    DashboardStatus,
    DashboardSummary,
    PolicySummary,
)
from procurement.database.policy_repository import PolicyRepository

#: 부족률 표기 자리수 (소수점 둘째 자리)
_RATE_EXPONENT = Decimal("0.01")

#: 완전 달성 기준 비율(%). 부족률은 이 값에서 달성률을 뺀 값입니다.
_FULL_ACHIEVEMENT = Decimal("100")


class DashboardDataService:
    """Calculator 결과를 대시보드 요약 DTO 로 조합합니다."""

    def __init__(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repository: PolicyRepository | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            calculator: 달성률 계산에 사용할 :class:`ProcurementAchievementCalculator`.
            policy_repository: 시스템에 등록된 목표율을 조회할
                :class:`PolicyRepository`. :meth:`build_summary_from_registered_targets`
                를 사용할 때만 필요하며, 외부 입력 방식(:meth:`build_summary`)만
                사용할 경우 생략할 수 있습니다.
        """
        self._calculator = calculator
        self._policy_repository = policy_repository

    def build_summary(self, target_rates: dict[int, Decimal]) -> DashboardSummary:
        """대시보드 전체 요약을 생성합니다.

        전체 구매액은 정책 목표 입력과 무관하게 항상 집계하며, 정책별 요약은
        ``target_rates`` 에 포함된 정책에 대해서만 생성합니다.

        Args:
            target_rates: ``{policy_id: 목표율}`` 형태의 매핑. 비어 있으면
                정책 요약 없이 전체 구매액만 담긴 요약을 반환합니다.

        Returns:
            :class:`DashboardSummary`.

        Raises:
            CalculatorValidationError: 목표율이 0 이하이거나 존재하지 않는
                정책이 포함된 경우(계산기 검증 전파).
        """
        total_amount = self._calculator.calculate_total_purchase()
        results = self._calculator.calculate_all(target_rates)

        summaries = [
            self._to_policy_summary(result, target_rates[result.policy_id]) for result in results
        ]
        return DashboardSummary(total_purchase_amount=total_amount, policy_summaries=summaries)

    def build_summary_from_registered_targets(self) -> DashboardSummary:
        """시스템에 등록된 목표율로 대시보드 전체 요약을 생성합니다.

        외부 입력 없이 :class:`PolicyRepository` 에서 **활성이면서 목표율이
        설정된** 정책을 조회해 ``{policy_id: target_rate}`` 를 구성한 뒤,
        기존 :meth:`build_summary` 를 그대로 호출합니다. 목표율이 없는(NULL)
        정책은 조회 단계에서 제외되므로 계산 대상에 포함되지 않습니다.

        Calculator 는 변경하지 않으며, 목표율 dict 를 만들어 넘기는 방식만
        다릅니다.

        Returns:
            :class:`DashboardSummary`. 목표율이 설정된 활성 정책이 없으면 정책
            요약은 빈 목록이 되고 전체 구매액만 담깁니다.

        Raises:
            ValueError: 생성 시 ``policy_repository`` 를 주입하지 않은 경우.
            CalculatorValidationError: 존재하지 않는 정책이 조회된 경우(계산기 검증 전파).
        """
        if self._policy_repository is None:
            raise ValueError(
                "build_summary_from_registered_targets 를 사용하려면 "
                "policy_repository 를 주입해야 합니다."
            )

        target_rates: dict[int, Decimal] = {}
        for policy in self._policy_repository.find_active_with_target_rate():
            # 조회 조건상 target_rate 는 NOT NULL 이며, 저장된 정책은 policy_id 를 가진다.
            if policy.policy_id is None or policy.target_rate is None:
                continue
            target_rates[policy.policy_id] = policy.target_rate

        return self.build_summary(target_rates)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _to_policy_summary(self, result: AchievementResult, target_rate: Decimal) -> PolicySummary:
        """계산 결과 한 건에 목표율·부족률·상태를 더해 요약 DTO 로 변환합니다."""
        shortage_rate = self._shortage_rate(result.achievement_rate)
        status = DashboardStatus.from_achievement_rate(result.achievement_rate)
        return PolicySummary(
            policy_id=result.policy_id,
            policy_code=result.policy_code,
            policy_name=result.policy_name,
            purchase_amount=result.purchase_amount,
            total_purchase_amount=result.total_purchase_amount,
            target_rate=target_rate,
            achievement_rate=result.achievement_rate,
            shortage_rate=shortage_rate,
            status=status,
        )

    @staticmethod
    def _shortage_rate(achievement_rate: Decimal) -> Decimal:
        """목표 달성까지 부족한 비율(%)을 계산합니다.

        ``max(0, 100 - 달성률)`` 로 정의하며, 목표를 초과 달성한 경우(달성률
        100 이상)에는 ``0`` 을 반환합니다.
        """
        shortage = _FULL_ACHIEVEMENT - achievement_rate
        if shortage < 0:
            shortage = Decimal("0")
        return shortage.quantize(_RATE_EXPONENT)
