"""
tests.test_resolution_date_field

**결의일자(``Purchase.resolution_date``) 전용 필드** 검증 — 2026-08-15 PM 최종 결정.

    ``resolution_date`` 필드를 신설한다. 기존 ``payment_date`` 를 결의일자로
    재정의하지 않는다.

    Excel 결의일자 → ``Purchase.resolution_date``
    Excel 계약일자 → ``Purchase.contract_date``

이 파일이 고정하는 것:

1. 모델·DB·적재 계층을 통해 결의일자가 **끝까지 보존**된다
2. 결의일자가 없는 **기존 데이터가 깨지지 않는다**
3. ``payment_date`` 의 의미가 **바뀌지 않았다**
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.period import RESOLUTION_DATE, PeriodFilter
from procurement.database.bootstrap import init_db, migrate_schema, verify_bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.purchase_importer import ImportStatus, PurchaseImporter
from procurement.models.purchase import Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """초기화된 DB 경로."""
    path = tmp_path / "resolution.db"
    init_db(path)
    return path


def _row(**overrides: object) -> dict[str, object]:
    """표준 양식에서 매핑된 형태의 행 한 건."""
    row: dict[str, object] = {
        "business_no": "220-81-62517",
        "company_name": "한빛산업개발",
        "contract_date": "2026-02-20",
        "payment_date": "2026-04-01",
        "resolution_date": "2026-03-15",
        "amount": "54,648,000",
    }
    row.update(overrides)
    return row


class TestModel:
    """모델이 결의일자를 **선택 항목**으로 가진다."""

    def test_field_is_optional(self) -> None:
        """값을 주지 않아도 만들 수 있다(기존 호출부 보호)."""
        purchase = Purchase(
            business_no="1234567890",
            company_name="A기업",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 4, 1),
            amount=Decimal("1000"),
        )
        assert purchase.resolution_date is None

    def test_payment_date_is_still_its_own_field(self) -> None:
        """⛔ 결의일자와 지급일은 **다른 값**으로 각각 보존된다."""
        purchase = Purchase(
            business_no="1234567890",
            company_name="A기업",
            contract_date=date(2026, 2, 20),
            payment_date=date(2026, 4, 1),
            resolution_date=date(2026, 3, 15),
            amount=Decimal("1000"),
        )
        assert purchase.resolution_date != purchase.payment_date


class TestPersistence:
    """DB 왕복에서 값이 그대로 살아남는다."""

    def test_roundtrip_keeps_the_value(self, db_path: Path) -> None:
        repository = PurchaseRepository(db_path)
        saved = repository.insert(
            Purchase(
                business_no="1234567890",
                company_name="A기업",
                contract_date=date(2026, 2, 20),
                payment_date=date(2026, 4, 1),
                resolution_date=date(2026, 3, 15),
                amount=Decimal("1000"),
            )
        )

        assert saved.resolution_date == date(2026, 3, 15)
        loaded = repository.find_all()[0]
        assert loaded.resolution_date == date(2026, 3, 15)
        assert loaded.payment_date == date(2026, 4, 1)

    def test_null_roundtrips_as_none(self, db_path: Path) -> None:
        repository = PurchaseRepository(db_path)
        repository.insert(
            Purchase(
                business_no="1234567890",
                company_name="A기업",
                contract_date=date(2026, 2, 20),
                payment_date=date(2026, 4, 1),
                amount=Decimal("1000"),
            )
        )

        assert repository.find_all()[0].resolution_date is None


class TestLegacyDatabase:
    """기존 DB 를 깨뜨리지 않는다."""

    @pytest.fixture
    def legacy_db(self, tmp_path: Path) -> Path:
        """``resolution_date`` 가 없던 시절의 DB 를 만들고 한 건 넣습니다."""
        path = tmp_path / "legacy.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE purchase (
                    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            )
            conn.execute(
                "INSERT INTO purchase (business_no, company_name, contract_date, "
                "payment_date, amount, created_at, updated_at) "
                "VALUES ('1234567890', 'A기업', '2026-02-20', '2026-04-01', 1000, "
                "'2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            )
        return path

    def test_migration_adds_the_column(self, legacy_db: Path) -> None:
        assert "purchase.resolution_date" in migrate_schema(legacy_db)

    def test_migration_is_idempotent(self, legacy_db: Path) -> None:
        migrate_schema(legacy_db)
        assert migrate_schema(legacy_db) == []

    def test_existing_row_survives_with_null(self, legacy_db: Path) -> None:
        """기존 행은 남고, 결의일자는 **NULL 로 비어 있음이 그대로 보존**된다."""
        init_db(legacy_db)

        rows = PurchaseRepository(legacy_db).find_all()
        assert len(rows) == 1
        assert rows[0].amount == Decimal("1000")
        assert rows[0].payment_date == date(2026, 4, 1)
        assert rows[0].resolution_date is None

    def test_health_check_flags_the_missing_column(self, legacy_db: Path) -> None:
        """구 스키마는 ``init`` 전에는 실패로 보고된다."""
        report = verify_bootstrap(legacy_db)
        assert not report.healthy
        assert "resolution_date" in report.format_report()

    def test_health_check_passes_after_init(self, legacy_db: Path) -> None:
        init_db(legacy_db)
        from procurement.database.bootstrap import seed_policies

        seed_policies(legacy_db)
        assert verify_bootstrap(legacy_db).healthy


class TestImporter:
    """적재 계층이 결의일자를 읽는다."""

    def _importer(self, db_path: Path) -> PurchaseImporter:
        return PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path))

    def test_resolution_date_is_stored(self, db_path: Path) -> None:
        report = self._importer(db_path).import_rows([_row()])

        assert report.stored_count == 1
        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.resolution_date == date(2026, 3, 15)
        assert stored.contract_date == date(2026, 2, 20)
        assert stored.payment_date == date(2026, 4, 1)

    def test_missing_resolution_date_is_accepted(self, db_path: Path) -> None:
        """결의일자가 없는 행도 **기존과 동일하게** 적재된다(선택 항목)."""
        row = _row()
        del row["resolution_date"]

        report = self._importer(db_path).import_rows([row])

        assert report.stored_count == 1
        assert PurchaseRepository(db_path).find_all()[0].resolution_date is None

    def test_blank_resolution_date_is_accepted_as_none(self, db_path: Path) -> None:
        report = self._importer(db_path).import_rows([_row(resolution_date="  ")])

        assert report.stored_count == 1
        assert PurchaseRepository(db_path).find_all()[0].resolution_date is None

    def test_malformed_resolution_date_fails_the_row(self, db_path: Path) -> None:
        """⛔ 형식이 틀린 결의일자를 조용히 버리지 않는다."""
        report = self._importer(db_path).import_rows([_row(resolution_date="2026년 3월")])

        assert report.rows[0].status is ImportStatus.FAILED
        assert any("결의일자" in message for message in report.rows[0].messages)
        assert PurchaseRepository(db_path).count() == 0

    def test_date_object_is_accepted(self, db_path: Path) -> None:
        """엑셀에서 날짜형으로 들어온 값도 그대로 받는다."""
        report = self._importer(db_path).import_rows(
            [_row(resolution_date=date(2026, 3, 15))]
        )

        assert report.stored_count == 1
        assert PurchaseRepository(db_path).find_all()[0].resolution_date == date(2026, 3, 15)


class TestPeriodFilter:
    """연도 귀속을 결의일자 기준으로 지정할 수 있다."""

    def test_resolution_date_is_a_valid_period_field(self) -> None:
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert period.date_field == RESOLUTION_DATE
        assert period.start == date(2026, 1, 1)
        assert period.end == date(2026, 12, 31)

    def test_no_default_is_introduced(self) -> None:
        """⛔ 기준 날짜 필드에 **기본값을 만들지 않는다**(D-24)."""
        with pytest.raises(TypeError):
            PeriodFilter.for_year(2026)  # type: ignore[call-arg]
