"""
procurement.calculators.procurement_achievement

기관의 우선구매 정책 달성률을 계산하는 Calculator 서비스입니다.

Dashboard 가 사용할 계산 기반(Service Layer)으로, Repository 를 주입받아
구매실적·인증·정책 데이터를 조합합니다. SQL 을 직접 작성하지 않고 Repository
메서드만 사용합니다.

계산 흐름:
    Purchase (구매실적)
        └─ company_id ─┐
    Certification (정책 인증) ─ policy_id ─ Policy
        → 정책별 구매금액 / 전체 구매금액 / 목표 대비 달성률

사용 예:
    from procurement.calculators import ProcurementAchievementCalculator

    calculator = ProcurementAchievementCalculator(
        purchase_repository, certification_repository, policy_repository
    )
    results = calculator.calculate_all({small_biz_policy_id: Decimal("50")})

.. note::
    Dashboard 화면·Chart·API 는 이번 범위에 포함하지 않습니다.
    목표율(``target_rate``)은 DB 로 관리하지 않고 호출 시 입력값으로 받습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from procurement.calculators.achievement_result import AchievementResult
from procurement.calculators.rules import (
    RuleContext,
    RuleRegistry,
    build_default_registry,
)
from procurement.core.period import PeriodFilter
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.policy import Policy

#: 달성률 표기 자리수 (소수점 둘째 자리)
_RATE_EXPONENT = Decimal("0.01")

#: 비율을 백분율로 변환할 때 사용하는 계수
_PERCENT = Decimal("100")


class CalculatorValidationError(ValueError):
    """목표율 오류·존재하지 않는 정책 등 계산 입력 검증 실패 시 발생하는 예외."""


class ProcurementAchievementCalculator:
    """구매실적을 기반으로 정책별 우선구매 달성률을 계산합니다."""

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        certification_repository: CertificationRepository,
        policy_repository: PolicyRepository,
        rule_registry: RuleRegistry | None = None,
    ) -> None:
        """Calculator 를 초기화합니다.

        Args:
            purchase_repository: 구매실적 조회에 사용할 :class:`PurchaseRepository`.
            certification_repository: 인증 조회에 사용할 :class:`CertificationRepository`.
            policy_repository: 정책 조회에 사용할 :class:`PolicyRepository`.
            rule_registry: 정책 판정 규칙 레지스트리. 생략 시 기본 레지스트리
                (:func:`build_default_registry`)를 사용하며, 기본 동작은
                지급일/계약일 기준 판정으로 기존과 동일합니다.
        """
        self._purchase_repository = purchase_repository
        self._certification_repository = certification_repository
        self._policy_repository = policy_repository
        self._rule_registry = rule_registry or build_default_registry()

    def calculate_total_purchase(self, period: PeriodFilter | None = None) -> Decimal:
        """기관 전체 구매금액을 합산합니다.

        기업 매칭 여부와 무관하게 모든 구매실적을 포함합니다. 대체된 배치의
        행은 제외됩니다(Repository 조회 단계에서 처리).

        Args:
            period: 적용할 기간 조건. ``None`` 이면 기간 제한 없이 전체를
                합산합니다(기존 동작과 동일).

        Returns:
            전체 구매금액. 구매실적이 없으면 ``Decimal("0")``.
        """
        total = Decimal("0")
        for purchase in self._purchase_repository.find_for_calculation(period):
            total += purchase.amount
        return total

    def calculate_policy_purchase(
        self, policy_id: int, period: PeriodFilter | None = None
    ) -> Decimal:
        """해당 정책의 실제 업무 규칙을 적용해 정책별 구매금액을 합산합니다.

        정책의 ``evaluation_basis`` 에 따라 판정 기준일을 선택합니다.

        - ``PAYMENT_DATE`` → 구매의 대금 지급일(``payment_date``)
        - ``CONTRACT_DATE`` → 구매의 계약일(``contract_date``)

        선택한 기준일이 해당 기업의 정책 인증 유효기간
        (``valid_from <= 기준일 <= valid_to``, 경계 포함) 내에 있는 구매만
        정책 실적으로 인정합니다. ``company_id`` 가 없는(미매칭) 구매는 제외됩니다.

        Args:
            policy_id: 집계할 정책 ID.
            period: 적용할 기간 조건. ``None`` 이면 기간 제한 없음.

        Returns:
            정책별 구매금액. 대상이 없으면 ``Decimal("0")``.

        Raises:
            CalculatorValidationError: 존재하지 않는 정책인 경우.
        """
        self._get_policy(policy_id)
        return self._sum_policy_purchase(policy_id, period)

    def calculate_achievement(
        self,
        policy_id: int,
        target_rate: Decimal,
        period: PeriodFilter | None = None,
    ) -> AchievementResult:
        """정책 하나의 목표 대비 달성률을 계산합니다.

        정책 구매비율(정책 구매금액 / 전체 구매금액)을 목표율과 비교합니다::

            달성률(%) = (정책 구매금액 / 전체 구매금액) / 목표율 × 100 × 100

        Args:
            policy_id: 계산할 정책 ID.
            target_rate: 목표 구매비율(퍼센트 단위. 예: ``Decimal("50")`` 은 50%).
            period: 적용할 기간 조건. 분모·분자에 **동일하게** 적용됩니다.
                ``None`` 이면 기간 제한 없음.

        Returns:
            계산 결과 :class:`AchievementResult`.

        Raises:
            CalculatorValidationError: ``target_rate`` 가 0 이하이거나
                존재하지 않는 정책인 경우.
        """
        policy = self._get_policy(policy_id)
        self._validate_target_rate(target_rate)

        total_amount = self.calculate_total_purchase(period)
        policy_amount = self._sum_policy_purchase(policy_id, period)

        return self._build_result(policy, policy_amount, total_amount, target_rate)

    def calculate_all(
        self, target_rates: dict[int, Decimal], period: PeriodFilter | None = None
    ) -> list[AchievementResult]:
        """여러 정책의 달성률을 한 번에 계산합니다.

        전체 구매금액은 한 번만 집계하여 모든 결과가 동일한 분모를 공유합니다.

        Args:
            target_rates: ``{policy_id: 목표율}`` 형태의 매핑.
            period: 적용할 기간 조건. 분모·분자에 **동일하게** 적용됩니다.
                ``None`` 이면 기간 제한 없음.

        Returns:
            :class:`AchievementResult` 목록. 입력이 비어 있으면 빈 목록.

        Raises:
            CalculatorValidationError: 목표율이 0 이하이거나
                존재하지 않는 정책이 포함된 경우.
        """
        policies: list[tuple[Policy, Decimal]] = []
        for policy_id, target_rate in target_rates.items():
            policy = self._get_policy(policy_id)
            self._validate_target_rate(target_rate)
            policies.append((policy, target_rate))

        if not policies:
            return []

        total_amount = self.calculate_total_purchase(period)

        results: list[AchievementResult] = []
        for policy, target_rate in policies:
            assert policy.policy_id is not None  # _get_policy 가 보장
            policy_amount = self._sum_policy_purchase(policy.policy_id, period)
            results.append(self._build_result(policy, policy_amount, total_amount, target_rate))
        return results

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _get_policy(self, policy_id: int) -> Policy:
        """정책을 조회하고 존재하지 않으면 예외를 발생시킵니다."""
        policy = self._policy_repository.find_by_id(policy_id)
        if policy is None or policy.policy_id is None:
            raise CalculatorValidationError(f"존재하지 않는 정책입니다: policy_id={policy_id}")
        return policy

    @staticmethod
    def _validate_target_rate(target_rate: Decimal) -> None:
        """목표율이 유효한지 검증합니다."""
        if target_rate <= 0:
            raise CalculatorValidationError(
                f"목표율은 0 보다 커야 합니다: target_rate={target_rate}"
            )

    def _sum_policy_purchase(
        self, policy_id: int, period: PeriodFilter | None = None
    ) -> Decimal:
        """정책 인증기업의 구매금액을 업무 규칙에 따라 합산합니다 (정책 존재 검증 없음).

        정책의 ``evaluation_basis`` 에 따라 판정 기준일(지급일 또는 계약일)을
        선택하고, 그 기준일이 해당 기업의 인증 유효기간(``valid_from`` ~
        ``valid_to``, 경계 포함) 내에 있는 구매만 합산합니다. ``company_id`` 가
        없는(미매칭) 구매는 제외됩니다.

        한 기업이 같은 정책 인증을 여러 건 보유한 경우, 그중 하나라도 유효기간을
        만족하면 해당 구매를 인정합니다.

        판정 기준일 선택과 유효기간 판정은 정책의 ``evaluation_basis`` 에 매핑된
        :class:`~procurement.calculators.rules.PolicyRule` 이 담당하며, 계산기는
        규칙이 인정한 구매의 금액만 합산합니다.
        """
        policy = self._policy_repository.find_by_id(policy_id)
        if policy is None:
            return Decimal("0")

        # company_id -> 인증 유효기간(valid_from, valid_to) 목록
        validity_ranges: dict[int, list[tuple[date, date]]] = {}
        for certification in self._certification_repository.find_by_policy(policy_id):
            if certification.company_id is None:
                continue
            validity_ranges.setdefault(certification.company_id, []).append(
                (certification.valid_from, certification.valid_to)
            )
        if not validity_ranges:
            return Decimal("0")

        rule = self._rule_registry.get(policy.evaluation_basis)

        total = Decimal("0")
        for purchase in self._purchase_repository.find_for_calculation(period):
            company_id = purchase.company_id
            if company_id is None or company_id not in validity_ranges:
                continue
            context = RuleContext(purchase=purchase, validity_ranges=validity_ranges[company_id])
            if rule.matches(context):
                total += purchase.amount
        return total

    def _build_result(
        self,
        policy: Policy,
        policy_amount: Decimal,
        total_amount: Decimal,
        target_rate: Decimal,
    ) -> AchievementResult:
        """계산 결과 객체를 생성합니다."""
        assert policy.policy_id is not None  # _get_policy 가 보장
        return AchievementResult(
            policy_id=policy.policy_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            purchase_amount=policy_amount,
            total_purchase_amount=total_amount,
            achievement_rate=self._achievement_rate(policy_amount, total_amount, target_rate),
        )

    @staticmethod
    def _achievement_rate(
        policy_amount: Decimal, total_amount: Decimal, target_rate: Decimal
    ) -> Decimal:
        """목표 대비 달성률(%)을 계산합니다.

        전체 구매금액이 0 이면 0 으로 나누지 않고 ``Decimal("0")`` 을 반환합니다.
        """
        if total_amount == 0:
            return Decimal("0")

        purchase_rate = policy_amount / total_amount * _PERCENT
        rate = purchase_rate / target_rate * _PERCENT
        return rate.quantize(_RATE_EXPONENT, rounding=ROUND_HALF_UP)
