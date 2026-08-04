"""
CompanyMatcher 테스트.

사업자등록번호를 기준으로 Purchase 와 Company 를 연결하는 동작을 검증합니다.
Repository 확장 메서드(find_unmatched / update_company_id)도 함께 검증합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers.company_matcher import CompanyMatcher
from procurement.models import Company, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """company / purchase 테이블이 생성된 DB 파일 경로를 반환합니다."""
    path = tmp_path / "test.db"
    CompanyRepository(path).create_table()
    PurchaseRepository(path).create_table()
    return path


@pytest.fixture
def company_repo(db_path: Path) -> CompanyRepository:
    return CompanyRepository(db_path)


@pytest.fixture
def purchase_repo(db_path: Path) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def matcher(company_repo: CompanyRepository, purchase_repo: PurchaseRepository) -> CompanyMatcher:
    return CompanyMatcher(company_repo, purchase_repo)


def _company(business_no: str) -> Company:
    return Company(
        business_no=business_no,
        company_name="테스트기업",
        representative_name="홍길동",
    )


def _purchase(business_no: str, company_id: int | None = None) -> Purchase:
    return Purchase(
        business_no=business_no,
        company_id=company_id,
        company_name="공급업체",
        purchase_date=date(2026, 3, 15),
        amount=Decimal("1000000"),
    )


class TestPurchaseRepositoryFindUnmatched:
    """find_unmatched() 동작을 검증합니다."""

    def test_returns_empty_when_no_rows(self, purchase_repo: PurchaseRepository) -> None:
        assert purchase_repo.find_unmatched() == []

    def test_returns_only_rows_without_company_id(self, purchase_repo: PurchaseRepository) -> None:
        purchase_repo.insert(_purchase("1000000001"))
        purchase_repo.insert(_purchase("1000000002", company_id=7))
        purchase_repo.insert(_purchase("1000000003"))

        unmatched = purchase_repo.find_unmatched()
        assert len(unmatched) == 2
        assert all(p.company_id is None for p in unmatched)
        assert [p.business_no for p in unmatched] == ["1000000001", "1000000003"]

    def test_returns_empty_when_all_matched(self, purchase_repo: PurchaseRepository) -> None:
        purchase_repo.insert(_purchase("1000000001", company_id=1))
        assert purchase_repo.find_unmatched() == []


class TestPurchaseRepositoryUpdateCompanyId:
    """update_company_id() 동작을 검증합니다."""

    def test_updates_company_id(self, purchase_repo: PurchaseRepository) -> None:
        saved = purchase_repo.insert(_purchase("2000000001"))
        assert saved.purchase_id is not None

        assert purchase_repo.update_company_id(saved.purchase_id, 55) is True

        found = purchase_repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.company_id == 55

    def test_returns_false_for_missing_purchase(self, purchase_repo: PurchaseRepository) -> None:
        assert purchase_repo.update_company_id(99999, 1) is False

    def test_updates_updated_at(self, purchase_repo: PurchaseRepository) -> None:
        """실제 수정이 발생하므로 updated_at 이 갱신되어야 합니다."""
        saved = purchase_repo.insert(_purchase("2000000002"))
        assert saved.purchase_id is not None
        assert saved.updated_at is not None

        purchase_repo.update_company_id(saved.purchase_id, 3)

        found = purchase_repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.updated_at is not None
        assert found.updated_at >= saved.updated_at

    def test_does_not_change_other_columns(self, purchase_repo: PurchaseRepository) -> None:
        saved = purchase_repo.insert(_purchase("2000000003"))
        assert saved.purchase_id is not None

        purchase_repo.update_company_id(saved.purchase_id, 9)

        found = purchase_repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.business_no == saved.business_no
        assert found.company_name == saved.company_name
        assert found.purchase_date == saved.purchase_date
        assert found.amount == saved.amount
        assert found.created_at == saved.created_at


class TestMatchPurchase:
    """match_purchase() 동작을 검증합니다."""

    def test_match_success(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        company = company_repo.insert(_company("1234567890"))
        purchase = purchase_repo.insert(_purchase("1234567890"))
        assert purchase.purchase_id is not None

        assert matcher.match_purchase(purchase.purchase_id) is True

        found = purchase_repo.find_by_id(purchase.purchase_id)
        assert found is not None
        assert found.company_id == company.company_id

    def test_returns_false_when_company_missing(
        self, matcher: CompanyMatcher, purchase_repo: PurchaseRepository
    ) -> None:
        """기업이 등록되지 않은 사업자번호면 False 를 반환합니다."""
        purchase = purchase_repo.insert(_purchase("9999999999"))
        assert purchase.purchase_id is not None

        assert matcher.match_purchase(purchase.purchase_id) is False

        found = purchase_repo.find_by_id(purchase.purchase_id)
        assert found is not None
        assert found.company_id is None

    def test_returns_false_when_purchase_missing(self, matcher: CompanyMatcher) -> None:
        """존재하지 않는 purchase_id 면 예외 없이 False 를 반환합니다."""
        assert matcher.match_purchase(99999) is False

    def test_does_not_raise_on_missing_data(self, matcher: CompanyMatcher) -> None:
        """없는 데이터에 대해 예외를 발생시키지 않습니다."""
        assert matcher.match_purchase(12345) is False

    def test_already_matched_purchase(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """이미 company_id 가 있는 건도 명세의 동작 순서대로 매칭합니다."""
        company = company_repo.insert(_company("1112223334"))
        purchase = purchase_repo.insert(_purchase("1112223334", company_id=999))
        assert purchase.purchase_id is not None

        assert matcher.match_purchase(purchase.purchase_id) is True

        found = purchase_repo.find_by_id(purchase.purchase_id)
        assert found is not None
        assert found.company_id == company.company_id


class TestMatchAll:
    """match_all() 동작을 검증합니다."""

    def test_returns_zero_when_no_purchases(self, matcher: CompanyMatcher) -> None:
        assert matcher.match_all() == 0

    def test_matches_multiple_purchases(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        company_repo.insert(_company("1000000001"))
        company_repo.insert(_company("1000000002"))
        purchase_repo.insert(_purchase("1000000001"))
        purchase_repo.insert(_purchase("1000000002"))

        assert matcher.match_all() == 2
        assert purchase_repo.find_unmatched() == []

    def test_counts_only_successful_matches(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """5건 중 3건만 기업이 존재하면 3 을 반환합니다."""
        for no in ("1000000001", "1000000002", "1000000003"):
            company_repo.insert(_company(no))
        for no in (
            "1000000001",
            "1000000002",
            "1000000003",
            "8888888888",
            "9999999999",
        ):
            purchase_repo.insert(_purchase(no))

        assert matcher.match_all() == 3
        assert len(purchase_repo.find_unmatched()) == 2

    def test_skips_already_matched(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """company_id 가 이미 있는 건은 대상에서 제외합니다."""
        company_repo.insert(_company("1000000001"))
        already = purchase_repo.insert(_purchase("1000000001", company_id=777))
        purchase_repo.insert(_purchase("1000000001"))
        assert already.purchase_id is not None

        assert matcher.match_all() == 1

        # 기존 company_id 는 변경되지 않아야 함
        found = purchase_repo.find_by_id(already.purchase_id)
        assert found is not None
        assert found.company_id == 777

    def test_multiple_purchases_same_business_no(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """같은 사업자번호의 여러 구매건이 모두 매칭됩니다."""
        company = company_repo.insert(_company("1000000001"))
        purchase_repo.insert(_purchase("1000000001"))
        purchase_repo.insert(_purchase("1000000001"))
        purchase_repo.insert(_purchase("1000000001"))

        assert matcher.match_all() == 3
        matched = purchase_repo.find_by_business_no("1000000001")
        assert all(p.company_id == company.company_id for p in matched)

    def test_returns_zero_when_no_company_registered(
        self, matcher: CompanyMatcher, purchase_repo: PurchaseRepository
    ) -> None:
        purchase_repo.insert(_purchase("7777777777"))
        purchase_repo.insert(_purchase("8888888888"))
        assert matcher.match_all() == 0
        assert len(purchase_repo.find_unmatched()) == 2

    def test_is_idempotent(
        self,
        matcher: CompanyMatcher,
        company_repo: CompanyRepository,
        purchase_repo: PurchaseRepository,
    ) -> None:
        """두 번째 실행에는 대상이 없으므로 0 을 반환합니다."""
        company_repo.insert(_company("1000000001"))
        purchase_repo.insert(_purchase("1000000001"))

        assert matcher.match_all() == 1
        assert matcher.match_all() == 0
