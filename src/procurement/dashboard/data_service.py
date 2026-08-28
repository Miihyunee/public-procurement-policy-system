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
from procurement.core.period import RESOLUTION_DATE, PeriodFilter
from procurement.dashboard.models import (
    NOT_APPLICABLE,
    DashboardStatus,
    DashboardSummary,
    MissingResolutionDate,
    PolicySummary,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.policy import Policy

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
        purchase_repository: PurchaseRepository | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            calculator: 달성률 계산에 사용할 :class:`ProcurementAchievementCalculator`.
            policy_repository: 시스템에 등록된 목표율을 조회할
                :class:`PolicyRepository`. :meth:`build_summary_from_registered_targets`
                를 사용할 때만 필요하며, 외부 입력 방식(:meth:`build_summary`)만
                사용할 경우 생략할 수 있습니다.
            purchase_repository: **결의일자 미기재 건수**를 세는 데만 쓰는
                :class:`PurchaseRepository`. ⛔ 계산에는 쓰이지 않습니다 —
                달성률은 지금도 ``calculator`` 만 산출합니다. 생략하면 안내
                값이 "해당 없음" 으로 나갑니다.
        """
        self._calculator = calculator
        self._policy_repository = policy_repository
        self._purchase_repository = purchase_repository

    def build_summary(
        self, target_rates: dict[int, Decimal], period: PeriodFilter | None = None
    ) -> DashboardSummary:
        """대시보드 전체 요약을 생성합니다.

        전체 구매액은 정책 목표 입력과 무관하게 항상 집계하며, 정책별 요약은
        ``target_rates`` 에 포함된 정책에 대해서만 생성합니다.

        Args:
            target_rates: ``{policy_id: 목표율}`` 형태의 매핑. 비어 있으면
                정책 요약 없이 전체 구매액만 담긴 요약을 반환합니다.
            period: 적용할 기간 조건. 계산기에 그대로 전달합니다. ``None`` 이면
                기간 제한 없음(기존 동작).

        Returns:
            :class:`DashboardSummary`.

        Raises:
            CalculatorValidationError: 목표율이 0 이하이거나 존재하지 않는
                정책이 포함된 경우(계산기 검증 전파).
        """
        total_amount = self._calculator.calculate_total_purchase(period)
        results = self._calculator.calculate_all(target_rates, period)

        summaries = [
            self._to_policy_summary(result, target_rates[result.policy_id]) for result in results
        ]
        return DashboardSummary(
            total_purchase_amount=total_amount,
            policy_summaries=summaries,
            missing_resolution_date=self._missing_resolution_date(period),
        )

    def build_summary_from_registered_targets(
        self, period: PeriodFilter | None = None
    ) -> DashboardSummary:
        """시스템에 등록된 목표율로 대시보드 전체 요약을 생성합니다.

        외부 입력 없이 :class:`PolicyRepository` 에서 **활성 정책 전체**를 조회한
        뒤, 목표율 설정 여부에 따라 다르게 처리합니다.

        - **목표율이 설정된 정책**: 기존과 동일하게 계산기로 달성률을 계산합니다.
        - **목표율이 없는(NULL) 정책**: 계산기를 호출하지 않고, 계산 값을 모두
          ``None`` 으로 두고 상태를 :attr:`DashboardStatus.TARGET_RATE_NOT_SET`
          으로 표시합니다. **요약에서 제외하지 않습니다.**

        목표율이 없는 정책을 제외하지 않는 이유는, 화면에서 "정책이 없음"과
        "정책은 있으나 목표율이 아직 등록되지 않음"을 구분하기 위해서입니다.
        달성률을 ``0`` 으로 처리하지 않습니다.

        목표율이 있는 정책만 골라 dict 로 넘기는 방식은 기존과 같습니다.

        Args:
            period: 적용할 기간 조건. 계산기에 그대로 전달합니다. ``None`` 이면
                기간 제한 없음(기존 동작).

        Returns:
            :class:`DashboardSummary`. 활성 정책이 없으면 정책 요약은 빈 목록이
            되고 전체 구매액만 담깁니다.

        Raises:
            ValueError: 생성 시 ``policy_repository`` 를 주입하지 않은 경우.
            CalculatorValidationError: 목표율이 0 이하이거나 존재하지 않는
                정책이 조회된 경우(계산기 검증 전파).
        """
        if self._policy_repository is None:
            raise ValueError(
                "build_summary_from_registered_targets 를 사용하려면 "
                "policy_repository 를 주입해야 합니다."
            )

        policies = [
            policy
            for policy in self._policy_repository.find_active()
            if policy.policy_id is not None
        ]

        target_rates: dict[int, Decimal] = {
            policy.policy_id: policy.target_rate
            for policy in policies
            if policy.policy_id is not None and policy.target_rate is not None
        }

        total_amount = self._calculator.calculate_total_purchase(period)
        # 목표율이 있는 정책만 계산 대상으로 넘긴다(기존 계산 경로 그대로).
        results = {
            result.policy_id: result
            for result in self._calculator.calculate_all(target_rates, period)
        }

        summaries: list[PolicySummary] = []
        for policy in policies:
            assert policy.policy_id is not None  # 위에서 필터링됨
            result = results.get(policy.policy_id)
            if result is None:
                # 목표율 미설정 — 계산기를 호출하지 않는다.
                summaries.append(self._to_unset_summary(policy, total_amount))
            else:
                summaries.append(
                    self._to_policy_summary(result, target_rates[policy.policy_id])
                )

        return DashboardSummary(
            total_purchase_amount=total_amount,
            policy_summaries=summaries,
            missing_resolution_date=self._missing_resolution_date(period),
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _missing_resolution_date(self, period: PeriodFilter | None) -> MissingResolutionDate:
        """결의일자가 없어 기간 산정에서 빠진 건수·금액을 셉니다.

        .. warning::
            ⛔ **계산에 쓰이지 않습니다.** 위에서 이미 산출한 전체 구매액·정책별
            달성률에 전혀 영향을 주지 않으며, 이 값을 더하거나 빼지 않습니다.

        .. note::
            **결의일자 기준 조회에서만 의미가 있습니다.** 지급일·계약일 기준으로
            연도를 나눌 때는 결의일자가 없어도 행이 빠지지 않으므로, 안내를
            띄우면 오히려 사실과 다릅니다. 그때는 "해당 없음" 을 반환합니다.

        .. note::
            **기간 조건을 넘기지 않습니다.** 이 행들은 결의일자가 없어서 빠진
            것이라, 같은 날짜로 기간을 걸면 정의상 하나도 남지 않습니다.
            :meth:`~procurement.database.purchase_repository.PurchaseRepository.count_missing_resolution_date`
            가 계산 대상과 **같은 배치 조건**으로 셉니다.
        """
        if self._purchase_repository is None:
            return NOT_APPLICABLE
        if period is None or period.date_field != RESOLUTION_DATE:
            return NOT_APPLICABLE
        count, amount = self._purchase_repository.count_missing_resolution_date()
        return MissingResolutionDate(applies=True, count=count, amount=amount)

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
    def _to_unset_summary(policy: Policy, total_amount: Decimal) -> PolicySummary:
        """목표율이 없는 정책의 요약을 만듭니다(계산 없음).

        계산기를 호출하지 않으므로 정책별 구매금액·달성률·부족률은 모두
        ``None`` 이며, ``0`` 과 구분됩니다(계산하지 않았음을 의미).
        """
        assert policy.policy_id is not None  # 호출부에서 보장
        return PolicySummary(
            policy_id=policy.policy_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            purchase_amount=None,
            total_purchase_amount=total_amount,
            target_rate=None,
            achievement_rate=None,
            shortage_rate=None,
            status=DashboardStatus.TARGET_RATE_NOT_SET,
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
