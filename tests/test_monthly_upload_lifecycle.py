"""STEP 14 — 매월 원본 파일을 올려도 데이터가 섞이지 않는다.

이 시스템은 담당자가 **매월 원본 Excel 을 하나씩 올려** 운영합니다. 그러면
자연히 이런 질문이 생깁니다.

* 3월 데이터와 4월 데이터가 섞이지는 않는가
* 같은 달을 다시 올리면 어느 쪽이 쓰이는가
* 이전 업로드 기록은 남는가
* 원본 행은 여전히 전부 설명되는가

여기서 고정하는 것은 그 답들입니다.

⛔ **업무규칙을 만들지 않습니다.** 금액 0 이하 행의 처리 방식(Q5-8)을 비롯해
어떤 판단도 하지 않습니다. 확인하는 것은 **데이터가 안전하게 관리되는가**
뿐입니다.

⚠️ 데이터는 전부 **합성**입니다. 건수를 정답으로 하드코딩하지 않고, 넣은
행에서 계산해 비교합니다.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.rejection_query import (
    ANY,
    DESCENDING,
    RejectionQuery,
    RejectionQueryError,
)
from procurement.importers.trace_service import ImportTraceService
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED
from procurement.models.import_rejection import REASON_NON_POSITIVE_AMOUNT

#: 월별 기간 ``(시작, 끝)``. ⛔ 화면·테스트가 기간을 만들지 않고 호출자가 준다.
MARCH: tuple[date, date] = (date(2026, 3, 1), date(2026, 3, 31))
APRIL: tuple[date, date] = (date(2026, 4, 1), date(2026, 4, 30))
MAY: tuple[date, date] = (date(2026, 5, 1), date(2026, 5, 31))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "monthly.db"
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
def trace(db_path: Path) -> ImportTraceService:
    return ImportTraceService(
        PurchaseRepository(db_path),
        ImportBatchRepository(db_path),
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


def month(
    service: BatchImportService,
    period: tuple[date, date],
    rows: list[dict[str, Any]],
    *,
    name: str,
) -> BatchImportResult:
    """한 달치 파일을 올립니다."""
    start, end = period
    return service.import_batch(rows, file_name=name, period_start=start, period_end=end)


def normal(count: int, tag: str) -> list[dict[str, Any]]:
    return [row(description=f"{tag} 정상 {index}") for index in range(count)]


def negative(count: int, tag: str) -> list[dict[str, Any]]:
    return [
        row(amount=f"-{index + 1}", description=f"{tag} 음수 {index}") for index in range(count)
    ]


def read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


# ----------------------------------------------------------------------
# 시나리오 1 — 다른 달은 서로 섞이지 않는다
# ----------------------------------------------------------------------
class TestMonthsStaySeparate:
    """작업 C 시나리오 1·3."""

    def test_each_month_keeps_its_own_numbers(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        march = month(service, MARCH, normal(5, "3월") + negative(2, "3월"), name="3월.xlsx")
        april = month(service, APRIL, normal(3, "4월") + negative(1, "4월"), name="4월.xlsx")

        assert march.trace.stored == 5
        assert march.trace.rejected == 2
        assert april.trace.stored == 3
        assert april.trace.rejected == 1

        entries = {entry.batch.file_name: entry for entry in trace.history()}
        assert entries["3월.xlsx"].stored == 5
        assert entries["3월.xlsx"].rejected == 2
        assert entries["4월.xlsx"].stored == 3
        assert entries["4월.xlsx"].rejected == 1

    def test_totals_add_up_across_months(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        rows_march = normal(5, "3월") + negative(2, "3월")
        rows_april = normal(3, "4월") + negative(1, "4월")
        month(service, MARCH, rows_march, name="3월.xlsx")
        month(service, APRIL, rows_april, name="4월.xlsx")

        overview = trace.overview()

        assert overview.source_rows == len(rows_march) + len(rows_april)
        assert overview.stored == 8
        assert overview.rejected == 3

    def test_review_list_shows_every_current_month(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        month(service, MARCH, normal(5, "3월"), name="3월.xlsx")
        month(service, APRIL, normal(3, "4월"), name="4월.xlsx")

        descriptions = [
            item["source"]["description"]
            for item in client.get("/reviews?page=1&page_size=50").json()["items"]
        ]

        assert sum(1 for value in descriptions if value.startswith("3월")) == 5
        assert sum(1 for value in descriptions if value.startswith("4월")) == 3

    def test_three_months_do_not_mix(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        month(service, MARCH, normal(4, "3월") + negative(1, "3월"), name="3월.xlsx")
        month(service, APRIL, normal(3, "4월") + negative(2, "4월"), name="4월.xlsx")
        month(service, MAY, normal(2, "5월") + negative(3, "5월"), name="5월.xlsx")

        entries = {entry.batch.file_name: entry for entry in trace.history()}

        assert [entries[name].stored for name in ("3월.xlsx", "4월.xlsx", "5월.xlsx")] == [4, 3, 2]
        assert [entries[name].rejected for name in ("3월.xlsx", "4월.xlsx", "5월.xlsx")] == [
            1,
            2,
            3,
        ]

    def test_rejections_carry_their_own_batch(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        march = month(service, MARCH, negative(2, "3월"), name="3월.xlsx")
        april = month(service, APRIL, negative(1, "4월"), name="4월.xlsx")

        recorded = ImportRejectionRepository(db_path).find_all()
        by_batch = {item.batch_id for item in recorded}

        assert by_batch == {march.batch.batch_id, april.batch.batch_id}


# ----------------------------------------------------------------------
# 시나리오 2 — 같은 달을 다시 올리면
# ----------------------------------------------------------------------
class TestReupload:
    """작업 C 시나리오 2 · 작업 G 3~7."""

    def _reupload(self, service: BatchImportService) -> tuple[BatchImportResult, ...]:
        first = month(service, APRIL, normal(4, "1차") + negative(3, "1차"), name="4월-1차.xlsx")
        second = month(service, APRIL, normal(2, "2차") + negative(1, "2차"), name="4월-2차.xlsx")
        return first, second

    def test_previous_batch_is_superseded(self, service: BatchImportService, db_path: Path) -> None:
        first, second = self._reupload(service)

        repository = ImportBatchRepository(db_path)
        assert first.batch.batch_id is not None
        assert second.batch.batch_id is not None
        before = repository.find_by_id(first.batch.batch_id)
        after = repository.find_by_id(second.batch.batch_id)
        assert before is not None and after is not None
        assert before.status == STATUS_SUPERSEDED
        assert before.superseded_by == second.batch.batch_id
        assert after.status == STATUS_ACTIVE

    def test_review_list_shows_only_the_new_batch(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        self._reupload(service)

        descriptions = [
            item["source"]["description"]
            for item in client.get("/reviews?page=1&page_size=50").json()["items"]
        ]

        assert all(value.startswith("2차") for value in descriptions)
        assert len(descriptions) == 2

    def test_rejections_do_not_accumulate(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        """⛔ STEP 12 에서 실제로 겪은 문제 — 재업로드마다 미적재만 불어났다."""
        self._reupload(service)

        overview = trace.overview()

        assert overview.stored == 2
        assert overview.rejected == 1
        assert overview.source_rows == 3

    def test_csv_follows_the_current_batch(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        self._reupload(service)

        rows = read_csv(client.get("/imports/trace.csv").content)

        assert len(rows) - 1 == 1
        assert "2차" in " ".join(rows[1])

    def test_previous_records_are_kept(self, service: BatchImportService, db_path: Path) -> None:
        """⛔ 대체되었다고 지우지 않는다 — 조회에서 빠질 뿐이다."""
        self._reupload(service)

        repository = ImportRejectionRepository(db_path)
        assert len(repository.find_all()) == 4
        assert len(repository.find_current()) == 1

    def test_history_keeps_both_uploads(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        self._reupload(service)

        entries = trace.history()

        assert [entry.batch.file_name for entry in entries] == ["4월-2차.xlsx", "4월-1차.xlsx"]
        assert [entry.is_current for entry in entries] == [True, False]

    def test_superseded_entry_keeps_its_own_numbers(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        """대체된 업로드도 그때의 원본·적재·미적재를 그대로 보여준다."""
        self._reupload(service)

        old = next(entry for entry in trace.history() if entry.batch.file_name == "4월-1차.xlsx")

        assert old.source_rows == 7
        assert old.stored == 4
        assert old.rejected == 3
        assert old.unexplained == 0

    def test_other_months_are_untouched(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        """작업 G 5·10 — 올리지 않은 달은 변하지 않는다."""
        month(service, MARCH, normal(5, "3월") + negative(2, "3월"), name="3월.xlsx")
        before = next(entry for entry in trace.history() if entry.batch.file_name == "3월.xlsx")

        self._reupload(service)

        after = next(entry for entry in trace.history() if entry.batch.file_name == "3월.xlsx")
        assert (after.stored, after.rejected, after.source_rows) == (
            before.stored,
            before.rejected,
            before.source_rows,
        )
        assert after.is_current is True


# ----------------------------------------------------------------------
# 작업 G — 데이터 불변성
# ----------------------------------------------------------------------
class TestNothingIsLost:
    """원본 = 적재 + 미적재 + 설명되지 않는 행."""

    def test_equation_holds_for_every_batch(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        month(service, MARCH, normal(6, "3월") + negative(2, "3월"), name="3월.xlsx")
        month(service, APRIL, normal(3, "4월") + negative(4, "4월"), name="4월.xlsx")

        for entry in trace.history():
            assert entry.source_rows is not None
            assert entry.source_rows == entry.stored + entry.rejected + (entry.unexplained or 0)

    def test_unexplained_is_zero(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        """⛔ 0 이 아니면 실패해야 한다 — 행이 사라졌다는 뜻이다."""
        month(service, MARCH, normal(6, "3월") + negative(2, "3월"), name="3월.xlsx")

        assert [entry.unexplained for entry in trace.history()] == [0]

    def test_source_row_count_is_measured_not_derived(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """원본 행 수는 **세어서** 기록한다 — 합계로 되계산하지 않는다.

        되계산하면 ``unexplained`` 가 늘 0 이 되어 아무것도 검증하지 못한다.
        """
        rows = normal(4, "3월") + negative(3, "3월")
        result = month(service, MARCH, rows, name="3월.xlsx")

        assert result.batch.batch_id is not None
        saved = ImportBatchRepository(db_path).find_by_id(result.batch.batch_id)
        assert saved is not None
        assert saved.source_row_count == len(rows)

    def test_older_batches_report_unknown_not_zero(self, db_path: Path) -> None:
        """이 값을 기록하기 전에 만들어진 배치는 ``None`` 이다 — 0 이 아니다."""
        from decimal import Decimal

        from procurement.models.import_batch import ImportBatch

        repository = ImportBatchRepository(db_path)
        saved = repository.insert(
            ImportBatch(file_name="옛날.xlsx", period_start=MARCH[0], period_end=MARCH[1])
        )
        assert saved.batch_id is not None
        repository.update_totals(saved.batch_id, 10, Decimal("100"))

        found = repository.find_by_id(saved.batch_id)
        assert found is not None
        assert found.source_row_count is None
        assert found.rejected_hint is None

    def test_csv_and_screen_agree(self, service: BatchImportService, client: TestClient) -> None:
        """작업 G 8·9 — 건수와 원본 행 번호가 모두 일치한다."""
        month(service, APRIL, normal(3, "4월") + negative(5, "4월"), name="4월.xlsx")

        api = client.get("/imports/trace").json()
        listed = client.get("/imports/rejections?page=1&page_size=500").json()
        exported = read_csv(client.get("/imports/trace.csv").content)

        assert api["rejected"] == listed["total"] == len(exported) - 1
        assert [item["row_number"] for item in listed["items"]] == [
            int(line[0]) for line in exported[1:]
        ]


# ----------------------------------------------------------------------
# 작업 A·B — 업로드 이력
# ----------------------------------------------------------------------
class TestHistoryApi:
    """``GET /imports/batches``."""

    def test_empty_history(self, client: TestClient) -> None:
        body = client.get("/imports/batches").json()

        assert body == {"items": [], "total": 0, "current": 0}

    def test_lists_newest_first(self, service: BatchImportService, client: TestClient) -> None:
        month(service, MARCH, normal(2, "3월"), name="3월.xlsx")
        month(service, APRIL, normal(2, "4월"), name="4월.xlsx")

        items = client.get("/imports/batches").json()["items"]

        assert [item["file_name"] for item in items] == ["4월.xlsx", "3월.xlsx"]

    def test_entry_carries_what_the_operator_needs(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        month(service, APRIL, normal(3, "4월") + negative(2, "4월"), name="4월.xlsx")

        item = client.get("/imports/batches").json()["items"][0]

        assert item["period_start"] == "2026-04-01"
        assert item["period_end"] == "2026-04-30"
        assert item["uploaded_at"]
        assert item["source_rows"] == 5
        assert item["stored"] == 3
        assert item["rejected"] == 2
        assert item["unexplained"] == 0
        assert item["is_current"] is True

    def test_status_uses_the_existing_lifecycle(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 새 상태값을 만들지 않았다."""
        month(service, APRIL, normal(2, "1차"), name="1차.xlsx")
        month(service, APRIL, normal(2, "2차"), name="2차.xlsx")

        statuses = {item["status"] for item in client.get("/imports/batches").json()["items"]}

        assert statuses == {STATUS_ACTIVE, STATUS_SUPERSEDED}

    def test_counts_current_batches(self, service: BatchImportService, client: TestClient) -> None:
        month(service, MARCH, normal(2, "3월"), name="3월.xlsx")
        month(service, APRIL, normal(2, "1차"), name="4월-1차.xlsx")
        month(service, APRIL, normal(2, "2차"), name="4월-2차.xlsx")

        body = client.get("/imports/batches").json()

        assert body["total"] == 3
        assert body["current"] == 2

    def test_detail_of_one_upload(self, service: BatchImportService, client: TestClient) -> None:
        result = month(service, APRIL, normal(2, "4월") + negative(3, "4월"), name="4월.xlsx")

        detail = client.get(f"/imports/batches/{result.batch.batch_id}").json()

        assert detail["stored"] == 2
        assert detail["rejected"] == 3
        assert detail["reasons"][0]["reason"] == REASON_NON_POSITIVE_AMOUNT
        assert detail["reasons"][0]["count"] == 3

    def test_unknown_batch_is_404(self, client: TestClient) -> None:
        assert client.get("/imports/batches/9999").status_code == 404

    def test_no_delete_or_restore_endpoint(self, client: TestClient) -> None:
        """⛔ 배치 삭제·복구 기능을 만들지 않았다 (지시 3번)."""
        paths = client.get("/openapi.json").json()["paths"]

        for path, methods in paths.items():
            if path.startswith("/imports"):
                assert set(methods) <= {"get", "post"}, (path, methods)
            assert "restore" not in path


