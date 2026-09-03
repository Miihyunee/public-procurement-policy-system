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
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.policy import Policy
from procurement.models.purchase import Purchase

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
        policy_target_repository: PolicyTargetRepository | None = None,
        policy_company_source_repository: PolicyCompanySourceRepository | None = None,
        certification_repository: CertificationRepository | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            calculator: 달성률 계산에 사용할 :class:`ProcurementAchievementCalculator`.
            policy_repository: 활성 정책 **목록**을 조회할 :class:`PolicyRepository`.
                :meth:`build_summary_from_registered_targets` 를 사용할 때만
                필요하며, 외부 입력 방식(:meth:`build_summary`)만 사용할 경우
                생략할 수 있습니다.
            purchase_repository: **결의일자 미기재 건수**를 세는 데만 쓰는
                :class:`PurchaseRepository`. ⛔ 계산에는 쓰이지 않습니다 —
                달성률은 지금도 ``calculator`` 만 산출합니다. 생략하면 안내
                값이 "해당 없음" 으로 나갑니다.
            policy_target_repository: **연도별 목표비율**을 조회할
                :class:`PolicyTargetRepository`. 주입하면 목표비율을 여기서
                읽습니다(DECISIONS §0.20). 생략하면 예전처럼
                ``Policy.target_rate`` 를 읽습니다 — 기존 호출부를 깨지 않기
                위한 하위호환 경로이며, ⛔ 새 경로는 ``Policy.target_rate`` 를
                읽지 않습니다.
            policy_company_source_repository: **기업정보를 받은 적이 있는지**
                확인할 :class:`PolicyCompanySourceRepository`. 주입하면 등록되지
                않은 정책을 **조회불가**로 표시합니다(STEP 96 §8). 생략하면 그
                구분을 하지 않습니다(기존 호출부 하위호환).
            certification_repository: 인증이 있는 정책을 확인할
                :class:`CertificationRepository`. 등록 기록이 없어도 인증이
                이미 있으면 판정 가능한 것으로 봅니다 —
                :meth:`_registered_policy_ids` 참고. ⛔ 계산에는 쓰이지
                않습니다(계산은 그대로 ``calculator`` 가 합니다).
        """
        self._calculator = calculator
        self._policy_repository = policy_repository
        self._purchase_repository = purchase_repository
        self._policy_target_repository = policy_target_repository
        self._policy_company_source_repository = policy_company_source_repository
        self._certification_repository = certification_repository

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
        - **목표율이 없는 정책**: 실적과 분모는 그대로 채우고 **달성률만**
          ``None`` 으로 두며, 상태는 :attr:`DashboardStatus.TARGET_RATE_NOT_SET`
          입니다(STEP 97 §13). 누가 해당하는지는 알기 때문에 실적은 셀 수
          있습니다. **요약에서 제외하지 않습니다.**
        - **기업정보가 등록되지 않은 정책**: 누가 해당하는지 모르므로 실적까지
          ``None`` 이며, 상태는
          :attr:`DashboardStatus.COMPANY_DATA_NOT_REGISTERED` 입니다(STEP 96 §8).

        정책을 제외하지 않는 이유는, 화면에서 "정책이 없음" · "기업정보를 아직
        받지 못함" · "목표율이 아직 등록되지 않음" 을 구분하기 위해서입니다.
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

        target_rates = self._resolve_target_rates(policies, period)

        total_amount = self._calculator.calculate_total_purchase(period)
        # 목표율이 있는 정책만 계산 대상으로 넘긴다(기존 계산 경로 그대로).
        results = {
            result.policy_id: result
            for result in self._calculator.calculate_all(target_rates, period)
        }

        registered = self._registered_policy_ids()
        on_hold = self._on_hold_policy_ids(period)

        summaries: list[PolicySummary] = []
        for policy in policies:
            assert policy.policy_id is not None  # 위에서 필터링됨
            if registered is not None and policy.policy_id not in registered:
                # ⛔ 기업정보를 받은 적이 없다 → **조회불가**. 미해당도 0원도 아니다.
                summaries.append(self._to_not_registered_summary(policy, total_amount))
                continue
            result = results.get(policy.policy_id)
            if result is None:
                # 목표가 저장돼 있는데 계산기에 오지 않았다면, 분모를 못 구하는
                # 것이다 → **계산 보류**. ⛔ "목표율 미설정" 이라고 말하면 거짓이다.
                status = (
                    DashboardStatus.CALCULATION_ON_HOLD
                    if policy.policy_id in on_hold
                    else DashboardStatus.TARGET_RATE_NOT_SET
                )
                summaries.append(
                    self._to_uncalculated_summary(policy, total_amount, period, status)
                )
            else:
                summaries.append(self._to_policy_summary(result, target_rates[policy.policy_id]))

        return DashboardSummary(
            total_purchase_amount=total_amount,
            policy_summaries=summaries,
            missing_resolution_date=self._missing_resolution_date(period),
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _resolve_target_rates(
        self, policies: list[Policy], period: PeriodFilter | None
    ) -> dict[int, Decimal]:
        """계산기에 넘길 ``{policy_id: 목표비율}`` 을 만듭니다.

        **연도별 목표비율이 정본입니다**(DECISIONS §0.20).
        :class:`PolicyTargetRepository` 가 주입되어 있고 대상 연도를 알 수 있으면
        그 연도의 값만 씁니다.

        .. warning::
            ⛔ **연도끼리 값을 빌려오지 않습니다.** 2026년 목표가 없으면 2025년
            값을 끌어다 쓰지 않고 **미설정**입니다. ⛔ 0 으로 대체하지도, 예전
            ``Policy.target_rate`` 로 메우지도 않습니다 — 그렇게 하면 "설정하지
            않았다" 와 "설정했다" 를 구분할 수 없습니다.

        .. note::
            ``policy_target_repository`` 를 주입하지 않은 호출부는 예전처럼
            ``Policy.target_rate`` 를 씁니다. 기존 코드를 깨지 않기 위한
            하위호환 경로입니다.

        Args:
            policies: 활성 정책 목록.
            period: 적용할 기간 조건. 연도는 ``period.start.year`` 로 읽습니다 —
                API 는 언제나 :meth:`PeriodFilter.for_year` 로 만들기 때문입니다.

        Returns:
            목표비율이 **설정된 정책만** 담긴 매핑.
        """
        if self._policy_target_repository is None or period is None:
            # 하위호환 경로 — 연도 축이 없던 시절의 값.
            return {
                policy.policy_id: policy.target_rate
                for policy in policies
                if policy.policy_id is not None and policy.target_rate is not None
            }

        active_ids = {policy.policy_id for policy in policies if policy.policy_id is not None}
        registered = self._policy_target_repository.rates_by_policy_id(period.start.year)
        # 비활성 정책의 목표비율이 남아 있어도 계산 대상에 넣지 않는다.
        return {
            policy_id: rate for policy_id, rate in registered.items() if policy_id in active_ids
        }

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

    def list_missing_resolution_date(self, period: PeriodFilter | None = None) -> list[Purchase]:
        """결의일자가 없어 기간 산정에서 빠진 구매를 **행 단위로** 돌려줍니다.

        :meth:`_missing_resolution_date` 가 세는 것과 **같은 조건·같은 모집단**
        입니다. 화면이 "N건" 만 보여 주면 담당자는 어떤 행인지 알 수 없어
        무엇을 확인해야 할지 판단할 수 없으므로, 같은 사실을 행으로 펼칩니다.

        .. warning::
            ⛔ **조회 전용입니다.** 달성률 계산 경로를 거치지 않으며, 어떤 행도
            수정하지 않습니다. 결의일자를 채우거나 다른 날짜로 대체하지 않습니다.

        Args:
            period: 지금 화면이 보고 있는 기간 조건. **범위 조건으로 쓰지
                않습니다** — 결의일자 기준 조회인지 판단하는 데만 씁니다.
                결의일자 기준이 아니거나 ``None`` 이면 **빈 목록**입니다
                (안내 자체가 해당되지 않는 조회이기 때문입니다).

        Returns:
            :class:`Purchase` 목록(``purchase_id`` 오름차순). 없으면 빈 목록.
        """
        if self._purchase_repository is None:
            return []
        if period is None or period.date_field != RESOLUTION_DATE:
            return []
        return self._purchase_repository.find_missing_resolution_date()

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

    def _registered_policy_ids(self) -> set[int] | None:
        """**판정할 근거가 있는** 정책 ID.

        근거는 둘 중 하나면 됩니다.

        1. **등록 기록이 있다** — 그 정책의 목록을 받았다.
           목록이 비어 있었어도(우리 거래처가 한 곳도 없어도) 근거는 있습니다.
           그것은 "모른다" 가 아니라 **"전부 미해당"** 이기 때문입니다.
        2. **인증이 한 건이라도 있다** — 어떤 경로로든 그 정책의 기업 정보가
           들어와 있다는 뜻입니다.

        ⭐ 둘을 합치는 이유: 인증이 저장되어 있는데 등록 기록이 없다고 해서
        "판단할 수 없다" 고 말하면 **거짓말**이 됩니다. 이미 알고 있는 것이
        있으니까요.

        Returns:
            판정 가능한 정책 ID 집합. 저장소를 주입하지 않았으면 ``None`` 이며,
            그 경우 조회불가 구분을 하지 않습니다(기존 호출부 하위호환).
        """
        if self._policy_company_source_repository is None:
            return None
        registered = self._policy_company_source_repository.registered_policy_ids()
        if self._certification_repository is not None:
            registered |= self._certification_repository.policy_ids_with_certifications()
        return registered

    @staticmethod
    def _to_not_registered_summary(policy: Policy, total_amount: Decimal) -> PolicySummary:
        """기업정보를 받은 적이 없는 정책의 요약을 만듭니다 — **조회불가**.

        .. warning::
            ⛔ **미해당이 아닙니다.** 어떤 사업자가 이 정책의 기업인지 모르므로
            실적을 셀 수 없습니다. 금액·비율·달성률을 모두 ``None`` 으로 두어
            **0 과 구분**합니다(STEP 96 §8 · §22-7·8).

        ⛔ 계산기를 호출하지 않습니다 — 인증이 0건이라 0 이 나오는데, 그 0 을
        보여주면 "해당 기업이 없다" 로 읽히기 때문입니다.
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
            status=DashboardStatus.COMPANY_DATA_NOT_REGISTERED,
        )

    def _on_hold_policy_ids(self, period: PeriodFilter | None) -> set[int]:
        """목표는 저장돼 있으나 **분모를 구할 수 없는** 정책 ID(STEP 99 §1).

        여성기업(구매유형별)과 자활용사촌(생산가능품목)이 여기에 해당합니다.
        목표비율 저장소가 없거나 대상 연도를 모르면 빈 집합입니다.
        """
        if self._policy_target_repository is None or period is None:
            return set()
        return self._policy_target_repository.on_hold_policy_ids(period.start.year)

    def _to_uncalculated_summary(
        self,
        policy: Policy,
        total_amount: Decimal,
        period: PeriodFilter | None,
        status: DashboardStatus,
    ) -> PolicySummary:
        """달성률을 내지 못한 정책의 요약을 만듭니다.

        ⚠️ **STEP 97 §13 — 실적과 구매비율은 보여 줍니다.** 기업정보가 등록되어
        있으면 누가 해당하는지 알 수 있으므로 **실적은 셀 수 있습니다.** 못 내는
        것은 **달성률뿐**입니다.

        ========================  ============================================
        기업정보 미등록(조회불가)    실적도 모른다 → 전부 ``None``
        목표율 미설정              목표가 없다 → 금액은 채우고 달성률만 ``None``
        계산 보류                  목표는 있으나 **분모**를 못 구한다 → 위와 같음
        ========================  ============================================

        ⭐ 뒤의 두 가지는 화면에 같은 숫자를 보여 주지만 **뜻이 다릅니다.** 하나는
        "목표를 아직 못 받았다", 다른 하나는 "목표는 받았는데 잴 기준이 없다"
        입니다. 그래서 상태를 호출부에서 받아 그대로 씁니다.

        ⛔ 달성률·부족률은 ``None`` 으로 두어 ``0`` 과 구분합니다.
        """
        assert policy.policy_id is not None  # 호출부에서 보장
        return PolicySummary(
            policy_id=policy.policy_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            # ⭐ 목표가 없어도 실적은 센다(§13). 계산기는 목표 없이도 금액을 낸다.
            purchase_amount=self._calculator.calculate_policy_purchase(policy.policy_id, period),
            total_purchase_amount=total_amount,
            target_rate=None,
            achievement_rate=None,
            shortage_rate=None,
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
