"""
tests.test_upload_replace_confirmation

**동일 기간 재업로드 시 사용자 확인** 검증 — PM-005 · PM-006 · PM-007 · PM-012.

가장 중요하게 고정하는 성질은 **"무엇이 일어나지 않는가"** 입니다.

1. 확인 없이는 **절대 교체되지 않는다**
2. 409 를 낼 때 **DB 는 전혀 변경되지 않는다**
3. 검증 실패 때문에 **기존 정상 데이터가 사라지지 않는다**
4. 교체는 **논리 교체**다 — 이전 배치를 물리 삭제하지 않는다
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import build_upload_service, create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED
from procurement.uploads.format import header_row
from procurement.uploads.upload_service import ExistingPeriodBatchError

IMPORT_URL = "/uploads/purchases"

ROW_A: list[object] = [
    date(2026, 3, 15),
    date(2026, 2, 20),
    date(2026, 4, 1),
    "한빛산업개발",
    "220-81-62517",
    1000000,
    date(2026, 3, 10),
    "사무용품 구매",
    "소모성물품구입비",
]
ROW_B: list[object] = [
    date(2026, 5, 10),
    date(2026, 4, 30),
    date(2026, 6, 1),
    "가나전자",
    "220-81-62517",
    2000000,
    date(2026, 5, 8),
    "부품 구매",
    "",
]
BAD_ROW: list[object] = [
    "2026.13.45",
    date(2026, 2, 20),
    date(2026, 4, 1),
    None,
    "999",
    "abc",
    date(2026, 3, 10),
    "",
    "",
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "replace.db"
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


def _upload(
    client: TestClient, path: Path, year: int = 2026, **extra: object
) -> httpx.Response:
    """업로드 요청 한 번."""
    payload: dict[str, object] = {"file_path": str(path), "year": year}
    payload.update(extra)
    response: httpx.Response = client.post(IMPORT_URL, json=payload)
    return response


class TestFirstUpload:
    """최초 업로드는 묻지 않는다."""

    def test_first_upload_stores_without_asking(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        response = _upload(client, _excel(tmp_path / "a.xlsx", [ROW_A, ROW_B]))

        assert response.status_code == 200
        assert response.json()["stored"] is True
        assert PurchaseRepository(db_path).count() == 2

    def test_different_period_does_not_ask(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """다른 연도는 교체 대상이 아니므로 묻지 않는다."""
        _upload(client, _excel(tmp_path / "a.xlsx", [ROW_A]), year=2026)

        response = _upload(client, _excel(tmp_path / "b.xlsx", [ROW_A]), year=2025)

        assert response.status_code == 200
        assert response.json()["stored"] is True
        assert PurchaseRepository(db_path).count() == 2  # 둘 다 남는다


class TestConfirmationIsRequired:
    """⛔ 확인 없이 교체하지 않는다 (PM-005)."""

    @pytest.fixture
    def seeded(self, client: TestClient, tmp_path: Path) -> Path:
        """2026년 데이터를 미리 넣어 둡니다."""
        first = _excel(tmp_path / "first.xlsx", [ROW_A, ROW_B])
        assert _upload(client, first).status_code == 200
        return first

    def test_reupload_without_flag_is_409(
        self, client: TestClient, seeded: Path, tmp_path: Path
    ) -> None:
        response = _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]))

        assert response.status_code == 409

    def test_409_body_tells_the_screen_what_to_show(
        self, client: TestClient, seeded: Path, tmp_path: Path
    ) -> None:
        """화면이 팝업을 그릴 수 있도록 필요한 값을 돌려준다."""
        body = _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A])).json()
        detail = body["detail"]

        assert detail["code"] == "EXISTING_PERIOD"
        assert "2026" in detail["message"]
        assert detail["existing_batch_id"] is not None
        assert detail["existing_row_count"] == 2
        assert detail["year"] == 2026

    def test_409_changes_nothing_in_the_database(
        self, client: TestClient, db_path: Path, seeded: Path, tmp_path: Path
    ) -> None:
        """⛔ 409 를 낼 때 **DB 는 전혀 변경되지 않는다.**"""
        before_rows = PurchaseRepository(db_path).count()
        before_batches = len(ImportBatchRepository(db_path).find_all())

        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]))

        assert PurchaseRepository(db_path).count() == before_rows
        assert len(ImportBatchRepository(db_path).find_all()) == before_batches

    def test_existing_batch_stays_active_after_409(
        self, client: TestClient, db_path: Path, seeded: Path, tmp_path: Path
    ) -> None:
        """거부당해도 기존 배치는 ACTIVE 그대로다."""
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]))

        batches = ImportBatchRepository(db_path).find_all()
        assert len(batches) == 1
        assert batches[0].status == STATUS_ACTIVE

    def test_calculation_still_sees_the_old_data(
        self, client: TestClient, db_path: Path, seeded: Path, tmp_path: Path
    ) -> None:
        """계산 대상도 그대로다 — 사용자가 취소한 것과 같은 상태."""
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]))

        found = PurchaseRepository(db_path).find_for_calculation()
        assert len(found) == 2
        assert sum(p.amount for p in found) == Decimal("3000000")


class TestReplaceAfterConfirmation:
    """확인하면 교체한다 (PM-007)."""

    @pytest.fixture
    def seeded(self, client: TestClient, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "first.xlsx", [ROW_A, ROW_B]))

    def test_replace_stores_the_new_file(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        response = _upload(
            client, _excel(tmp_path / "second.xlsx", [ROW_A]), replace_existing=True
        )

        assert response.status_code == 200
        assert response.json()["stored"] is True

    def test_only_the_new_batch_is_calculated(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]), replace_existing=True)

        found = PurchaseRepository(db_path).find_for_calculation()
        assert len(found) == 1
        assert sum(p.amount for p in found) == Decimal("1000000")

    def test_previous_batch_is_superseded_not_deleted(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        """⛔ **물리 삭제하지 않는다** (PM-012 — 이력 보존)."""
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]), replace_existing=True)

        batches = ImportBatchRepository(db_path).find_all()
        statuses = sorted(b.status for b in batches)
        assert statuses == [STATUS_ACTIVE, STATUS_SUPERSEDED]

    def test_old_rows_remain_in_the_table(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        """이전 배치의 행도 테이블에 남는다 — 계산에서만 빠진다."""
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]), replace_existing=True)

        repository = PurchaseRepository(db_path)
        assert repository.count() == 3  # 2 + 1, 삭제되지 않음
        assert len(repository.find_for_calculation()) == 1

    def test_which_file_was_used_is_traceable(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        """어떤 파일이 쓰였는지 이력으로 남는다 (PM-012 의 목적)."""
        _upload(client, _excel(tmp_path / "second.xlsx", [ROW_A]), replace_existing=True)

        names = {b.file_name for b in ImportBatchRepository(db_path).find_all()}
        assert names == {"first.xlsx", "second.xlsx"}


class TestValidationFailureNeverDestroysData:
    """⛔ 검증 실패로 기존 데이터가 사라지지 않는다 (PM-006 · §9)."""

    @pytest.fixture
    def seeded(self, client: TestClient, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "first.xlsx", [ROW_A, ROW_B]))

    def test_bad_file_with_replace_flag_keeps_old_data(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        """**교체를 승인했더라도** 새 파일에 오류가 있으면 기존 데이터를 지키다."""
        bad = _excel(tmp_path / "bad.xlsx", [ROW_A, BAD_ROW])

        body = _upload(client, bad, replace_existing=True).json()

        assert body["stored"] is False
        assert body["error_rows"] == 1
        # 기존 데이터가 그대로다.
        assert len(PurchaseRepository(db_path).find_for_calculation()) == 2

    def test_bad_file_does_not_supersede_the_old_batch(
        self, client: TestClient, db_path: Path, seeded: None, tmp_path: Path
    ) -> None:
        """오류 파일은 이전 배치를 무효화하지 못한다."""
        _upload(client, _excel(tmp_path / "bad.xlsx", [BAD_ROW]), replace_existing=True)

        batches = ImportBatchRepository(db_path).find_all()
        assert len(batches) == 1
        assert batches[0].status == STATUS_ACTIVE

    def test_bad_file_is_reported_not_asked_about(
        self, client: TestClient, seeded: None, tmp_path: Path
    ) -> None:
        """오류 파일이면 **교체 여부를 묻지 않는다** — 검증이 먼저다.

        저장할 수 없는 파일로 "교체하시겠습니까" 를 물으면 사용자가 혼란스럽다.
        """
        response = _upload(client, _excel(tmp_path / "bad.xlsx", [BAD_ROW]))

        assert response.status_code == 200  # 409 가 아니다
        assert response.json()["stored"] is False


class TestServiceLayer:
    """서비스 계층 계약."""

    def test_default_is_not_replacing(self, db_path: Path, tmp_path: Path) -> None:
        """기본값은 **교체하지 않음**이다 — 실수로 교체되지 않도록."""
        service = build_upload_service(db_path)
        good = _excel(tmp_path / "a.xlsx", [ROW_A])
        service.import_file(good, period_start=date(2026, 1, 1), period_end=date(2026, 12, 31))

        with pytest.raises(ExistingPeriodBatchError) as exc_info:
            service.import_file(
                good, period_start=date(2026, 1, 1), period_end=date(2026, 12, 31)
            )

        assert exc_info.value.existing.batch_id is not None
        assert "2026" in str(exc_info.value)

    def test_error_carries_what_the_caller_needs(self, db_path: Path, tmp_path: Path) -> None:
        service = build_upload_service(db_path)
        good = _excel(tmp_path / "a.xlsx", [ROW_A])
        service.import_file(good, period_start=date(2026, 1, 1), period_end=date(2026, 12, 31))

        with pytest.raises(ExistingPeriodBatchError) as exc_info:
            service.import_file(
                good, period_start=date(2026, 1, 1), period_end=date(2026, 12, 31)
            )

        error = exc_info.value
        assert error.period_start == date(2026, 1, 1)
        assert error.period_end == date(2026, 12, 31)
        assert error.existing.file_name == "a.xlsx"