# ----------------------------------------------------------------------
# 작업 E — 미적재 행 조회
# ----------------------------------------------------------------------
class TestRejectionSearch:
    """검색 · 필터 · 정렬 · 페이지. ⛔ 업무 판단이 아니다."""

    @pytest.fixture
    def loaded(self, service: BatchImportService) -> None:
        month(
            service,
            APRIL,
            [
                row(amount="-100", description="1월 임대료"),
                row(amount="-5000", description="주차비"),
                row(amount="-30", description="통신비"),
                row(amount="", description="금액 없음"),
                row(amount="-700", description="임대료 재청구"),
            ],
            name="4월.xlsx",
        )

    def test_all_rows_by_default(self, loaded: None, client: TestClient) -> None:
        body = client.get("/imports/rejections").json()

        assert body["total"] == 5
        assert len(body["items"]) == 5

    def test_search_by_description(self, loaded: None, client: TestClient) -> None:
        body = client.get("/imports/rejections?search=임대료").json()

        assert body["total"] == 2

    def test_search_ignores_spacing(self, loaded: None, client: TestClient) -> None:
        assert (
            client.get("/imports/rejections?search=1월 임대료").json()["total"]
            == client.get("/imports/rejections?search=1월임대료").json()["total"]
            == 1
        )

    def test_search_by_row_number(self, loaded: None, client: TestClient) -> None:
        body = client.get("/imports/rejections?search=2").json()

        assert body["total"] >= 1
        assert any(item["row_number"] == 2 for item in body["items"])

    def test_filter_by_reason(self, loaded: None, client: TestClient) -> None:
        body = client.get(f"/imports/rejections?reason={REASON_NON_POSITIVE_AMOUNT}").json()

        assert 0 < body["total"] < 5
        assert {item["reason"] for item in body["items"]} == {REASON_NON_POSITIVE_AMOUNT}

    def test_unknown_reason_is_refused(self, client: TestClient) -> None:
        """⛔ 조용히 전체를 보여주지 않는다 — 고른 조건과 다른 목록이 된다."""
        assert client.get("/imports/rejections?reason=자동제외").status_code == 422

    def test_sort_by_amount(self, loaded: None, client: TestClient) -> None:
        body = client.get("/imports/rejections?sort=amount&direction=asc").json()
        amounts = [item["amount"] for item in body["items"] if item["amount"] is not None]

        assert amounts == sorted(amounts, key=float)

    def test_missing_amount_sorts_last_in_both_directions(
        self, loaded: None, client: TestClient
    ) -> None:
        """⛔ 값이 없는 행이 내림차순에서 맨 앞으로 올라오면 안 된다."""
        for direction in ("asc", "desc"):
            items = client.get(f"/imports/rejections?sort=amount&direction={direction}").json()[
                "items"
            ]
            assert items[-1]["amount"] is None, direction

    def test_sort_by_row_number_descending(self, loaded: None, client: TestClient) -> None:
        items = client.get(f"/imports/rejections?sort=row_number&direction={DESCENDING}").json()[
            "items"
        ]
        numbers = [item["row_number"] for item in items]

        assert numbers == sorted(numbers, reverse=True)

    def test_pagination_splits_without_overlap(self, loaded: None, client: TestClient) -> None:
        first = client.get("/imports/rejections?page=1&page_size=2").json()
        second = client.get("/imports/rejections?page=2&page_size=2").json()

        assert first["total"] == second["total"] == 5
        assert first["total_pages"] == 3
        assert first["has_next"] is True
        assert second["has_previous"] is True
        assert {item["row_number"] for item in first["items"]}.isdisjoint(
            {item["row_number"] for item in second["items"]}
        )

    def test_page_size_is_capped(self, client: TestClient) -> None:
        assert client.get("/imports/rejections?page_size=5000").status_code == 422

    def test_filters_do_not_change_the_data(
        self, loaded: None, client: TestClient, db_path: Path
    ) -> None:
        """⛔ 조회는 아무것도 바꾸지 않는다."""
        before = ImportRejectionRepository(db_path).find_all()

        client.get("/imports/rejections?search=임대료&sort=amount&direction=desc")

        assert ImportRejectionRepository(db_path).find_all() == before

    def test_query_rejects_bad_values(self) -> None:
        with pytest.raises(RejectionQueryError):
            RejectionQuery(sort="score")
        with pytest.raises(RejectionQueryError):
            RejectionQuery(direction="위로")
        with pytest.raises(RejectionQueryError):
            RejectionQuery(page=0)
        with pytest.raises(RejectionQueryError):
            RejectionQuery(page_size=0)
        with pytest.raises(RejectionQueryError):
            RejectionQuery(reason="실적제외")

    def test_default_query_is_unfiltered(self, loaded: None, trace: ImportTraceService) -> None:
        page = trace.search_rejections(RejectionQuery(reason=ANY))

        assert page.total == 5


