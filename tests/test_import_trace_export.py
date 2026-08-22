"""STEP 13 — 미적재 행을 업로드 직후에 보고, CSV 로 원본과 대조한다.

STEP 12 에서 미적재 행을 **기록**하는 데까지 갔습니다. 여기서는 그 기록을
담당자가 실제로 **쓸 수 있게** 만든 부분을 고정합니다.

* 업로드 결과에 원본 · 적재 · 미적재가 함께 나온다
* 미적재 행을 CSV 로 내려받아 원본 엑셀과 나란히 놓고 볼 수 있다
* 같은 기간을 다시 올려도 숫자가 어긋나지 않는다

⛔ **업무규칙을 만들지 않습니다.** "금액 0 이하는 제외" 같은 규칙을 세우거나
테스트에 넣지 않았습니다. 여기서 검증하는 것은 **현재 시스템이 내린 미적재
사실을 정확히 추적하고 보여주는가** 뿐입니다(Q5-8 은 확인 대기).

⚠️ 데이터는 전부 **합성**입니다.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
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
from procurement.importers.rejection_export import EXPORT_COLUMNS, export_lines, export_row
from procurement.importers.trace_service import ImportTraceService
from procurement.models.import_rejection import (
    REASON_NON_POSITIVE_AMOUNT,
    REASON_OTHER,
    ImportRejection,
)

#: 기본 대상 기간 ``(시작, 끝)``.
PERIOD: tuple[date, date] = (date(2026, 1, 1), date(2026, 12, 31))

#: 다른 달을 흉내 내는 두 번째 기간.
OTHER_PERIOD: tuple[date, date] = (date(2025, 1, 1), date(2025, 12, 31))

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "export.db"
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


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def row(
    *,
    amount: object = "1000000",
    description: str = "합성 적요",
    company_name: str = "합성거래처",
    business_no: str = "111-11-11111",
    budget_account: str | None = "임차료",
) -> dict[str, Any]:
    return {
        "business_no": business_no,
        "company_name": company_name,
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-20",
        "resolution_date": "2026-03-25",
        "issue_date": "2026-03-10",
        "description": description,
        "budget_account": budget_account,
        "amount": amount,
    }


def load(
    service: BatchImportService,
    rows: list[dict[str, Any]],
    *,
    name: str = "합성.xlsx",
    period: tuple[date, date] | None = None,
) -> BatchImportResult:
    start, end = period or PERIOD
    return service.import_batch(rows, file_name=name, period_start=start, period_end=end)


def read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


# ----------------------------------------------------------------------
# 작업 C·H — CSV
# ----------------------------------------------------------------------
class TestCsvShape:
    """엑셀에서 바로 열리는 모양인가."""

    def test_no_rejections_still_returns_a_header(self, client: TestClient) -> None:
        """미적재 0건이어도 빈 응답이 아니라 **머리글만 있는 파일**이 온다."""
        response = client.get("/imports/trace.csv")

        assert response.status_code == 200
        rows = read_csv(response.content)
        assert rows == [list(EXPORT_COLUMNS)]

    def test_one_rejection(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(), row(amount="-1")])

        rows = read_csv(client.get("/imports/trace.csv").content)

        assert len(rows) == 2
        assert len(rows[1]) == len(EXPORT_COLUMNS)

    def test_many_rejections(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row() for _ in range(4)] + [row(amount=f"-{n}") for n in range(1, 8)])

        rows = read_csv(client.get("/imports/trace.csv").content)

        assert len(rows) - 1 == 7

    def test_bom_and_crlf(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(amount="-1"), row(amount="-2")])

        content = client.get("/imports/trace.csv").content

        assert content.startswith(b"\xef\xbb\xbf")
        assert content.count(b"\r\n") == 3  # 머리글 + 2행
        # 줄 구분은 CRLF 뿐이다. 칸 **안**의 줄바꿈(원문 메시지가 여러 줄인
        # 경우)은 csv 모듈이 따옴표로 감싸므로 행이 밀리지 않는다.
        assert len(read_csv(content)) == 3

    def test_content_disposition_names_the_file(self, client: TestClient) -> None:
        disposition = client.get("/imports/trace.csv").headers["content-disposition"]

        assert "attachment" in disposition
        assert "import-rejections.csv" in disposition

    def test_korean_survives(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(amount="-1", description="1월 임대료", company_name="수원상공회의소")])

        rows = read_csv(client.get("/imports/trace.csv").content)

        assert "1월 임대료" in rows[1]
        assert "수원상공회의소" in rows[1]

    def test_streams_line_by_line(self) -> None:
        """⛔ 전체를 메모리에 만들지 않는다 — generator 로 한 줄씩 흘린다."""
        lines = export_lines(
            ImportRejection(row_number=index, reason=REASON_OTHER) for index in range(1, 4)
        )

        first = next(lines)
        assert first.startswith("﻿")
        assert sum(1 for _ in lines) == 3


class TestCsvValues:
    """원본 값이 그대로 실리는가 (작업 H)."""

    def _first_row(self, client: TestClient) -> dict[str, str]:
        rows = read_csv(client.get("/imports/trace.csv").content)
        return dict(zip(rows[0], rows[1], strict=True))

    def test_row_number_points_at_the_original(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(), row(), row(amount="-1")])

        assert self._first_row(client)["원본 행 번호"] == "3"

    def test_negative_amount_is_kept_as_a_number(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """음수 부호를 보존하고, **따옴표를 붙이지 않는다** — 엑셀에서 합계를 낼 수 있어야 한다."""
        load(service, [row(amount="-1841700")])

        assert self._first_row(client)["금액"] == "-1841700"

    def test_large_amount_has_no_exponent(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-12345678901234")])

        value = self._first_row(client)["금액"]
        assert "E" not in value.upper()
        assert value == "-12345678901234"

    def test_business_no_is_written_as_received(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """CSV 는 사업자번호를 **받은 그대로** 쓴다 — 하이픈을 넣거나 빼지 않는다.

        ⚠️ 다만 업로드 경로에서는 검증 단계
        (:func:`procurement.uploads.validation._parse_business_no`)가 이미
        정규화한 값이 넘어오므로, 실제 파일에서 올라온 행은 하이픈 없는
        10자리로 실립니다 — **적재된 행이 DB-1 에 저장되는 형태와 같습니다.**
        원본 표기를 그대로 남기려면 업로드 파이프라인에 원문 컬럼을 더해야
        하며, 이번 STEP 범위 밖입니다.
        """
        load(service, [row(amount="-1", business_no="204-82-07256")])

        assert self._first_row(client)["사업자번호"] == "204-82-07256"

    def test_blank_values_stay_blank(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(amount="-1", budget_account=None)])

        assert self._first_row(client)["예산과목"] == ""

    def test_comma_in_description_does_not_break_columns(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1", description="토너, 사무실청소, 정수기")])

        rows = read_csv(client.get("/imports/trace.csv").content)
        assert len(rows[1]) == len(EXPORT_COLUMNS)
        assert "토너, 사무실청소, 정수기" in rows[1]

    def test_newline_in_description_does_not_break_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1", description="1월 임대료\n(재청구)")])

        rows = read_csv(client.get("/imports/trace.csv").content)
        assert len(rows) == 2
        assert "1월 임대료\n(재청구)" in rows[1]

    def test_dates_are_iso(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(amount="-1")])

        values = self._first_row(client)
        assert values["결의일자"] == "2026-03-25"
        assert values["신고기준일"] == "2026-03-10"

    def test_reason_code_and_label_are_both_present(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1")])

        values = self._first_row(client)
        assert values["미적재 사유 코드"] == REASON_NON_POSITIVE_AMOUNT
        assert values["미적재 사유"]

    def test_original_message_is_carried(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1")])

        assert "0 보다 커야" in self._first_row(client)["원문 메시지"]


class TestCsvInjection:
    """작업 H — 엑셀이 수식으로 읽지 않도록."""

    def test_formula_in_description_is_neutralised(self) -> None:
        line = export_row(ImportRejection(row_number=1, reason=REASON_OTHER, description="=1+1"))

        assert "'=1+1" in line

    @pytest.mark.parametrize("prefix", ["=", "+", "@"])
    def test_each_prefix_is_guarded(self, prefix: str) -> None:
        line = export_row(
            ImportRejection(
                row_number=1, reason=REASON_OTHER, company_name=f"{prefix}cmd|'/c calc'!A0"
            )
        )

        assert any(cell.startswith("'" + prefix) for cell in line)

    def test_amount_is_not_quoted(self) -> None:
        """⛔ 음수 금액에 따옴표를 붙이면 숫자가 아니게 된다.

        ``-`` 로 시작한다고 무조건 막으면 원본 금액을 쓸 수 없다. 금액 칸은
        숫자 전용이라 자유 입력이 들어오지 않으므로 방어 대상이 아니다.
        """
        line = export_row(
            ImportRejection(row_number=1, reason=REASON_OTHER, amount=Decimal("-1841700"))
        )

        assert "-1841700" in line
        assert "'-1841700" not in line

    def test_free_text_starting_with_minus_is_guarded(self) -> None:
        """자유 입력에서 ``-`` 로 시작하면 기존 CSV 와 같은 방식으로 막는다."""
        line = export_row(
            ImportRejection(row_number=1, reason=REASON_OTHER, description="-1월 임대료")
        )

        assert "'-1월 임대료" in line


# ----------------------------------------------------------------------
# 작업 B — 숫자 일관성
# ----------------------------------------------------------------------
class TestCountsAgree:
    """어디서 세든 같은 숫자가 나오는가."""

    def test_upload_result_adds_up(self, service: BatchImportService) -> None:
        result = load(service, [row() for _ in range(6)] + [row(amount="-1") for _ in range(2)])

        assert result.report.total_count == 8
        assert result.trace.stored + result.trace.rejected == 8
        assert result.trace.unexplained == 0

    def test_csv_row_count_matches_the_api(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row() for _ in range(3)] + [row(amount=f"-{n}") for n in range(1, 6)])

        api = client.get("/imports/trace").json()
        csv_rows = read_csv(client.get("/imports/trace.csv").content)

        assert api["rejected"] == len(csv_rows) - 1 == 5
        assert len(api["rows"]) == api["rejected"]

    def test_trace_stored_matches_the_review_list(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row() for _ in range(3)] + [row(amount="-1")])

        api = client.get("/imports/trace").json()
        listed = client.get("/reviews?page=1&page_size=20").json()["page"]["total"]

        assert api["stored"] == listed

    def test_row_numbers_are_unique_and_complete(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """원본 ↔ 미적재 1:1 대조가 가능해야 한다."""
        rows = [row(amount="-1") if index % 3 == 0 else row() for index in range(12)]

        load(service, rows)

        numbers = [int(line[0]) for line in read_csv(client.get("/imports/trace.csv").content)[1:]]
        expected = [index + 1 for index, item in enumerate(rows) if item["amount"] == "-1"]
        assert numbers == expected
        assert len(set(numbers)) == len(numbers)


# ----------------------------------------------------------------------
# 작업 F — 기간별 업로드 / 재업로드
# ----------------------------------------------------------------------
class TestPeriodsAndReupload:
    """다음 달에도, 다시 올려도 숫자가 맞는가."""

    def test_two_periods_accumulate(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        load(service, [row(), row(amount="-1")], name="4월.xlsx")
        load(service, [row(), row(), row(amount="-2")], name="5월.xlsx", period=OTHER_PERIOD)

        overview = trace.overview()

        assert overview.stored == 3
        assert overview.rejected == 2
        assert overview.source_rows == 5
        assert len(overview.batches) == 2

    def test_reupload_replaces_instead_of_accumulating(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        """⛔ 같은 기간을 다시 올렸는데 미적재만 쌓이면 안 된다.

        STEP 12 구현에서 실제로 그랬습니다 — 적재 행은 대체된 배치를 빼고
        세는데 미적재 기록은 전부 세어, 재업로드 뒤 숫자가 어긋났습니다.
        """
        load(service, [row(), row(amount="-1"), row(amount="-2")], name="1차.xlsx")

        load(service, [row(), row(), row(amount="-9")], name="2차.xlsx")

        overview = trace.overview()
        assert overview.stored == 2
        assert overview.rejected == 1
        assert overview.source_rows == 3

    def test_superseded_records_are_kept_not_deleted(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """⛔ 대체되었다고 지우지 않는다 — 조회에서 빠질 뿐이다."""
        load(service, [row(amount="-1"), row(amount="-2")], name="1차.xlsx")
        load(service, [row(amount="-9")], name="2차.xlsx")

        repository = ImportRejectionRepository(db_path)
        assert len(repository.find_all()) == 3
        assert len(repository.find_current()) == 1

    def test_superseded_batch_leaves_the_batch_list(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        load(service, [row(), row(amount="-1")], name="1차.xlsx")
        load(service, [row(), row(amount="-2")], name="2차.xlsx")

        names = [batch.file_name for batch in trace.overview().batches]

        assert names == ["2차.xlsx"]

    def test_csv_follows_the_same_rule(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1"), row(amount="-2")], name="1차.xlsx")
        load(service, [row(amount="-9")], name="2차.xlsx")

        rows = read_csv(client.get("/imports/trace.csv").content)

        assert len(rows) - 1 == 1
        assert rows[1][EXPORT_COLUMNS.index("금액")] == "-9"

    def test_other_period_is_not_touched_by_a_reupload(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        load(service, [row(), row(amount="-1")], name="5월.xlsx", period=OTHER_PERIOD)
        load(service, [row(), row(amount="-2")], name="4월-1차.xlsx")

        load(service, [row(), row()], name="4월-2차.xlsx")

        overview = trace.overview()
        # 5월 배치는 그대로 남는다.
        assert overview.stored == 3
        assert overview.rejected == 1


# ----------------------------------------------------------------------
# 작업 G — 추적 불변성
# ----------------------------------------------------------------------
class TestNothingVanishes:
    """성공하지 않은 행이 **아무 기록 없이** 사라지지 않는다."""

    def test_every_row_lands_somewhere(self, service: BatchImportService, db_path: Path) -> None:
        rows = [
            row(),
            row(amount="-1"),  # 금액
            row(business_no=""),  # 필수값
            row(amount="숫자아님"),  # 해석 실패
            row(),
        ]

        result = load(service, rows)

        stored = len(PurchaseRepository(db_path).find_for_calculation(None))
        rejected = len(ImportRejectionRepository(db_path).find_current())
        assert stored + rejected == len(rows)
        assert result.trace.unexplained == 0

    def test_a_new_failure_kind_is_still_recorded(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """모르는 사유라도 기록은 남는다 — ``OTHER`` 로 떨어질 뿐이다.

        ⛔ 이번 STEP 에서 새 실패 규칙을 만들지 않았습니다. 기존 규칙에 걸리는
        행으로 확인합니다.
        """
        load(service, [row(amount="숫자아님")])

        recorded = ImportRejectionRepository(db_path).find_current()
        assert len(recorded) == 1
        assert recorded[0].message


# ----------------------------------------------------------------------
# 작업 A·D·E — 화면
# ----------------------------------------------------------------------
class TestUploadScreen:
    """업로드 직후 담당자가 보는 것."""

    def test_trace_area_exists(self, page: str) -> None:
        assert 'id="upload-trace"' in page
        assert 'id="upload-trace-actions"' in page
        assert 'id="upload-trace-rows"' in page

    def test_shows_source_stored_rejected(self, page: str) -> None:
        body = _function_body(page, "renderUploadTrace")

        assert "원본 행 " in body
        assert "검토 대상 적재 " in body
        assert "미적재 " in body

    def test_zero_rejections_says_so(self, page: str) -> None:
        body = _function_body(page, "renderUploadTrace")

        assert "미적재 행 없음" in body

    def test_buttons_are_hidden_when_nothing_is_rejected(self, page: str) -> None:
        body = _function_body(page, "renderUploadTrace")
        zero_branch = body[body.index("if (!rejected)") :]

        assert "actions.hidden = true" in zero_branch

    def test_csv_link_uses_the_server(self, page: str) -> None:
        """⛔ 브라우저에서 데이터를 받아 JS 로 CSV 를 만들지 않는다."""
        assert 'href="/imports/trace.csv"' in page
        body = _function_body(page, "renderUploadTrace")
        for banned in ("Blob(", "createObjectURL", 'join(",")'):
            assert banned not in body, banned

    def test_reason_summary_uses_backend_labels(self, page: str) -> None:
        """⛔ 화면이 사유 이름을 새로 만들지 않는다."""
        body = _function_body(page, "renderUploadTrace")

        assert "item.label" in body

    def test_nothing_is_shown_before_saving(self, page: str) -> None:
        """검증만 하고 저장하지 않았으면 적재/미적재를 말할 수 없다."""
        body = _function_body(page, "renderUploadTrace")

        assert "if (!result.stored)" in body

    def test_rejection_table_is_shared_with_the_review_screen(self, page: str) -> None:
        """⛔ 같은 표를 두 번 만들지 않는다 (지시 ⑤ — 재사용)."""
        assert page.count("function renderRejectionTable(") == 1
        assert "renderRejectionTable(rows, trace)" in _function_body(page, "renderTrace")
        assert "renderRejectionTable(rows, trace)" in _function_body(page, "showUploadRejections")

    def test_show_button_reuses_the_existing_api(self, page: str) -> None:
        body = _function_body(page, "showUploadRejections")

        assert "/imports/trace" in body

    def test_ids_stay_unique(self, page: str) -> None:
        import re

        found = re.findall(r'\bid="([^"]+)"', page)
        duplicates = {value for value in found if found.count(value) > 1}
        assert duplicates == set(), duplicates


class TestWordingStaysFactual:
    """⛔ 화면 문구가 Q5-8 을 앞질러 결정하지 않는다."""

    BANNED = (
        "제외되었습니다",
        "실적에서 제외합니다",
        "검토할 필요가 없습니다",
        "정상적으로 제외",
        "부적합 데이터",
    )

    def test_upload_screen_wording(self, page: str) -> None:
        body = _function_body(page, "renderUploadTrace")

        for banned in self.BANNED:
            assert banned not in body, banned

    def test_csv_column_names(self) -> None:
        for column in EXPORT_COLUMNS:
            for banned in ("제외", "부적합", "무시"):
                assert banned not in column, column

    def test_csv_reason_labels(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(amount="-1"), row(business_no=""), row(amount="숫자아님")])

        rows = read_csv(client.get("/imports/trace.csv").content)
        labels = [line[EXPORT_COLUMNS.index("미적재 사유")] for line in rows[1:]]

        for label in labels:
            for banned in ("제외", "부적합", "무시"):
                assert banned not in label, label


# ----------------------------------------------------------------------
# 작업 I — 기존 기능 회귀
# ----------------------------------------------------------------------
class TestExistingBehaviourUnchanged:
    """검토 경로는 건드리지 않았다."""

    def test_review_list_still_works(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(description=f"합성 적요 {index}") for index in range(5)])

        body = client.get("/reviews?page=1&page_size=20").json()

        assert body["page"]["total"] == 5
        assert set(body["items"][0]) >= {"source", "analysis", "review", "past_labels"}

    def test_review_csv_still_works(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(), row(amount="-1")])

        content = client.get("/reviews/export.csv").content

        assert content.startswith(b"\xef\xbb\xbf")
        assert len(read_csv(content)) - 1 == 1

    def test_two_csv_endpoints_do_not_collide(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """검토 CSV 와 미적재 CSV 는 서로 다른 파일이다."""
        load(service, [row(), row(amount="-1")])

        review = read_csv(client.get("/reviews/export.csv").content)
        rejections = read_csv(client.get("/imports/trace.csv").content)

        assert review[0] != rejections[0]
        assert review[0][0] == "구매ID"
        assert rejections[0][0] == "원본 행 번호"

    def test_confirm_and_undo_still_work(
        self, service: BatchImportService, client: TestClient, db_path: Path
    ) -> None:
        from procurement.core.purchase_type import SERVICE as SERVICE_TYPE

        load(service, [row()])
        purchase = PurchaseRepository(db_path).find_for_calculation(None)[0]

        confirmed = client.put(
            f"/reviews/{purchase.purchase_id}",
            json={"final_purchase_type": SERVICE_TYPE, "reviewed_by": "합성담당"},
        )
        undone = client.post(f"/reviews/{purchase.purchase_id}/reopen", json={})

        assert confirmed.status_code == 200
        assert undone.status_code == 200
        assert undone.json()["review"]["final_purchase_type"] == SERVICE_TYPE

    def test_progress_still_counts_only_stored_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 미적재 행이 진행률 분모에 섞여 들어가지 않는다."""
        load(service, [row(), row(), row(amount="-1")])

        progress = client.get("/reviews/progress").json()

        assert progress["total"] == 2

    def test_trace_api_shape_is_unchanged(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1")])

        body = client.get("/imports/trace").json()

        assert set(body) >= {
            "source_rows",
            "stored",
            "rejected",
            "all_visible",
            "notice",
            "reasons",
            "batches",
            "rows",
            "truncated",
        }


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
