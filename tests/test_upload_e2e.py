"""
tests.test_upload_e2e

**표준 Excel → 저장 → 계산** 전 구간 검증 (2026-08-17 PM 지시서 §8).

한 단계라도 끊기면 여기서 깨집니다::

    표준 Excel
      ↓ excel_adapter
    머리글 검증 → 행 검증
      ↓ mapping
    BatchImportService → PurchaseImporter → PurchaseRepository
      ↓
    SQLite
      ↓ 조회
    ProcurementAchievementCalculator

두 가지를 반드시 증명합니다.

1. **정상 케이스** — 저장되고, 저장된 값이 원본 그대로이며, 계산까지 이어진다
2. **오류 케이스** — 적재 계층이 **호출되지 않고**, DB 에 아무것도 남지 않는다
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.calculators import ProcurementAchievementCalculator
from procurement.core.period import RESOLUTION_DATE, PeriodFilter
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.certification import Certification
from procurement.models.company import Company
from procurement.uploads.format import header_row

IMPORT_URL = "/uploads/purchases"
VALIDATE_URL = "/uploads/purchases/validate"

#: 정상 행 2건. 사업자등록번호는 체크섬을 만족하는 실제 형식이다.
ROWS: list[list[object]] = [
    [
        date(2026, 3, 15),
        date(2026, 2, 20),
        date(2026, 4, 1),
        "한빛산업개발",
        "220-81-62517",
        54648000,
    ],
    [
        date(2026, 5, 10),
        date(2026, 4, 30),
        date(2026, 6, 1),
        "가나전자",
        "220-81-62517",
        1000000,
    ],
]

#: 오류가 섞인 행. 날짜·기업명·사업자번호·금액이 모두 잘못되었다.
BAD_ROW: list[object] = ["2026.13.45", date(2026, 2, 20), date(2026, 4, 1), None, "999", "abc"]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "e2e.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """격리 DB 를 쓰는 API 클라이언트."""
    return TestClient(create_app(db_path=db_path))


def _excel(path: Path, rows: list[list[object]]) -> Path:
    """표준 머리글 + 주어진 행으로 엑셀을 만듭니다."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


