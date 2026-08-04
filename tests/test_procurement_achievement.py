"""
ProcurementAchievementCalculator 테스트.

전체/정책별 구매금액 집계와 목표 대비 달성률 계산을 검증합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators import (
    AchievementResult,
    CalculatorValidationError,
    ProcurementAchievementCalculator,
)
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Policy, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """필요한 테이블이 모두 생성된 DB 파일 경로를 반환합니다."""
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
def calculator(
    purchase_repo: PurchaseRepository,
    certification_repo: CertificationRepository,
    policy_repo: PolicyRepository,
) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(purchase_repo, certification_repo, policy_repo)


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


def _add_policy(
    repo: PolicyRepository,
    code: str,
    name: str,
    evaluation_basis: str = "PAYMENT_DATE",
) -> int:
    saved = repo.insert(
        Policy(policy_code=code, policy_name=name, evaluation_basis=evaluation_basis)
    )
    assert saved.policy_id is not None
    return saved.policy_id


def _add_certification(
    repo: CertificationRepository,
    company_id: int,
    policy_id: int,
    valid_from: date = date(2026, 1, 1),
    valid_to: date = date(2026, 12, 31),
) -> None:
    repo.insert(
        Certification(
            company_id=company_id,
            policy_id=policy_id,
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )


def _add_purchase(
    repo: PurchaseRepository,
    amount: str,
    company_id: int | None = None,
    payment_date: date = date(2026, 3, 15),
    contract_date: date = date(2026, 3, 1),
) -> None:
    repo.insert(
        Purchase(
            business_no="0000000000",
            company_id=company_id,
            company_name="공급업체",
            contract_date=contract_date,
            payment_date=payment_date,
            amount=Decimal(amount),
        )
    )


class TestCalculateTotalPurchase:
    """전체 구매금액 집계를 검증합니다."""

    def test_returns_zero_when_no_purchase(
        self, calculator: ProcurementAchievementCalculator
    ) -> None:
        assert calculator.calculate_total_purchase() == Decimal("0")

    def test_single_purchase(
        self,
        calculator: ProcurementAchievementCalculator,
        purchase_repo: PurchaseRepository,
    ) -> None:
        _add_purchase(purchase_repo, "1000000")
        assert calculator.calculate_total_purchase() == Decimal("1000000")

    def test_sums_multiple_purchases(
        self,
        calculator: ProcurementAchievementCalculator,
        purchase_repo: PurchaseRepository,
    ) -> None:
        _add_purchase(purchase_repo, "1000000")
        _add_purchase(purchase_repo, "2500000")
        _add_purchase(purchase_repo, "500000")
        assert calculator.calculate_total_purchase() == Decimal("4000000")

    def test_returns_decimal(
        self,
        calculator: ProcurementAchievementCalculator,
        purchase_repo: PurchaseRepository,
    ) -> None:
        _add_purchase(purchase_repo, "1000")
        assert isinstance(calculator.calculate_total_purchase(), Decimal)

    def test_decimal_precision_is_exact(
        self,
        calculator: ProcurementAchievementCalculator,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """소수 금액 합산이 부동소수 오차 없이 정확해야 합니다."""
        _add_purchase(purchase_repo, "0.10")
        _add_purchase(purchase_repo, "0.20")
        assert calculator.calculate_total_purchase() == Decimal("0.30")

    def test_includes_unmatched_purchases(
        self,
        calculator: ProcurementAchievementCalculator,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """전체 구매금액은 기업 매칭 여부와 무관하게 모든 구매를 포함합니다."""
        _add_purchase(purchase_repo, "1000000", company_id=None)
        _add_purchase(purchase_repo, "2000000", company_id=1)
        assert calculator.calculate_total_purchase() == Decimal("3000000")


class TestCalculatePolicyPurchase:
    """정책별 구매금액 집계를 검증합니다."""

    def test_returns_zero_when_no_certification(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_purchase(purchase_repo, "1000000", company_id=1)
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")

    def test_returns_zero_when_no_purchase(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
    ) -> None:
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")

    def test_sums_certified_company_purchases(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, "1000000", company_id=company_id)
        _add_purchase(purchase_repo, "500000", company_id=company_id)
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1500000")

    def test_excludes_other_policy_companies(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """다른 정책 인증만 보유한 기업의 구매는 제외됩니다."""
        small_company = _add_company(company_repo, "1000000001")
        woman_company = _add_company(company_repo, "1000000002")
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        woman_policy = _add_policy(policy_repo, "WOMAN", "여성기업")
        _add_certification(certification_repo, small_company, small_policy)
        _add_certification(certification_repo, woman_company, woman_policy)
        _add_purchase(purchase_repo, "1000000", company_id=small_company)
        _add_purchase(purchase_repo, "9000000", company_id=woman_company)

        assert calculator.calculate_policy_purchase(small_policy) == Decimal("1000000")
        assert calculator.calculate_policy_purchase(woman_policy) == Decimal("9000000")

    def test_excludes_unmatched_purchases(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """company_id 가 없는(미매칭) 구매는 정책 실적에 포함되지 않습니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, "1000000", company_id=company_id)
        _add_purchase(purchase_repo, "7000000", company_id=None)
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")

    def test_company_with_multiple_certifications_counted_once(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """같은 정책 인증을 중복 보유해도 구매금액이 중복 합산되지 않아야 합니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, "1000000", company_id=company_id)
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")

    def test_company_certified_for_two_policies(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """한 기업이 두 정책 인증을 보유하면 두 정책 모두에 집계됩니다."""
        company_id = _add_company(company_repo, "1000000001")
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        woman_policy = _add_policy(policy_repo, "WOMAN", "여성기업")
        _add_certification(certification_repo, company_id, small_policy)
        _add_certification(certification_repo, company_id, woman_policy)
        _add_purchase(purchase_repo, "1000000", company_id=company_id)

        assert calculator.calculate_policy_purchase(small_policy) == Decimal("1000000")
        assert calculator.calculate_policy_purchase(woman_policy) == Decimal("1000000")

    def test_raises_for_unknown_policy(self, calculator: ProcurementAchievementCalculator) -> None:
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_policy_purchase(99999)


class TestCalculateAchievement:
    """목표 대비 달성률 계산을 검증합니다."""

    def _setup(
        self,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
        policy_amount: str = "3000000",
        other_amount: str = "7000000",
    ) -> int:
        """정책 구매 policy_amount / 전체 (policy_amount + other_amount) 구성."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        _add_certification(certification_repo, company_id, policy_id)
        _add_purchase(purchase_repo, policy_amount, company_id=company_id)
        _add_purchase(purchase_repo, other_amount, company_id=None)
        return policy_id

    def test_returns_achievement_result(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        result = calculator.calculate_achievement(policy_id, Decimal("50"))
        assert isinstance(result, AchievementResult)

    def test_result_fields(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        result = calculator.calculate_achievement(policy_id, Decimal("50"))

        assert result.policy_id == policy_id
        assert result.policy_code == "SMALL_BUSINESS"
        assert result.policy_name == "중소기업"
        assert result.purchase_amount == Decimal("3000000")
        assert result.total_purchase_amount == Decimal("10000000")

    def test_achievement_rate_against_target(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """정책 구매비율 30%, 목표 50% → 목표 대비 달성률 60%."""
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        result = calculator.calculate_achievement(policy_id, Decimal("50"))
        assert result.achievement_rate == Decimal("60.00")

    def test_target_rate_changes_result(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """목표율이 낮아지면 달성률이 올라갑니다 (목표율 반영 확인)."""
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        assert calculator.calculate_achievement(
            policy_id, Decimal("30")
        ).achievement_rate == Decimal("100.00")
        assert calculator.calculate_achievement(
            policy_id, Decimal("10")
        ).achievement_rate == Decimal("300.00")

    def test_achievement_rate_is_decimal(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        policy_id = self._setup(company_repo, policy_repo, certification_repo, purchase_repo)
        result = calculator.calculate_achievement(policy_id, Decimal("50"))
        assert isinstance(result.achievement_rate, Decimal)

    def test_decimal_accuracy_on_repeating_fraction(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """1/3 처럼 순환소수가 나와도 예외 없이 2자리로 반올림됩니다."""
        policy_id = self._setup(
            company_repo,
            policy_repo,
            certification_repo,
            purchase_repo,
            policy_amount="1000000",
            other_amount="2000000",
        )
        # 정책비율 = 1/3 = 33.333...%, 목표 100% → 33.33%
        result = calculator.calculate_achievement(policy_id, Decimal("100"))
        assert result.achievement_rate == Decimal("33.33")

    def test_zero_total_returns_zero_rate(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
    ) -> None:
        """구매 데이터가 없으면 0으로 나누지 않고 달성률 0 을 반환합니다."""
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        result = calculator.calculate_achievement(policy_id, Decimal("50"))
        assert result.achievement_rate == Decimal("0")
        assert result.purchase_amount == Decimal("0")
        assert result.total_purchase_amount == Decimal("0")

    def test_raises_for_zero_target_rate(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
    ) -> None:
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_achievement(policy_id, Decimal("0"))

    def test_raises_for_negative_target_rate(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
    ) -> None:
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_achievement(policy_id, Decimal("-10"))

    def test_raises_for_unknown_policy(self, calculator: ProcurementAchievementCalculator) -> None:
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_achievement(99999, Decimal("50"))


class TestCalculateAll:
    """여러 정책 일괄 계산을 검증합니다."""

    def test_returns_empty_list_for_empty_target_rates(
        self, calculator: ProcurementAchievementCalculator
    ) -> None:
        assert calculator.calculate_all({}) == []

    def test_calculates_three_policies(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """중소기업 / 여성기업 / 장애인기업 3개 정책을 한 번에 계산합니다."""
        small_company = _add_company(company_repo, "1000000001")
        woman_company = _add_company(company_repo, "1000000002")
        disabled_company = _add_company(company_repo, "1000000003")

        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        woman_policy = _add_policy(policy_repo, "WOMAN", "여성기업")
        disabled_policy = _add_policy(policy_repo, "DISABLED", "장애인기업")

        _add_certification(certification_repo, small_company, small_policy)
        _add_certification(certification_repo, woman_company, woman_policy)
        _add_certification(certification_repo, disabled_company, disabled_policy)

        _add_purchase(purchase_repo, "5000000", company_id=small_company)
        _add_purchase(purchase_repo, "3000000", company_id=woman_company)
        _add_purchase(purchase_repo, "2000000", company_id=disabled_company)

        results = calculator.calculate_all(
            {
                small_policy: Decimal("50"),
                woman_policy: Decimal("30"),
                disabled_policy: Decimal("10"),
            }
        )

        assert len(results) == 3
        by_code = {r.policy_code: r for r in results}

        # 전체 1,000만 기준
        assert by_code["SMALL_BUSINESS"].purchase_amount == Decimal("5000000")
        assert by_code["SMALL_BUSINESS"].achievement_rate == Decimal("100.00")
        assert by_code["WOMAN"].purchase_amount == Decimal("3000000")
        assert by_code["WOMAN"].achievement_rate == Decimal("100.00")
        assert by_code["DISABLED"].purchase_amount == Decimal("2000000")
        assert by_code["DISABLED"].achievement_rate == Decimal("200.00")

    def test_all_results_share_same_total(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        company_id = _add_company(company_repo, "1000000001")
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        woman_policy = _add_policy(policy_repo, "WOMAN", "여성기업")
        _add_certification(certification_repo, company_id, small_policy)
        _add_purchase(purchase_repo, "4000000", company_id=company_id)

        results = calculator.calculate_all(
            {small_policy: Decimal("50"), woman_policy: Decimal("50")}
        )
        assert {r.total_purchase_amount for r in results} == {Decimal("4000000")}

    def test_policy_without_certification_gets_zero(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """인증 기업이 없는 정책은 실적 0 · 달성률 0 으로 보고됩니다."""
        company_id = _add_company(company_repo, "1000000001")
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        green_policy = _add_policy(policy_repo, "GREEN", "녹색제품")
        _add_certification(certification_repo, company_id, small_policy)
        _add_purchase(purchase_repo, "1000000", company_id=company_id)

        results = calculator.calculate_all(
            {small_policy: Decimal("50"), green_policy: Decimal("20")}
        )
        by_code = {r.policy_code: r for r in results}
        assert by_code["GREEN"].purchase_amount == Decimal("0")
        assert by_code["GREEN"].achievement_rate == Decimal("0.00")

    def test_returns_zero_rates_when_no_data(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
    ) -> None:
        """Purchase / Certification 이 전혀 없어도 예외 없이 0 을 반환합니다."""
        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        results = calculator.calculate_all({small_policy: Decimal("50")})
        assert len(results) == 1
        assert results[0].achievement_rate == Decimal("0")

    def test_raises_for_unknown_policy(self, calculator: ProcurementAchievementCalculator) -> None:
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_all({99999: Decimal("50")})

    def test_raises_for_invalid_target_rate(
        self,
        calculator: ProcurementAchievementCalculator,
        policy_repo: PolicyRepository,
    ) -> None:
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업")
        with pytest.raises(CalculatorValidationError):
            calculator.calculate_all({policy_id: Decimal("0")})


class TestPurchaseRepositoryFindAll:
    """Calculator 가 사용하는 find_all() 을 검증합니다."""

    def test_returns_empty_when_no_rows(self, purchase_repo: PurchaseRepository) -> None:
        assert purchase_repo.find_all() == []

    def test_returns_all_rows_ordered(self, purchase_repo: PurchaseRepository) -> None:
        _add_purchase(purchase_repo, "100")
        _add_purchase(purchase_repo, "200", company_id=1)
        _add_purchase(purchase_repo, "300")

        rows = purchase_repo.find_all()
        assert len(rows) == 3
        assert [p.purchase_id for p in rows] == [1, 2, 3]
        assert [p.amount for p in rows] == [
            Decimal("100"),
            Decimal("200"),
            Decimal("300"),
        ]


class TestBusinessRuleBasisSelection:
    """evaluation_basis 에 따른 판정 기준일 선택을 검증합니다."""

    def test_payment_date_basis_uses_payment_date(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """PAYMENT_DATE 정책은 지급일을 기준으로 판정합니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        # 지급일은 유효기간 내(인정), 계약일은 유효기간 밖 → 지급일 기준이면 인정
        _add_purchase(
            purchase_repo,
            "1000000",
            company_id=company_id,
            payment_date=date(2026, 3, 15),
            contract_date=date(2025, 12, 1),
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")

    def test_payment_date_basis_ignores_contract_date(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """PAYMENT_DATE 정책에서 지급일이 유효기간 밖이면 계약일이 안에 있어도 제외됩니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        _add_purchase(
            purchase_repo,
            "1000000",
            company_id=company_id,
            payment_date=date(2026, 8, 1),  # 유효기간 밖
            contract_date=date(2026, 3, 1),  # 유효기간 안 (무시되어야 함)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")

    def test_contract_date_basis_uses_contract_date(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """CONTRACT_DATE 정책은 계약일을 기준으로 판정합니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "STARTUP", "창업기업", "CONTRACT_DATE")
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        # 계약일은 유효기간 내(인정), 지급일은 유효기간 밖 → 계약일 기준이면 인정
        _add_purchase(
            purchase_repo,
            "2000000",
            company_id=company_id,
            payment_date=date(2026, 8, 1),
            contract_date=date(2026, 3, 1),
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("2000000")

    def test_contract_date_basis_ignores_payment_date(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """CONTRACT_DATE 정책에서 계약일이 유효기간 밖이면 지급일이 안에 있어도 제외됩니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "STARTUP", "창업기업", "CONTRACT_DATE")
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 1, 1), date(2026, 6, 30)
        )
        _add_purchase(
            purchase_repo,
            "2000000",
            company_id=company_id,
            payment_date=date(2026, 3, 15),  # 유효기간 안 (무시되어야 함)
            contract_date=date(2025, 12, 1),  # 유효기간 밖
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")


class TestBusinessRuleValidityPeriod:
    """인증 유효기간 경계 판정(inclusive)을 검증합니다."""

    def _setup(
        self,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
        payment_date: date,
        valid_from: date = date(2026, 3, 1),
        valid_to: date = date(2026, 3, 31),
    ) -> int:
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        _add_certification(certification_repo, company_id, policy_id, valid_from, valid_to)
        _add_purchase(purchase_repo, "1000000", company_id=company_id, payment_date=payment_date)
        return policy_id

    def test_start_boundary_included(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """기준일 == valid_from 은 포함(inclusive)됩니다."""
        policy_id = self._setup(
            company_repo, policy_repo, certification_repo, purchase_repo, date(2026, 3, 1)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")

    def test_end_boundary_included(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """기준일 == valid_to 은 포함(inclusive)됩니다."""
        policy_id = self._setup(
            company_repo, policy_repo, certification_repo, purchase_repo, date(2026, 3, 31)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")

    def test_before_start_excluded(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """기준일 < valid_from (하루 전) 은 제외됩니다."""
        policy_id = self._setup(
            company_repo, policy_repo, certification_repo, purchase_repo, date(2026, 2, 28)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")

    def test_after_end_excluded(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """기준일 > valid_to (하루 후) 은 제외됩니다."""
        policy_id = self._setup(
            company_repo, policy_repo, certification_repo, purchase_repo, date(2026, 4, 1)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("0")

    def test_multiple_certifications_any_range_counts(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """같은 정책 인증을 여러 건 보유하면 그중 하나라도 만족하면 인정합니다."""
        company_id = _add_company(company_repo, "1000000001")
        policy_id = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 1, 1), date(2026, 1, 31)
        )
        _add_certification(
            certification_repo, company_id, policy_id, date(2026, 6, 1), date(2026, 6, 30)
        )
        # 지급일 6-15 는 두 번째 인증 기간에 포함
        _add_purchase(
            purchase_repo, "1000000", company_id=company_id, payment_date=date(2026, 6, 15)
        )
        assert calculator.calculate_policy_purchase(policy_id) == Decimal("1000000")


class TestBusinessRuleMixedPolicies:
    """서로 다른 판정 기준을 가진 여러 정책의 혼합 계산을 검증합니다."""

    def test_mixed_payment_and_contract_policies(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """PAYMENT_DATE 정책과 CONTRACT_DATE 정책이 각자 기준일로 판정됩니다."""
        small_company = _add_company(company_repo, "1000000001")
        startup_company = _add_company(company_repo, "1000000002")

        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        startup_policy = _add_policy(policy_repo, "STARTUP", "창업기업", "CONTRACT_DATE")

        _add_certification(
            certification_repo, small_company, small_policy, date(2026, 1, 1), date(2026, 6, 30)
        )
        _add_certification(
            certification_repo, startup_company, startup_policy, date(2026, 1, 1), date(2026, 6, 30)
        )

        # 중소기업: 지급일 기준으로 인정(지급일 3-15 in), 계약일은 밖
        _add_purchase(
            purchase_repo,
            "3000000",
            company_id=small_company,
            payment_date=date(2026, 3, 15),
            contract_date=date(2025, 11, 1),
        )
        # 창업기업: 계약일 기준으로 인정(계약일 2-1 in), 지급일은 밖
        _add_purchase(
            purchase_repo,
            "7000000",
            company_id=startup_company,
            payment_date=date(2026, 9, 1),
            contract_date=date(2026, 2, 1),
        )

        results = calculator.calculate_all(
            {small_policy: Decimal("50"), startup_policy: Decimal("50")}
        )
        by_code = {r.policy_code: r for r in results}
        assert by_code["SMALL_BUSINESS"].purchase_amount == Decimal("3000000")
        assert by_code["STARTUP"].purchase_amount == Decimal("7000000")
        # 전체 구매금액은 매칭·유효기간과 무관하게 모든 구매 합산
        assert by_code["SMALL_BUSINESS"].total_purchase_amount == Decimal("10000000")

    def test_mixed_with_one_out_of_range(
        self,
        calculator: ProcurementAchievementCalculator,
        company_repo: CompanyRepository,
        policy_repo: PolicyRepository,
        certification_repo: CertificationRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """혼합 계산에서 유효기간 밖 구매는 해당 정책 실적에서만 제외됩니다."""
        small_company = _add_company(company_repo, "1000000001")
        startup_company = _add_company(company_repo, "1000000002")

        small_policy = _add_policy(policy_repo, "SMALL_BUSINESS", "중소기업", "PAYMENT_DATE")
        startup_policy = _add_policy(policy_repo, "STARTUP", "창업기업", "CONTRACT_DATE")

        _add_certification(
            certification_repo, small_company, small_policy, date(2026, 1, 1), date(2026, 6, 30)
        )
        _add_certification(
            certification_repo, startup_company, startup_policy, date(2026, 1, 1), date(2026, 6, 30)
        )

        # 중소기업: 지급일이 유효기간 밖 → 제외
        _add_purchase(
            purchase_repo,
            "3000000",
            company_id=small_company,
            payment_date=date(2026, 12, 1),
        )
        # 창업기업: 계약일 유효기간 안 → 인정
        _add_purchase(
            purchase_repo,
            "7000000",
            company_id=startup_company,
            contract_date=date(2026, 2, 1),
        )

        results = calculator.calculate_all(
            {small_policy: Decimal("50"), startup_policy: Decimal("50")}
        )
        by_code = {r.policy_code: r for r in results}
        assert by_code["SMALL_BUSINESS"].purchase_amount == Decimal("0")
        assert by_code["STARTUP"].purchase_amount == Decimal("7000000")
