"""
procurement.dashboard.status_service

대시보드 화면 상단의 **데이터 적재 현황** 영역에 필요한 사실(fact)만 조회하는
서비스 계층입니다.

이 서비스는 달성률을 계산하지 않습니다. Calculator·Rule Engine 을 호출하지
않으며, 저장소에 실제로 무엇이 얼마나 들어 있는지만 집계합니다::

    DataStatusService → PurchaseRepository / CompanyRepository
                      / CertificationRepository / PolicyRepository → SQLite

.. note::
    **기간 필터는 적용하지 않습니다.** 연도별 집계·기간 조건은
    ``docs/PURCHASE_PERIOD_AND_DEDUP_SPEC.md`` 와 ISSUE26 Spec 의 승인 대상이며
    아직 확정되지 않았습니다(D-23 ~ D-27). 따라서 본 서비스가 돌려주는 값은
    **전체 데이터 기준**이며, 응답에도 그 사실을 명시합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository


@dataclass(frozen=True, kw_only=True)
class DataStatus:
    """저장소에 적재된 데이터 현황(DTO).

    모든 값은 **전체 데이터 기준**입니다(기간 필터 없음).

    Attributes:
        purchase_count: 적재된 구매 건수.
        purchase_total_amount: 적재된 구매금액 합계. 건수가 0 이면 ``0``.
        matched_purchase_count: 기업 매칭이 끝난 구매 건수(``company_id`` 존재).
        unmatched_purchase_count: 기업 매칭이 되지 않은 구매 건수.
        earliest_payment_date: 가장 이른 지급일. 데이터가 없으면 ``None``.
        latest_payment_date: 가장 늦은 지급일. 데이터가 없으면 ``None``.
        earliest_contract_date: 가장 이른 계약일. 데이터가 없으면 ``None``.
        latest_contract_date: 가장 늦은 계약일. 데이터가 없으면 ``None``.
        company_count: 적재된 기업 수.
        certification_count: 적재된 인증 건수.
        policy_count: 등록된 정책 수(비활성 포함).
        policy_with_target_rate_count: 목표율이 설정된 활성 정책 수.
        batch_count: 등록된 업로드 배치 수(대체된 배치 포함).
        active_batch_count: 계산에 사용되는 ACTIVE 배치 수.
        superseded_batch_count: 재업로드로 대체된 배치 수.
        calculation_target_count: **계산 대상** 구매 건수. 대체된 배치의 행을
            제외한 수이며, ``purchase_count`` 와 다르면 대체가 발생한 것입니다.
    """

    purchase_count: int
    purchase_total_amount: Decimal
    matched_purchase_count: int
    unmatched_purchase_count: int
    earliest_payment_date: date | None
    latest_payment_date: date | None
    earliest_contract_date: date | None
    latest_contract_date: date | None
    company_count: int
    certification_count: int
    policy_count: int
    policy_with_target_rate_count: int
    batch_count: int
    active_batch_count: int
    superseded_batch_count: int
    calculation_target_count: int


class DataStatusService:
    """저장소 적재 현황을 조회합니다(계산 없음)."""

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        company_repository: CompanyRepository,
        certification_repository: CertificationRepository,
        policy_repository: PolicyRepository,
        import_batch_repository: ImportBatchRepository,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            purchase_repository: 구매 저장소.
            company_repository: 기업 저장소.
            certification_repository: 인증 저장소.
            policy_repository: 정책 저장소.
            import_batch_repository: 업로드 배치 저장소.
        """
        self._purchase_repository = purchase_repository
        self._company_repository = company_repository
        self._certification_repository = certification_repository
        self._policy_repository = policy_repository
        self._import_batch_repository = import_batch_repository

    def build_status(self) -> DataStatus:
        """현재 적재 현황을 집계합니다.

        구매 데이터의 기간(최초·최종 일자)과 금액 합계는 저장소가 돌려준 목록을
        그대로 순회해 계산합니다. 저장소에 새 조회 메서드를 추가하지 않기 위한
        선택이며, 계산 로직(Calculator)과는 무관합니다.

        Returns:
            :class:`DataStatus`. 데이터가 하나도 없으면 건수는 ``0``,
            일자는 ``None`` 입니다.
        """
        purchases = self._purchase_repository.find_all()

        total_amount = Decimal("0")
        payment_dates: list[date] = []
        contract_dates: list[date] = []
        matched = 0
        for purchase in purchases:
            total_amount += purchase.amount
            # 🟢 STEP 87 — 두 날짜는 선택 항목이다. 값이 있는 것만 모은다.
            # ⛔ 없는 날짜를 다른 날짜로 채우지 않는다(범위가 거짓이 된다).
            if purchase.payment_date is not None:
                payment_dates.append(purchase.payment_date)
            if purchase.contract_date is not None:
                contract_dates.append(purchase.contract_date)
            if purchase.company_id is not None:
                matched += 1

        batches = self._import_batch_repository.find_all()
        active_batches = sum(1 for batch in batches if batch.is_active)

        policies = self._policy_repository.find_all()
        with_target_rate = sum(
            1 for policy in policies if policy.is_active and policy.target_rate is not None
        )

        return DataStatus(
            purchase_count=len(purchases),
            purchase_total_amount=total_amount,
            matched_purchase_count=matched,
            unmatched_purchase_count=len(purchases) - matched,
            earliest_payment_date=min(payment_dates) if payment_dates else None,
            latest_payment_date=max(payment_dates) if payment_dates else None,
            earliest_contract_date=min(contract_dates) if contract_dates else None,
            latest_contract_date=max(contract_dates) if contract_dates else None,
            company_count=self._company_repository.count(),
            certification_count=self._certification_repository.count(),
            policy_count=len(policies),
            policy_with_target_rate_count=with_target_rate,
            batch_count=len(batches),
            active_batch_count=active_batches,
            superseded_batch_count=len(batches) - active_batches,
            calculation_target_count=len(self._purchase_repository.find_for_calculation()),
        )