class TestHappyPath:
    """✅ 정상 케이스 — 검증 통과 → 저장 → 조회."""

    def test_upload_is_stored(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        path = _excel(tmp_path / "good.xlsx", ROWS)

        body = client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026}).json()

        assert body["ok"] is True
        assert body["stored"] is True
        assert body["total_rows"] == 2
        assert body["valid_rows"] == 2
        assert body["stored_rows"] == 2
        assert body["batch_id"] is not None
        assert PurchaseRepository(db_path).count() == 2

    def test_stored_values_match_the_excel(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """엑셀에 적은 값이 **변형 없이** DB 에 들어간다."""
        path = _excel(tmp_path / "good.xlsx", [ROWS[0]])

        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.resolution_date == date(2026, 3, 15)
        assert stored.contract_date == date(2026, 2, 20)
        assert stored.payment_date == date(2026, 4, 1)
        assert stored.company_name == "한빛산업개발"
        assert stored.business_no == "2208162517"  # 하이픈 제거만 적용
        assert stored.amount == Decimal("54648000")

    def test_three_dates_are_kept_apart(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⛔ 결의일자·계약일자·지급일이 **서로 섞이지 않는다.**

        세 날짜는 업무 의미가 다르므로, 하나가 다른 하나를 대체하면 안 됩니다.
        """
        path = _excel(tmp_path / "good.xlsx", [ROWS[0]])

        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.resolution_date != stored.contract_date
        assert stored.resolution_date != stored.payment_date
        assert stored.contract_date != stored.payment_date

    def test_batch_period_comes_from_the_year(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """대상 기간은 화면이 보낸 연도에서 나온다(파일에서 유추하지 않음)."""
        from procurement.database.import_batch_repository import ImportBatchRepository

        path = _excel(tmp_path / "good.xlsx", ROWS)

        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        batch = ImportBatchRepository(db_path).find_all()[0]
        assert batch.period_start == date(2026, 1, 1)
        assert batch.period_end == date(2026, 12, 31)

    def test_stored_rows_are_queryable_by_resolution_date(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """저장된 행을 **결의일자 기준 연도 조회**로 다시 꺼낼 수 있다."""
        path = _excel(tmp_path / "good.xlsx", ROWS)

        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        found = PurchaseRepository(db_path).find_for_calculation(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )
        assert len(found) == 2
        assert sum(p.amount for p in found) == Decimal("55648000")


class TestReachesTheCalculator:
    """✅ 저장된 데이터가 **계산기까지** 이어진다."""

    def _seed_certified_company(self, db_path: Path) -> None:
        """업로드한 사업자번호를 가진 중소기업 인증 기업을 등록합니다."""
        company_repo = CompanyRepository(db_path)
        company = company_repo.insert(
            Company(
                business_no="2208162517",
                company_name="한빛산업개발",
                representative_name="홍길동",
            )
        )
        policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None
        assert policy.policy_id is not None
        assert company.company_id is not None
        CertificationRepository(db_path).insert(
            Certification(
                company_id=company.company_id,
                policy_id=policy.policy_id,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )

    def test_uploaded_rows_are_counted_as_policy_performance(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """업로드한 구매가 정책 실적으로 집계된다.

        중소기업 정책의 판정 기준일은 ``payment_date`` 이며(현행 유지 —
        2026-08-17 PM 결정 3), 업로드한 지급일이 인증 유효기간 안에 있다.
        """
        self._seed_certified_company(db_path)
        path = _excel(tmp_path / "good.xlsx", ROWS)
        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        # 업로드된 행은 기업 정보보다 나중에 들어오므로 재매칭이 필요하다.
        from procurement.matchers.company_matcher import CompanyMatcher

        CompanyMatcher(CompanyRepository(db_path), PurchaseRepository(db_path)).match_all()

        policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None
        assert policy.policy_id is not None

        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)

        assert calculator.calculate_total_purchase(period) == Decimal("55648000")
        assert calculator.calculate_policy_purchase(policy.policy_id, period) == Decimal(
            "55648000"
        )

    def test_dashboard_summary_reflects_the_upload(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """업로드 결과가 대시보드 API 응답에 나타난다."""
        path = _excel(tmp_path / "good.xlsx", ROWS)
        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        status = TestClient(
            create_app(db_path=db_path, period_date_field=RESOLUTION_DATE)
        ).get("/dashboard/data-status?year=2026")

        assert status.status_code == 200
        assert status.json()["purchase_count"] == 2


class TestErrorPathStoresNothing:
    """⛔ 오류 케이스 — 적재 계층을 호출조차 하지 않는다."""

    def test_error_row_blocks_the_whole_upload(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """한 행이라도 오류면 **정상 행도** 저장되지 않는다."""
        path = _excel(tmp_path / "mixed.xlsx", [*ROWS, BAD_ROW])

        body = client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026}).json()

        assert body["ok"] is False
        assert body["stored"] is False
        assert body["stored_rows"] == 0
        assert body["valid_rows"] == 2  # 검증은 통과했지만
        assert PurchaseRepository(db_path).count() == 0  # 저장은 0건

    def test_no_batch_is_created_on_error(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """오류 시 배치 자체가 만들어지지 않는다(빈 배치가 남지 않음)."""
        from procurement.database.import_batch_repository import ImportBatchRepository

        path = _excel(tmp_path / "mixed.xlsx", [*ROWS, BAD_ROW])

        client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026})

        assert ImportBatchRepository(db_path).find_all() == []

    def test_error_response_still_lists_problems(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """저장하지 않아도 무엇이 잘못됐는지 그대로 알려 준다."""
        path = _excel(tmp_path / "mixed.xlsx", [*ROWS, BAD_ROW])

        body = client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026}).json()

        headers = {issue["header"] for issue in body["issues"]}
        assert {"결의일자", "기업명", "사업자등록번호", "계"} <= headers

    def test_unreadable_file_stores_nothing(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        path = tmp_path / "broken.xlsx"
        path.write_bytes(b"not an excel file")

        body = client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026}).json()

        assert body["stored"] is False
        assert body["file_errors"]
        assert PurchaseRepository(db_path).count() == 0

    def test_missing_header_stores_nothing(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """지급일 컬럼이 빠진 구(舊) 5컬럼 파일은 파일 오류로 거부된다."""
        path = tmp_path / "old-form.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(["결의일자", "계약일자", "기업명", "사업자등록번호", "계"])
        sheet.append([date(2026, 3, 15), date(2026, 2, 20), "A기업", "220-81-62517", 1000])
        workbook.save(path)
        workbook.close()

        body = client.post(IMPORT_URL, json={"file_path": str(path), "year": 2026}).json()

        assert body["stored"] is False
        assert "지급일" in body["file_errors"][0]
        assert PurchaseRepository(db_path).count() == 0


class TestApiContract:
    """API 책임 분리와 입력 검증."""

    def test_validate_endpoint_never_stores(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⛔ 검증 API 는 정상 파일이어도 저장하지 않는다."""
        path = _excel(tmp_path / "good.xlsx", ROWS)

        body = client.post(VALIDATE_URL, json={"file_path": str(path)}).json()

        assert body["ok"] is True
        assert body["stored"] is False
        assert PurchaseRepository(db_path).count() == 0

    def test_year_is_required_for_import(self, client: TestClient, tmp_path: Path) -> None:
        """⛔ 대상 연도 없이 저장할 수 없다(파일에서 유추하지 않음)."""
        path = _excel(tmp_path / "good.xlsx", ROWS)

        response = client.post(IMPORT_URL, json={"file_path": str(path)})

        assert response.status_code == 422

    def test_out_of_range_year_is_rejected(self, client: TestClient, tmp_path: Path) -> None:
        path = _excel(tmp_path / "good.xlsx", ROWS)

        response = client.post(IMPORT_URL, json={"file_path": str(path), "year": 1800})

        assert response.status_code == 422

    def test_reupload_replaces_the_previous_batch(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """같은 기간 재업로드는 **대체**한다(D-25, 기존 규칙 재사용)."""
        first = _excel(tmp_path / "first.xlsx", ROWS)
        second = _excel(tmp_path / "second.xlsx", [ROWS[0]])

        client.post(IMPORT_URL, json={"file_path": str(first), "year": 2026})
        body = client.post(IMPORT_URL, json={"file_path": str(second), "year": 2026}).json()

        assert body["stored"] is True
        # 이전 배치는 SUPERSEDED 가 되어 계산에서 빠진다.
        found = PurchaseRepository(db_path).find_for_calculation()
        assert len(found) == 1
