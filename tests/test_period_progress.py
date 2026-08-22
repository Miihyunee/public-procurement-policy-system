"""STEP 16 — 기간별 검토 진행 상황.

담당자가 기간을 고를 때 "그 달을 얼마나 검토했는지" 를 함께 보게 합니다.

    2026-03 · 120 / 471

⛔ **업무 판단이 아닙니다.** 지금 DB 에 저장된 확정 건수를 그대로 셀 뿐이며,
몇 % 면 위험/적정 같은 등급을 만들지 않습니다.

⛔ **미적재 행을 분모에 더하지 않습니다.** 아직 검토 대상 DB 에 들어오지
않았고, 어떻게 처리할지는 고객 확인 사항입니다(Q5-8).

⚠️ 데이터는 전부 **합성**입니다. 건수는 넣은 행에서 계산해 비교합니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import build_import_trace_service, create_app
from procurement.core.purchase_type import SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.trace_service import ImportTraceService

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "progress.db"
    bootstrap(path)
    return path


@pytest.fixture
def service(db_path: Path) -> BatchImportService:
    return BatchImportService(
        PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path)),
        ImportBatchRepository(db_path),
        PurchaseRepository(db_path),
        ImportRejectionRepository(db_path),
    )


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="resolution_date"))


def row(*, amount: object = "1000000", description: str = "합성 적요") -> dict[str, Any]:
    return {
        "business_no": "111-11-11111",
        "company_name": "합성거래처",
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-20",
        "resolution_date": "2026-03-25",
        "issue_date": "2026-03-10",
        "description": description,
        "budget_account": "임차료",
        "amount": amount,
    }


def upload(
    service: BatchImportService,
    label: str,
    *,
    normal: int,
    rejected: int,
    tag: str | None = None,
    name: str | None = None,
) -> BatchImportResult:
    mark = tag or label
    start, end = MONTHS[label]
    rows = [row(description=f"{mark} 적요 {index}") for index in range(normal)]
    rows += [
        row(amount=f"-{index + 1}", description=f"{mark} 음수 {index}") for index in range(rejected)
    ]
    return service.import_batch(
        rows, file_name=name or f"{label}.xlsx", period_start=start, period_end=end
    )


@pytest.fixture
def loaded(service: BatchImportService) -> dict[str, Any]:
    """세 달치. 각 달의 원본/적재/미적재 계획을 함께 돌려준다."""
    plan = {"2026-01": (5, 2), "2026-02": (4, 1), "2026-03": (6, 3)}
    ids: dict[str, Any] = {"plan": plan}
    for label, (normal, rejected) in plan.items():
        result = upload(service, label, normal=normal, rejected=rejected)
        ids[label] = result.batch.batch_id
    return ids


def periods(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get("/imports/periods").json()
    return {item["label"]: item for item in body["items"]}


def confirm_some(client: TestClient, batch_id: int, count: int) -> list[int]:
    """그 기간의 앞 ``count`` 건을 확정합니다."""
    items = client.get(f"/reviews?page=1&page_size=100&batch_id={batch_id}").json()["items"]
    ids = [item["source"]["purchase_id"] for item in items[:count]]
    for purchase_id in ids:
        response = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "합성담당"},
        )
        assert response.status_code == 200
    return ids


# ----------------------------------------------------------------------
# 진행률 집계
# ----------------------------------------------------------------------
class TestPeriodProgress:
    """지시 §9 — 지금 DB 상태를 단순 집계한다."""

    def test_nothing_confirmed_yet(self, loaded: dict[str, Any], client: TestClient) -> None:
        found = periods(client)

        for label, (normal, _) in loaded["plan"].items():
            assert found[label]["stored"] == normal, label
            assert found[label]["confirmed"] == 0, label
            assert found[label]["pending"] == normal, label

    def test_confirmed_count_follows_the_database(
        self, loaded: dict[str, Any], client: TestClient
    ) -> None:
        confirm_some(client, loaded["2026-03"], 2)

        march = periods(client)["2026-03"]

        assert march["confirmed"] == 2
        assert march["pending"] == march["stored"] - 2

    def test_other_periods_are_untouched(self, loaded: dict[str, Any], client: TestClient) -> None:
        confirm_some(client, loaded["2026-03"], 2)

        found = periods(client)

        assert found["2026-01"]["confirmed"] == 0
        assert found["2026-02"]["confirmed"] == 0

    def test_pending_is_stored_minus_confirmed(
        self, loaded: dict[str, Any], client: TestClient
    ) -> None:
        confirm_some(client, loaded["2026-02"], 3)

        for item in periods(client).values():
            assert item["pending"] == item["stored"] - item["confirmed"]

    def test_whole_totals_match_the_sum_of_periods(
        self, loaded: dict[str, Any], client: TestClient
    ) -> None:
        confirm_some(client, loaded["2026-01"], 1)
        confirm_some(client, loaded["2026-03"], 4)

        found = periods(client).values()
        progress = client.get("/reviews/progress").json()

        assert sum(item["stored"] for item in found) == progress["total"]
        assert sum(item["confirmed"] for item in found) == progress["confirmed"]

    def test_reopened_is_not_counted_as_confirmed(
        self, loaded: dict[str, Any], client: TestClient
    ) -> None:
        ids = confirm_some(client, loaded["2026-03"], 2)
        client.post(f"/reviews/{ids[0]}/reopen", json={})

        march = periods(client)["2026-03"]

        assert march["confirmed"] == 1

    def test_service_without_reviews_reports_zero(self, db_path: Path) -> None:
        """검토 저장소를 넣지 않으면 확정 건수만 0 이고 나머지는 그대로다."""
        service = ImportTraceService(
            PurchaseRepository(db_path),
            ImportBatchRepository(db_path),
            ImportRejectionRepository(db_path),
        )

        assert service.periods() == []


class TestRejectedRowsStayOutOfTheDenominator:
    """지시 §10·§11 — 미적재 행은 분모에 들어가지 않는다."""

    def test_stored_excludes_rejected(self, loaded: dict[str, Any], client: TestClient) -> None:
        found = periods(client)

        for label, (normal, rejected) in loaded["plan"].items():
            assert found[label]["stored"] == normal, label
            assert found[label]["rejected"] == rejected, label
            # ⛔ 분모는 적재 건수다. 원본(normal + rejected)이 아니다.
            assert found[label]["stored"] != normal + rejected, label

    def test_pending_excludes_rejected(self, loaded: dict[str, Any], client: TestClient) -> None:
        march = periods(client)["2026-03"]

        assert march["pending"] + march["confirmed"] == march["stored"]
        assert march["rejected"] > 0

    def test_whole_progress_excludes_rejected(
        self, loaded: dict[str, Any], client: TestClient, db_path: Path
    ) -> None:
        stored = len(PurchaseRepository(db_path).find_for_calculation(None))
        rejected = len(ImportRejectionRepository(db_path).find_current())

        progress = client.get("/reviews/progress").json()

        assert progress["total"] == stored
        assert rejected > 0

    def test_no_verdict_words_in_the_response(
        self, loaded: dict[str, Any], client: TestClient
    ) -> None:
        """⛔ 진행률에 등급·판정을 붙이지 않는다 (지시 §15)."""
        body = client.get("/imports/periods").text

        for banned in ("위험", "주의", "적정", "우수", "충족", "미달", "제외"):
            assert banned not in body, banned

    def test_no_threshold_field_exists(self, loaded: dict[str, Any], client: TestClient) -> None:
        item = periods(client)["2026-03"]

        for banned in ("level", "grade", "status_color", "warning", "threshold"):
            assert banned not in item, banned


class TestSupersededIsExcluded:
    """지시 §13 — 대체된 배치의 확정 이력이 섞이지 않는다."""

    def _reupload(self, service: BatchImportService, client: TestClient) -> dict[str, int]:
        first = upload(service, "2026-03", normal=4, rejected=1)
        assert first.batch.batch_id is not None
        confirm_some(client, first.batch.batch_id, 3)

        second = upload(
            service, "2026-03", normal=2, rejected=1, tag="3월재", name="2026-03-재.xlsx"
        )
        assert second.batch.batch_id is not None
        return {"old": first.batch.batch_id, "new": second.batch.batch_id}

    def test_one_period_after_reupload(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        ids = self._reupload(service, client)

        found = periods(client)

        assert list(found) == ["2026-03"]
        assert found["2026-03"]["batch_id"] == ids["new"]

    def test_old_confirmations_are_not_counted(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 대체 전에 확정한 3건이 새 배치의 진행률에 섞이면 안 된다."""
        self._reupload(service, client)

        march = periods(client)["2026-03"]

        assert march["stored"] == 2
        assert march["confirmed"] == 0
        assert march["pending"] == 2

    def test_whole_progress_excludes_the_old_batch(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        self._reupload(service, client)

        progress = client.get("/reviews/progress").json()

        assert progress["total"] == 2
        assert progress["confirmed"] == 0

    def test_confirming_in_the_new_batch_counts(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        ids = self._reupload(service, client)

        confirm_some(client, ids["new"], 1)

        assert periods(client)["2026-03"]["confirmed"] == 1


class TestProgressMovesWithConfirmAndUndo:
    """지시 §21 [C] — 확정하면 늘고, 되돌리면 줄어든다."""

    def test_confirm_then_undo(self, loaded: dict[str, Any], client: TestClient) -> None:
        batch = loaded["2026-03"]
        before = periods(client)["2026-03"]["confirmed"]

        ids = confirm_some(client, batch, 1)
        after_confirm = periods(client)["2026-03"]["confirmed"]

        client.post(f"/reviews/{ids[0]}/reopen", json={})
        after_undo = periods(client)["2026-03"]["confirmed"]

        assert before == 0
        assert after_confirm == 1
        assert after_undo == 0

    def test_stored_never_moves(self, loaded: dict[str, Any], client: TestClient) -> None:
        """분모는 확정 여부와 무관하다."""
        batch = loaded["2026-03"]
        before = periods(client)["2026-03"]["stored"]

        ids = confirm_some(client, batch, 2)
        client.post(f"/reviews/{ids[0]}/reopen", json={})

        assert periods(client)["2026-03"]["stored"] == before


class TestServiceLayer:
    """조립과 계층."""

    def test_composition_root_injects_the_review_repository(self, db_path: Path) -> None:
        service = build_import_trace_service(db_path)

        assert isinstance(service, ImportTraceService)

    def test_progress_is_read_only(
        self, loaded: dict[str, Any], client: TestClient, db_path: Path
    ) -> None:
        """⛔ 진행률을 세는 것이 데이터를 바꾸지 않는다."""
        reviews = ReviewRepository(db_path)
        before = reviews.find_all()

        client.get("/imports/periods")

        assert reviews.find_all() == before
