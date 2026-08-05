"""
DashboardDataService 테스트.

Calculator(실제 Repository + 격리 DB)를 조합하여 대시보드 요약이 올바르게
생성되는지 검증합니다. DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators import ProcurementAchievementCalculator
from procurement.calculators.procurement_achievement import CalculatorValidationError
from procurement.dashboard import DashboardDataService, DashboardStatus
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Policy, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    CompanyRepository(path).create_table()
    PolicyRepository(path).create_table()
    CertificationRepository(path).create_table()
    PurchaseRepository(path).create_table()
    return path


@pytest.fixture
def company_repo(db_path: Path) -> CompanyRepository:
    return CompanyRepository(db_path)


@pytest.fixture
def policy_repo(db_path: Path) -> PolicyRepository:
    return PolicyRepository(db_path)


@pytest.fixture
def certification_repo(db_path: Path) -> CertificationRepository:
    return CertificationRepository(db_path)


@pytest.fixture
def purchase_repo(db_path: Path) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def service(
    purchase_repo: PurchaseRepository,
    certification_repo: CertificationRepository,
    policy_repo: PolicyRepository,
) -> DashboardDataService:
    calculator = ProcurementAchievementCalculator(purchase_repo, certification_repo, policy_repo)
    return DashboardDataService(calculator)


# ----------------------------------------------------------------------
# 테스트 데이터 헬퍼
# ----------------------------------------------------------------------
def _add_company(repo: CompanyRepository, business_no: str) -> int:
    saved = repo.insert(
        Company(
            business_no=business_no,
            company_name=f"기업-{business_no}",
            representative_name="홍길동",
        )
    )
    assert saved.company_id is not None
    return saved.company_id


def _add_policy(repo: PolicyRepository, code: str, name: str) -> int:
    saved = repo.insert(Policy(policy_code=code, policy_name=name))
    assert saved.policy_id is not None
    return saved.policy_id


def _add_certification(repo: CertificationRepository, company_id: int, policy_id: int) -> None:
    repo.insert(
        Certification(
            company_id=company_id,
            policy_id=policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )


def _add_purchase(repo: PurchaseRepository, amount: str, company_id: int | None = None) -> None:
    repo.insert(
        Purchase(
            business_no="0000000000",
            company_id=company_id,
            company_name="공급업체",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 15),
            amount=Decimal(amount),
        )
    )


class TestBuildSummaryEmpty:
    """정책 목표가 없거나 데이터가 없는 경우를 검증합니다."""

    def test_empty_target_rates_returns_total_only(
        self,
        service: DashboardDataService,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """목표 입력이 비어도 전체 구매액은 집계되고 정책 요약은 빈 목록입니다."""
        _add_purchase(purchase_repo, "1000000", company_id=None)
        _add_purchase(purchase_repo, "2000000", company_id=1)
        summary = service.build_summary({})
        assert summary.total_purchase_amount == Decimal("3000000")
        assert summary.policy_summaries == []

    def test_no_data_returns_zero_total(self, service: DashboardDataService) -> None:
        summary = service.build_summary({})
        assert summary.total_purchase_amount == Decimal("0")
        assert summary.policy_summaries == []


class TestBuildSummaryFields:
    """정책 요약의 각 필드 값을 검증합니다."""

    def _setup(
        self,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> int:
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, "3000000", company_id=company_id)  # 정책 구매
        _add_purchase(purchase_repo, "7000000", company_id=None)  # 나머지
        return policy_id

    def test_summary_field_values(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """정책비율 30%, 목표 50% → 달성률 60%, 부족률 40%, 상태 부족."""
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        summary = service.build_summary({policy_id: Decimal("50")})

        assert len(summary.policy_summaries) == 1
        item = summary.policy_summaries[0]
        assert item.policy_id == policy_id
        assert item.policy_code == "SMALL_BUSINESS"
        assert item.policy_name == "중소기업"
        assert item.purchase_amount == Decimal("3000000")
        assert item.total_purchase_amount == Decimal("10000000")
        assert item.target_rate == Decimal("50")
        assert item.achievement_rate == Decimal("60.00")
        assert item.shortage_rate == Decimal("40.00")
        assert item.status is DashboardStatus.SHORTAGE

    def test_total_matches_summary_total(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        summary = service.build_summary({policy_id: Decimal("50")})
        assert summary.total_purchase_amount == Decimal("10000000")


class TestBuildSummaryStatusClassification:
    """상태(정상/주의/부족)와 부족률 분류를 검증합니다."""

    def _make_policy_with_ratio(
        self,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
        code: str,
        policy_amount: str,
        other_amount: str,
    ) -> int:
        company_id = _add_company(company_repo, code.ljust(10, "0")[:10])
        policy_id = _add_policy(policy_repo, code, code)
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, policy_amount, company_id=company_id)
        if other_amount != "0":
            _add_purchase(purchase_repo, other_amount, company_id=None)
        return policy_id

    def test_normal_status(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """정책비율 50%, 목표 50% → 달성률 100% → 정상, 부족률 0."""
        policy_id = self._make_policy_with_ratio(
            company_repo, policy_repo, certification_repo, purchase_repo, "P1", "5000000", "5000000"
        )
        item = service.build_summary({policy_id: Decimal("50")}).policy_summaries[0]
        assert item.achievement_rate == Decimal("100.00")
        assert item.shortage_rate == Decimal("0.00")
        assert item.status is DashboardStatus.NORMAL

    def test_warning_status(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """정책비율 45%, 목표 50% → 달성률 90% → 주의."""
        policy_id = self._make_policy_with_ratio(
            company_repo, policy_repo, certification_repo, purchase_repo, "P2", "4500000", "5500000"
        )
        item = service.build_summary({policy_id: Decimal("50")}).policy_summaries[0]
        assert item.achievement_rate == Decimal("90.00")
        assert item.shortage_rate == Decimal("10.00")
        assert item.status is DashboardStatus.WARNING

    def test_shortage_status(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """정책비율 30%, 목표 50% → 달성률 60% → 부족."""
        policy_id = self._make_policy_with_ratio(
            company_repo, policy_repo, certification_repo, purchase_repo, "P3", "3000000", "7000000"
        )
        item = service.build_summary({policy_id: Decimal("50")}).policy_summaries[0]
        assert item.achievement_rate == Decimal("60.00")
        assert item.status is DashboardStatus.SHORTAGE


class TestBuildSummaryMultiplePolicies:
    """여러 정책 요약이 함께 생성되는지 검증합니다."""

    def test_multiple_policies_share_total(
        self,
        service: DashboardDataService,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        small_company = _add_company(company_repo, "1000000001")
        woman_company = _add_company(company_repo, "1000000002")
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        woman_policy = _add_policy(policy_repo, "WOMAN", "여성기업")
        _add_certification(certification_repo, small_company, small_policy)
        _add_certification(certification_repo, woman_company, woman_policy)
        _add_purchase(purchase_repo, "5000000", company_id=small_company)
        _add_purchase(purchase_repo, "3000000", company_id=woman_company)

        summary = service.build_summary({small_policy: Decimal("50"), woman_policy: Decimal("30")})
        assert len(summary.policy_summaries) == 2
        by_code = {s.policy_code: s for s in summary.policy_summaries}
        assert {s.total_purchase_amount for s in summary.policy_summaries} == {Decimal("8000000")}
        assert by_code["SMALL_BUSINESS"].target_rate == Decimal("50")
        assert by_code["WOMAN"].target_rate == Decimal("30")


class TestBuildSummaryValidationPropagation:
    """계산기 검증 예외가 그대로 전파되는지 확인합니다."""

    def test_unknown_policy_raises(self, service: DashboardDataService) -> None:
        with pytest.raises(CalculatorValidationError):
            service.build_summary({99999: Decimal("50")})

    def test_invalid_target_rate_raises(
        self,
        service: DashboardDataService,
        policy_repo: PolicyRepository,
    ) -> None:
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        with pytest.raises(CalculatorValidationError):
            service.build_summary({policy_id: Decimal("0")})