class TestNoBusinessRuleLeaked:
    """⛔ 이번 STEP 에서 업무 판단이 새어 들어가지 않았다."""

    def test_no_approval_or_exclusion_endpoint(self, client: TestClient) -> None:
        """미적재 행을 승인·제외·포함시키는 **동작**이 없다.

        ``/imports/rejections`` 은 기록을 가리키는 **이름**이지 동작이 아니므로,
        낱말이 섞여 들어가는 것을 피하려고 경로 조각 단위로 본다.
        """
        paths = client.get("/openapi.json").json()["paths"]
        actions = {"approve", "exclude", "include", "accept", "reject", "confirm-rejection"}

        for path in paths:
            segments = {segment.lower() for segment in path.split("/") if segment}
            assert segments.isdisjoint(actions), path

    def test_rejected_rows_never_enter_the_review_list(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        month(service, APRIL, normal(2, "4월") + negative(3, "4월"), name="4월.xlsx")

        descriptions = [
            item["source"]["description"]
            for item in client.get("/reviews?page=1&page_size=50").json()["items"]
        ]

        assert all("음수" not in value for value in descriptions)

    def test_progress_counts_only_stored_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        month(service, APRIL, normal(4, "4월") + negative(3, "4월"), name="4월.xlsx")

        assert client.get("/reviews/progress").json()["total"] == 4

    def test_reason_labels_stay_factual(self) -> None:
        from procurement.models.import_rejection import REJECTION_REASON_LABELS

        for label in REJECTION_REASON_LABELS.values():
            for banned in ("제외", "부적합", "무시", "승인"):
                assert banned not in label, label
