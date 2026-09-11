"""STEP 20 — 검토 **변경 이력** CSV 와 기간(batch_id) 연동.

``/reviews/export.csv`` 는 구매 한 건의 **현재 상태**가 한 줄이고,
``/reviews/history.csv`` 는 그 구매에 있었던 **변경 한 번**이 한 줄입니다.
확정 → 취소 → 재확정이면 세 줄이 그대로 남습니다.

.. warning::
    ⛔ **이력의 의미를 바꾸지 않습니다.** 최신만 남기거나, 취소 기록을 빼거나,
    중복을 지우지 않습니다. 기록이 곧 근거이기 때문입니다.

⛔ 기간은 날짜로 다시 계산하지 않고 **현재 배치(batch_id)** 로만 좁힙니다 —
어느 날짜로 기간을 나눌지는 아직 확정되지 않은 업무규칙입니다(Q5-9). 그래서
대체된(SUPERSEDED) 배치의 이력은 현재 기간 결과에 섞이지 않습니다.

⚠️ 데이터는 전부 **합성**입니다. 건수는 넣은 행에서 계산해 비교합니다.
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
from procurement.core.purchase_type import GOODS, PURCHASE_TYPE_LABELS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.reviews.export import EXPORT_COLUMNS, HISTORY_COLUMNS

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
}


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "history-export.db"
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
    rejected: int = 0,
    tag: str | None = None,
) -> BatchImportResult:
    mark = tag or label
    start, end = MONTHS[label]
    rows = [row(description=f"{mark} 적요 {index}") for index in range(normal)]
    rows += [
        row(amount=f"-{index + 1}", description=f"{mark} 음수 {index}") for index in range(rejected)
    ]
    return service.import_batch(rows, file_name=f"{label}.xlsx", period_start=start, period_end=end)


def batch_of(result: BatchImportResult) -> int:
    """새로 만들어진 배치 ID. ⚠️ 저장 후에는 항상 값이 있습니다."""
    batch_id = result.batch.batch_id
    assert batch_id is not None
    return batch_id


def ids_of(client: TestClient, batch_id: int) -> list[int]:
    body = client.get(f"/reviews?batch_id={batch_id}&page=1&page_size=100").json()
    return [item["source"]["purchase_id"] for item in body["items"]]


def confirm(
    client: TestClient, purchase_id: int, kind: str = SERVICE, who: str = "합성담당"
) -> None:
    response = client.put(
        f"/reviews/{purchase_id}",
        json={"final_purchase_type": kind, "reviewed_by": who},
    )
    assert response.status_code == 200, response.text


def reopen(client: TestClient, purchase_id: int, who: str = "합성담당") -> None:
    response = client.post(f"/reviews/{purchase_id}/reopen", json={"reopened_by": who})
    assert response.status_code == 200, response.text


def history_csv(client: TestClient, query: str = "") -> bytes:
    response = client.get("/reviews/history.csv" + query)
    assert response.status_code == 200, response.text
    content: bytes = response.content
    return content


def rows_of(raw: bytes) -> list[list[str]]:
    """머리글을 뺀 데이터 행. ⚠️ 바이트로 읽어 CRLF 가 보존되게 합니다."""
    text = raw.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))[1:]


def _function_body(page: str, name: str) -> str:
    """``function name(`` 부터 짝이 맞는 닫는 중괄호까지."""
    start = page.index("function " + name + "(")
    depth = 0
    started = False
    for index in range(start, len(page)):
        char = page[index]
        if char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if started and depth == 0:
                return page[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했습니다")


# ----------------------------------------------------------------------
# A. batch_id 없음 — 기존 동작
# ----------------------------------------------------------------------
class TestWithoutBatchId:
    """지시 §4-2 — 생략하면 전체."""

    def test_empty_database_gives_only_the_header(self, client: TestClient) -> None:
        raw = history_csv(client)

        assert rows_of(raw) == []

    def test_all_current_batches_are_included(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        first = batch_of(upload(service, "2026-01", normal=3))
        second = batch_of(upload(service, "2026-02", normal=2))
        confirm(client, ids_of(client, first)[0])
        confirm(client, ids_of(client, second)[0])

        found = rows_of(history_csv(client))

        assert len(found) == 2

    def test_nothing_confirmed_means_no_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 이력을 만들어 내지 않는다 — 아무 일도 없었으면 빈 표다."""
        upload(service, "2026-01", normal=3)

        assert rows_of(history_csv(client)) == []


# ----------------------------------------------------------------------
# B·C. batch_id 지정 / 잘못된 값
# ----------------------------------------------------------------------
class TestWithBatchId:
    """지시 §4-3 — 그 기간의 이력만."""

    def test_only_that_period(self, service: BatchImportService, client: TestClient) -> None:
        january = batch_of(upload(service, "2026-01", normal=3))
        february = batch_of(upload(service, "2026-02", normal=2))
        confirm(client, ids_of(client, january)[0])
        confirm(client, ids_of(client, january)[1])
        confirm(client, ids_of(client, february)[0])

        found = rows_of(history_csv(client, f"?batch_id={january}"))

        assert len(found) == 2
        assert {line[1] for line in found} == {str(january)}

    def test_other_period_is_untouched(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        january = batch_of(upload(service, "2026-01", normal=3))
        february = batch_of(upload(service, "2026-02", normal=2))
        confirm(client, ids_of(client, january)[0])

        assert rows_of(history_csv(client, f"?batch_id={february}")) == []

    def test_period_rows_add_up_to_the_whole(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        january = batch_of(upload(service, "2026-01", normal=3))
        february = batch_of(upload(service, "2026-02", normal=2))
        confirm(client, ids_of(client, january)[0])
        confirm(client, ids_of(client, february)[0])
        confirm(client, ids_of(client, february)[1])

        whole = len(rows_of(history_csv(client)))
        parts = sum(
            len(rows_of(history_csv(client, f"?batch_id={batch}"))) for batch in (january, february)
        )

        assert whole == parts

    def test_unknown_batch_gives_an_empty_table(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """지시 §15-C — 기존 규약 그대로. 없는 배치는 0건이지 오류가 아니다."""
        upload(service, "2026-01", normal=2)

        assert rows_of(history_csv(client, "?batch_id=999999")) == []

    def test_zero_is_rejected_like_the_review_list(self, client: TestClient) -> None:
        """``batch_id=0`` 은 기존 화면 경로와 **같은** 규약(422)을 따른다.

        ⚠️ ``page`` 를 함께 보냅니다. ``page`` 없이 부르면 ``limit``/``offset``
        을 쓰던 **예전 경로**로 빠지는데, 그 경로는 ``batch_id`` 를 아예 보지
        않습니다(STEP 20 발견사항). 화면은 항상 ``page`` 를 보내므로 여기서도
        화면과 같은 호출로 비교합니다.
        """
        listed = client.get("/reviews?batch_id=0&page=1&page_size=20")
        exported = client.get("/reviews/history.csv?batch_id=0")

        assert listed.status_code == 422
        assert exported.status_code == 422

    def test_the_other_csvs_agree_on_zero(self, client: TestClient) -> None:
        """검토 CSV · 미적재 CSV 와도 같은 규약이다."""
        for path in (
            "/reviews/export.csv?batch_id=0",
            "/imports/trace.csv?batch_id=0",
            "/reviews/history.csv?batch_id=0",
        ):
            assert client.get(path).status_code == 422, path

    def test_non_number_is_rejected(self, client: TestClient) -> None:
        assert client.get("/reviews/history.csv?batch_id=abc").status_code == 422


# ----------------------------------------------------------------------
# D·E. superseded / 재업로드
# ----------------------------------------------------------------------
class TestSupersededNeverLeaks:
    """지시 §5 — 대체된 배치의 이력이 현재 기간에 섞이면 안 된다."""

    def test_reupload_period_shows_only_the_current_batch(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        old = batch_of(upload(service, "2026-03", normal=4))
        confirm(client, ids_of(client, old)[0])
        confirm(client, ids_of(client, old)[1])
        new = batch_of(upload(service, "2026-03", normal=4, tag="재업로드"))
        confirm(client, ids_of(client, new)[0])

        found = rows_of(history_csv(client, f"?batch_id={new}"))

        assert len(found) == 1, "대체된 배치의 이력이 섞였다"
        assert found[0][1] == str(new)

    def test_superseded_batch_asked_directly_is_empty(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """``/reviews?batch_id=<대체됨>`` 이 0건인 것과 같은 규약."""
        old = batch_of(upload(service, "2026-03", normal=4))
        confirm(client, ids_of(client, old)[0])
        upload(service, "2026-03", normal=4, tag="재업로드")

        listed = client.get(f"/reviews?batch_id={old}&page=1&page_size=20").json()

        assert listed["page"]["total"] == 0
        assert rows_of(history_csv(client, f"?batch_id={old}")) == []

    def test_whole_export_also_drops_superseded_history(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """기간을 안 골라도 대체된 배치는 현재 데이터가 아니다."""
        old = batch_of(upload(service, "2026-03", normal=4))
        confirm(client, ids_of(client, old)[0])
        confirm(client, ids_of(client, old)[1])
        upload(service, "2026-03", normal=4, tag="재업로드")

        assert rows_of(history_csv(client)) == []

    def test_old_records_are_not_deleted(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 기록을 지우지 않는다 — 현재 집계에서 빠질 뿐이다."""
        old = batch_of(upload(service, "2026-03", normal=4))
        kept = ids_of(client, old)[0]
        confirm(client, kept)
        upload(service, "2026-03", normal=4, tag="재업로드")

        entries = client.get(f"/reviews/{kept}/history").json()["items"]

        assert len(entries) >= 1


# ----------------------------------------------------------------------
# F·G. 확정 / Undo
# ----------------------------------------------------------------------
class TestConfirmAndUndoAreBothRecorded:
    """지시 §6 · §11 — 이력의 의미를 바꾸지 않는다."""

    def test_confirm_appears(self, service: BatchImportService, client: TestClient) -> None:
        batch = batch_of(upload(service, "2026-03", normal=3))
        confirm(client, ids_of(client, batch)[0])

        found = rows_of(history_csv(client, f"?batch_id={batch}"))

        assert len(found) == 1
        assert found[0][6] == "CONFIRMED"

    def test_undo_adds_a_row_and_removes_nothing(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 취소해도 확정 기록이 지워지지 않는다 — 줄이 하나 늘어난다."""
        batch = batch_of(upload(service, "2026-03", normal=3))
        target = ids_of(client, batch)[0]
        confirm(client, target)
        before = rows_of(history_csv(client, f"?batch_id={batch}"))
        reopen(client, target)

        after = rows_of(history_csv(client, f"?batch_id={batch}"))

        assert len(after) == len(before) + 1
        assert [line[6] for line in after] == ["CONFIRMED", "REOPENED"]

    def test_confirm_undo_confirm_keeps_all_three(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        batch = batch_of(upload(service, "2026-03", normal=3))
        target = ids_of(client, batch)[0]
        confirm(client, target, SERVICE)
        reopen(client, target)
        confirm(client, target, GOODS)

        found = rows_of(history_csv(client, f"?batch_id={batch}"))

        assert [line[6] for line in found] == ["CONFIRMED", "REOPENED", "CONFIRMED"]

    def test_type_change_keeps_both_sides(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        batch = batch_of(upload(service, "2026-03", normal=3))
        target = ids_of(client, batch)[0]
        confirm(client, target, SERVICE)
        reopen(client, target)
        confirm(client, target, GOODS)

        last = rows_of(history_csv(client, f"?batch_id={batch}"))[-1]

        assert last[9] == PURCHASE_TYPE_LABELS[SERVICE]
        assert last[10] == PURCHASE_TYPE_LABELS[GOODS]

    def test_actor_is_recorded_as_given(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 비어 있으면 가짜 이름을 채우지 않는다."""
        batch = batch_of(upload(service, "2026-03", normal=3))
        ids = ids_of(client, batch)
        confirm(client, ids[0], who="김담당")
        client.put(f"/reviews/{ids[1]}", json={"final_purchase_type": SERVICE})

        found = {line[0]: line[8] for line in rows_of(history_csv(client, f"?batch_id={batch}"))}

        assert found[str(ids[0])] == "김담당"
        assert found[str(ids[1])] == ""

    def test_rows_are_ordered_by_purchase_then_time(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        batch = batch_of(upload(service, "2026-03", normal=3))
        ids = ids_of(client, batch)
        confirm(client, ids[1])
        confirm(client, ids[0])
        reopen(client, ids[0])

        found = rows_of(history_csv(client, f"?batch_id={batch}"))

        assert [line[0] for line in found] == [str(ids[0]), str(ids[0]), str(ids[1])]
        assert [line[6] for line in found[:2]] == ["CONFIRMED", "REOPENED"]


# ----------------------------------------------------------------------
# H. CSV 규격
# ----------------------------------------------------------------------
class TestCsvConventions:
    """지시 §7 — 기존 CSV 규약을 그대로 따른다."""

    def test_columns_are_fixed(self, service: BatchImportService, client: TestClient) -> None:
        batch = batch_of(upload(service, "2026-03", normal=2))
        confirm(client, ids_of(client, batch)[0])
        text = history_csv(client).decode("utf-8-sig")

        header = next(csv.reader(io.StringIO(text, newline="")))

        assert tuple(header) == HISTORY_COLUMNS
        assert len(header) == 12

    def test_it_is_a_different_table_from_the_review_export(self) -> None:
        """⚠️ 두 CSV 를 섞지 않는다 — 현재 상태 표와 변경 기록 표는 다르다."""
        assert HISTORY_COLUMNS != EXPORT_COLUMNS

    def test_byte_order_mark(self, service: BatchImportService, client: TestClient) -> None:
        upload(service, "2026-03", normal=1)

        assert history_csv(client).startswith(b"\xef\xbb\xbf")

    def test_lines_end_with_crlf(self, service: BatchImportService, client: TestClient) -> None:
        batch = batch_of(upload(service, "2026-03", normal=2))
        confirm(client, ids_of(client, batch)[0])

        raw = history_csv(client)

        assert raw.count(b"\r\n") == 2, "머리글 1 + 데이터 1"

    def test_formula_injection_is_blocked(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """적요·메모는 사람이 쓴 자유 문자열이다."""
        start, end = MONTHS["2026-03"]
        service.import_batch(
            [row(description="=1+1 위험 적요")],
            file_name="2026-03.xlsx",
            period_start=start,
            period_end=end,
        )
        target = client.get("/reviews?page=1&page_size=20").json()["items"][0]["source"]
        client.put(
            f"/reviews/{target['purchase_id']}",
            json={"final_purchase_type": SERVICE, "review_note": "@위험 메모"},
        )

        line = rows_of(history_csv(client))[0]

        assert line[3].startswith("'=")
        assert line[11].startswith("'@")

    def test_content_type_and_filename(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=1)

        response = client.get("/reviews/history.csv")

        assert response.headers["content-type"].startswith("text/csv")
        assert "review-history.csv" in response.headers["content-disposition"]

    def test_review_export_is_unchanged(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 기존 검토 CSV 를 건드리지 않았다."""
        batch = batch_of(upload(service, "2026-03", normal=2))
        confirm(client, ids_of(client, batch)[0])
        text = client.get("/reviews/export.csv").content.decode("utf-8-sig")

        header = next(csv.reader(io.StringIO(text, newline="")))

        assert tuple(header) == EXPORT_COLUMNS


# ----------------------------------------------------------------------
# I. 화면 연결
# ----------------------------------------------------------------------
class TestScreenPassesThePeriod:
    """지시 §8 · §13 — 지금 고른 기간이 그대로 전달된다."""

    def test_button_exists(self, page: str) -> None:
        assert 'id="review-export-history"' in page

    def test_button_is_wired(self, page: str) -> None:
        body = _function_body(page, "initReview")

        assert 'el("review-export-history").addEventListener("click", exportReviewHistory)' in body

    def test_it_sends_the_selected_period(self, page: str) -> None:
        body = _function_body(page, "exportReviewHistory")

        assert '"/reviews/history.csv"' in body
        assert 'el("review-period").value' in body
        assert '"?batch_id=" + encodeURIComponent(period)' in body

    def test_whole_period_sends_no_condition(self, page: str) -> None:
        """기간을 안 골랐으면 조건 없이 — 기존 전체 동작."""
        body = _function_body(page, "exportReviewHistory")

        assert "period ?" in body

    def test_it_asks_for_nothing_first(self, page: str) -> None:
        """지시 §13 — 누르기 전에 아무것도 다시 받지 않는다."""
        body = _function_body(page, "exportReviewHistory")

        for banned in ("fetch(", "fetchJson(", "loadPeriods", "refreshPeriods", "loadReviews"):
            assert banned not in body, banned

    def test_downloading_does_not_change_the_url_state(self, page: str) -> None:
        """지시 §12 — CSV 를 받는다고 조회 상태가 바뀌면 안 된다."""
        body = _function_body(page, "exportReviewHistory")

        for banned in ("pushState", "replaceState", "syncUrl"):
            assert banned not in body, banned

    def test_the_two_csv_buttons_are_told_apart(self, page: str) -> None:
        assert "검토 CSV 내려받기" in page
        assert "이력 CSV 내려받기" in page

    def test_review_csv_still_sends_every_condition(self, page: str) -> None:
        """⛔ 기존 검토 CSV 의 조건 연동을 건드리지 않았다."""
        body = _function_body(page, "exportReviews")

        assert '"/reviews/export.csv?" + reviewParams(false)' in body
