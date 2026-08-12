"""
스키마 마이그레이션 테스트.

``CREATE TABLE IF NOT EXISTS`` 는 **기존 테이블에 컬럼을 추가하지 않으므로**,
나중에 추가된 컬럼은 ``ALTER TABLE`` 로 보완해야 합니다. 구 스키마 DB 가
그대로 동작하는지, 기존 계산 결과가 달라지지 않는지 검증합니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.bootstrap import init_db, migrate_schema, verify_bootstrap
from procurement.database.purchase_repository import PurchaseRepository

#: batch_id 컬럼이 없던 시절의 purchase 테이블
LEGACY_PURCHASE_SQL = """
CREATE TABLE purchase (
    purchase_id INTEGER PRIMARY KEY,
    business_no TEXT NOT NULL,
    company_id INTEGER,
    company_name TEXT NOT NULL,
    contract_date DATE NOT NULL,
    payment_date DATE NOT NULL,
    amount NUMERIC NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

LEGACY_ROW_SQL = (
    "INSERT INTO purchase "
    "(business_no, company_id, company_name, contract_date, payment_date, "
    "amount, created_at, updated_at) "
    "VALUES ('1234567890', NULL, '기존업체', '2026-01-01', '2026-01-31', "
    "'1000', '2026-01-31 00:00:00', '2026-01-31 00:00:00')"
)


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """batch_id 컬럼이 없는 구 스키마 DB 를 만듭니다."""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(LEGACY_PURCHASE_SQL)
        conn.execute(LEGACY_ROW_SQL)
    return path


def _columns(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(purchase)")}


class TestMigrateSchema:
    """``migrate_schema``."""

    def test_adds_batch_id(self, legacy_db: Path) -> None:
        assert "batch_id" not in _columns(legacy_db)
        migrate_schema(legacy_db)
        assert "batch_id" in _columns(legacy_db)

    def test_reports_added_columns(self, legacy_db: Path) -> None:
        assert migrate_schema(legacy_db) == ["purchase.batch_id"]

    def test_is_idempotent(self, legacy_db: Path) -> None:
        migrate_schema(legacy_db)
        assert migrate_schema(legacy_db) == []

    def test_keeps_existing_rows(self, legacy_db: Path) -> None:
        migrate_schema(legacy_db)
        rows = PurchaseRepository(legacy_db).find_all()
        assert len(rows) == 1
        assert rows[0].amount == Decimal("1000")

    def test_existing_rows_have_null_batch_id(self, legacy_db: Path) -> None:
        migrate_schema(legacy_db)
        assert PurchaseRepository(legacy_db).find_all()[0].batch_id is None

    def test_existing_rows_stay_in_calculation(self, legacy_db: Path) -> None:
        """batch_id 가 NULL 인 기존 행은 계산에서 사라지지 않는다."""
        migrate_schema(legacy_db)
        assert len(PurchaseRepository(legacy_db).find_for_calculation()) == 1

    def test_does_nothing_on_missing_table(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        assert migrate_schema(empty) == []


class TestInitDb:
    """``init_db`` 는 마이그레이션까지 수행한다."""

    def test_migrates_legacy_db(self, legacy_db: Path) -> None:
        init_db(legacy_db)
        assert "batch_id" in _columns(legacy_db)

    def test_creates_import_batch_table(self, tmp_path: Path) -> None:
        path = tmp_path / "new.db"
        init_db(path)
        with sqlite3.connect(path) as conn:
            names = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "import_batch" in names

    def test_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "new.db"
        init_db(path)
        init_db(path)
        assert "batch_id" in _columns(path)


class TestVerifyBootstrap:
    """Health Check 가 새 스키마를 점검한다."""

    def test_healthy_after_init(self, tmp_path: Path) -> None:
        path = tmp_path / "new.db"
        init_db(path)
        report = verify_bootstrap(path)
        schema_items = [item for item in report.items if item.name == "스키마(컬럼)"]
        assert schema_items and schema_items[0].passed

    def test_detects_missing_import_batch(self, legacy_db: Path) -> None:
        report = verify_bootstrap(legacy_db)
        table_items = [item for item in report.items if item.name == "테이블"]
        assert table_items and not table_items[0].passed

    def test_detects_missing_batch_id(self, tmp_path: Path) -> None:
        """테이블은 다 있으나 purchase.batch_id 만 없는 DB 를 감지한다."""
        path = tmp_path / "partial.db"
        init_db(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DROP INDEX IF EXISTS idx_purchase_batch")
            conn.execute("ALTER TABLE purchase DROP COLUMN batch_id")
        report = verify_bootstrap(path)
        schema_items = [item for item in report.items if item.name == "스키마(컬럼)"]
        assert schema_items and not schema_items[0].passed


class TestFindForCalculationWithoutBatchTable:
    """import_batch 테이블이 없어도 계산 조회가 동작한다."""

    def test_works_on_legacy_db(self, legacy_db: Path) -> None:
        migrate_schema(legacy_db)
        rows = PurchaseRepository(legacy_db).find_for_calculation()
        assert len(rows) == 1

    def test_period_filter_works_on_legacy_db(self, legacy_db: Path) -> None:
        from procurement.core.period import PAYMENT_DATE, PeriodFilter

        migrate_schema(legacy_db)
        rows = PurchaseRepository(legacy_db).find_for_calculation(
            PeriodFilter.for_year(2026, PAYMENT_DATE)
        )
        assert len(rows) == 1
        assert rows[0].payment_date == date(2026, 1, 31)
