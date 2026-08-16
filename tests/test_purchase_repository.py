"""
PurchaseRepository 테스트.

Purchase 테이블 생성, 등록/조회/집계, 필수값·금액 검증을 확인합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.purchase_repository import (
    PurchaseRepository,
    PurchaseValidationError,
)
from procurement.models import Purchase


@pytest.fixture
def repo(tmp_path: Path) -> PurchaseRepository:
    """테이블이 생성된 PurchaseRepository 를 반환합니다."""
    r = PurchaseRepository(tmp_path / "test.db")
    r.create_table()
    return r


def _sample(business_no: str = "1234567890", amount: str = "1000000") -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name="테스트기업",
        contract_date=date(2026, 3, 1),
        payment_date=date(2026, 3, 15),
        amount=Decimal(amount),
    )


class TestCreateTable:
    """테이블 생성 및 제약조건을 검증합니다."""

    def test_create_table_is_idempotent(self, tmp_path: Path) -> None:
        r = PurchaseRepository(tmp_path / "test.db")
        r.create_table()
        r.create_table()  # 반복 실행해도 오류가 없어야 함
        assert r.count() == 0

    def test_table_exists_after_create(self, repo: PurchaseRepository) -> None:
        rows = repo.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='purchase'")
        assert len(rows) == 1

    def test_primary_key_defined(self, repo: PurchaseRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(purchase)")}
        assert cols["purchase_id"]["pk"] == 1

    def test_not_null_columns(self, repo: PurchaseRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(purchase)")}
        for name in (
            "business_no",
            "company_name",
            "contract_date",
            "payment_date",
            "amount",
            "created_at",
            "updated_at",
        ):
            assert cols[name]["notnull"] == 1, f"{name} 은 NOT NULL 이어야 합니다."

    def test_company_id_allows_null(self, repo: PurchaseRepository) -> None:
        """company_id 는 매칭 후 저장되므로 NULL 을 허용합니다."""
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(purchase)")}
        assert cols["company_id"]["notnull"] == 0

    def test_columns_match_design(self, repo: PurchaseRepository) -> None:
        """DATABASE_DESIGN.md 정의 컬럼과 정확히 일치해야 합니다.

        ``batch_id`` 는 월별 누적 적재(Import Batch) 도입으로 추가된 컬럼이며
        NULL 을 허용합니다.

        .. note::
            **기대값이 바뀐 이유** — 2026-08-15 PM 결정으로 결의일자를 담는
            ``resolution_date`` 컬럼이 신설되었습니다. ``payment_date`` 를
            결의일자로 재정의하지 않았으므로 기존 컬럼은 그대로입니다.
        """
        names = [row["name"] for row in repo.execute("PRAGMA table_info(purchase)")]
        assert names == [
            "purchase_id",
            "business_no",
            "company_id",
            "company_name",
            "contract_date",
            "payment_date",
            "resolution_date",
            "amount",
            "batch_id",
            "created_at",
            "updated_at",
        ]

    def test_resolution_date_allows_null(self, repo: PurchaseRepository) -> None:
        """``resolution_date`` 는 기존 행 보호를 위해 NULL 을 허용합니다."""
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(purchase)")}
        assert cols["resolution_date"]["notnull"] == 0

    def test_batch_id_allows_null(self, repo: PurchaseRepository) -> None:
        """batch_id 는 배치 없이 적재된 행을 위해 NULL 을 허용합니다."""
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(purchase)")}
        assert cols["batch_id"]["notnull"] == 0

    def test_no_foreign_keys(self, repo: PurchaseRepository) -> None:
        """이번 Issue 범위에서 Foreign Key 제약은 추가하지 않습니다."""
        assert repo.execute("PRAGMA foreign_key_list(purchase)") == []


class TestInsert:
    """등록(Insert) 동작을 검증합니다."""

    def test_insert_returns_purchase_id(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.purchase_id is not None
        assert saved.purchase_id >= 1

    def test_insert_sets_timestamps(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert isinstance(saved.created_at, datetime)
        assert isinstance(saved.updated_at, datetime)

    def test_insert_persists_row(self, repo: PurchaseRepository) -> None:
        repo.insert(_sample())
        assert repo.count() == 1

    def test_insert_without_company_id(self, repo: PurchaseRepository) -> None:
        """company_id 없이 저장할 수 있어야 합니다 (매칭 전 상태)."""
        saved = repo.insert(_sample())
        assert saved.company_id is None
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.company_id is None

    def test_insert_with_company_id(self, repo: PurchaseRepository) -> None:
        """company_id 가 주어지면 그대로 저장됩니다."""
        saved = repo.insert(
            Purchase(
                business_no="1112223334",
                company_id=42,
                company_name="매칭된기업",
                contract_date=date(2026, 1, 5),
                payment_date=date(2026, 1, 10),
                amount=Decimal("500000"),
            )
        )
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.company_id == 42

    def test_same_business_no_multiple_purchases(self, repo: PurchaseRepository) -> None:
        """한 사업자번호로 여러 구매건이 저장될 수 있습니다."""
        repo.insert(_sample("9998887776"))
        repo.insert(_sample("9998887776"))
        assert len(repo.find_by_business_no("9998887776")) == 2


class TestFindById:
    """단건 조회를 검증합니다."""

    def test_find_by_id(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample("5556667778"))
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.business_no == "5556667778"
        assert found.company_name == "테스트기업"

    def test_find_by_id_missing_returns_none(self, repo: PurchaseRepository) -> None:
        assert repo.find_by_id(99999) is None


class TestFindByBusinessNo:
    """사업자등록번호별 조회를 검증합니다."""

    def test_returns_only_matching_business_no(self, repo: PurchaseRepository) -> None:
        repo.insert(_sample("1000000001"))
        repo.insert(_sample("1000000001"))
        repo.insert(_sample("2000000002"))
        result = repo.find_by_business_no("1000000001")
        assert len(result) == 2
        assert all(p.business_no == "1000000001" for p in result)

    def test_returns_empty_list_when_none(self, repo: PurchaseRepository) -> None:
        assert repo.find_by_business_no("0000000000") == []


class TestFindAll:
    """전체 조회를 검증합니다."""

    def test_returns_empty_when_no_rows(self, repo: PurchaseRepository) -> None:
        assert repo.find_all() == []

    def test_returns_all_rows_ordered(self, repo: PurchaseRepository) -> None:
        repo.insert(_sample("1000000001"))
        repo.insert(_sample("1000000002"))
        rows = repo.find_all()
        assert [p.business_no for p in rows] == ["1000000001", "1000000002"]


class TestCount:
    """등록 구매건 수 집계를 검증합니다."""

    def test_count_zero(self, repo: PurchaseRepository) -> None:
        assert repo.count() == 0

    def test_count_multiple(self, repo: PurchaseRepository) -> None:
        repo.insert(_sample("1000000001"))
        repo.insert(_sample("1000000002"))
        repo.insert(_sample("1000000003"))
        assert repo.count() == 3


class TestDateAndTimestampRoundtrip:
    """날짜·타임스탬프 저장/조회를 검증합니다."""

    def test_contract_date_roundtrip(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.contract_date == date(2026, 3, 1)
        assert isinstance(found.contract_date, date)

    def test_payment_date_roundtrip(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.payment_date == date(2026, 3, 15)
        assert isinstance(found.payment_date, date)

    def test_contract_and_payment_are_independent(self, repo: PurchaseRepository) -> None:
        """계약일과 지급일이 서로 다른 값으로 각각 저장/복원되어야 합니다."""
        saved = repo.insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트기업",
                contract_date=date(2025, 12, 20),
                payment_date=date(2026, 2, 3),
                amount=Decimal("1000000"),
            )
        )
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.contract_date == date(2025, 12, 20)
        assert found.payment_date == date(2026, 2, 3)

    def test_timestamps_roundtrip(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.created_at == saved.created_at
        assert found.updated_at == saved.updated_at


class TestAmountRoundtrip:
    """구매금액 저장/조회를 검증합니다."""

    def test_amount_is_decimal(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert isinstance(found.amount, Decimal)

    def test_integer_amount_roundtrip(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample(amount="12345678"))
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.amount == Decimal("12345678")

    def test_fractional_amount_roundtrip(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample(amount="1000.50"))
        assert saved.purchase_id is not None
        found = repo.find_by_id(saved.purchase_id)
        assert found is not None
        assert found.amount == Decimal("1000.50")


class TestRequiredValidation:
    """필수값 검증을 확인합니다."""

    def test_missing_business_no(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(business_no=""))

    def test_blank_business_no(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(business_no="   "))

    def test_missing_company_name(self, repo: PurchaseRepository) -> None:
        p = _sample()
        p.company_name = ""
        with pytest.raises(PurchaseValidationError):
            repo.insert(p)

    def test_blank_company_name(self, repo: PurchaseRepository) -> None:
        p = _sample()
        p.company_name = "   "
        with pytest.raises(PurchaseValidationError):
            repo.insert(p)

    def test_none_contract_date(self, repo: PurchaseRepository) -> None:
        p = _sample()
        p.contract_date = None  # type: ignore[assignment]
        with pytest.raises(PurchaseValidationError):
            repo.insert(p)

    def test_none_payment_date(self, repo: PurchaseRepository) -> None:
        p = _sample()
        p.payment_date = None  # type: ignore[assignment]
        with pytest.raises(PurchaseValidationError):
            repo.insert(p)

    def test_none_amount(self, repo: PurchaseRepository) -> None:
        p = _sample()
        p.amount = None  # type: ignore[assignment]
        with pytest.raises(PurchaseValidationError):
            repo.insert(p)

    def test_validation_failure_persists_nothing(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(business_no=""))
        assert repo.count() == 0


class TestAmountValidation:
    """구매금액 검증(0 이하 불허)을 확인합니다."""

    def test_zero_amount_raises(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(amount="0"))

    def test_negative_amount_raises(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(amount="-1000"))

    def test_smallest_positive_amount_allowed(self, repo: PurchaseRepository) -> None:
        saved = repo.insert(_sample(amount="0.01"))
        assert saved.purchase_id is not None

    def test_amount_validation_failure_persists_nothing(self, repo: PurchaseRepository) -> None:
        with pytest.raises(PurchaseValidationError):
            repo.insert(_sample(amount="0"))
        assert repo.count() == 0
