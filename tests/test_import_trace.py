"""STEP 12 — 원본 행이 조용히 사라지지 않는다.

STEP 11 실데이터 리허설에서 원본 2,292행 중 **130행이 DB-1 에 적재되지 않아
검토 화면에 아예 보이지 않았습니다.** 담당자가 "전체를 검토했다" 고 판단해도
실제로는 보지 못한 행이 남습니다.

여기서 고정하는 것은 하나입니다.

    **원본 행 = 적재된 행 + 사유와 함께 기록된 행**

⛔ **업무규칙을 만들지 않습니다.** "금액 0 이하는 제외한다" 는 규칙을 세우는
것이 아니라, 적재되지 않았다는 **사실과 사유**를 기록할 뿐입니다. 처리 방식은
고객 확인 사항입니다(``CUSTOMER_DATA_QUESTIONS.md`` Q5-8).

⚠️ 데이터는 전부 **합성**입니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap, init_db
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import (
    ImportRejectionRepository,
    ImportRejectionValidationError,
)
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import (
    BatchImportResult,
    BatchImportService,
)
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.rejection_trace import classify_reason
from procurement.importers.trace_response import build_notice
from procurement.importers.trace_service import ImportTraceService
from procurement.models.import_rejection import (
    REASON_MISSING_REQUIRED,
    REASON_NON_POSITIVE_AMOUNT,
    REASON_OTHER,
    ImportRejection,
    ImportTrace,
)

PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 12, 31)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "trace.db"
    bootstrap(path)
    return path


@pytest.fixture
def service(db_path: Path) -> BatchImportService:
    """운영과 같은 조립 — 미적재 기록 저장소를 함께 넣는다."""
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


INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def row(
    *,
    business_no: str = "111-11-11111",
    company_name: str = "합성거래처",
    amount: object = "1000000",
    description: str = "합성 적요",
    budget_account: str | None = "임차료",
) -> dict[str, Any]:
    """매핑이 끝난 행 하나."""
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
    service: BatchImportService, rows: list[dict[str, Any]], *, name: str = "합성.xlsx"
) -> BatchImportResult:
    return service.import_batch(
        rows, file_name=name, period_start=PERIOD_START, period_end=PERIOD_END
    )


# ----------------------------------------------------------------------
# 작업 A — 어디서 걸러지는가
# ----------------------------------------------------------------------
class TestWhereRowsAreDropped:
    """금액 0 이하 행이 걸리는 지점을 **문서가 아니라 동작으로** 고정한다."""

    def test_repository_refuses_non_positive_amount(self, db_path: Path) -> None:
        """DB-1 저장 단계에서 거부된다 (``PurchaseRepository._validate``)."""
        from procurement.database.purchase_repository import PurchaseValidationError
        from procurement.models.purchase import Purchase

        with pytest.raises(PurchaseValidationError):
            PurchaseRepository(db_path).insert(
                Purchase(
                    business_no="111-11-11111",
                    company_name="합성거래처",
                    contract_date=PERIOD_START,
                    payment_date=PERIOD_START,
                    amount=Decimal("-1000"),
                )
            )

    def test_importer_marks_the_row_failed_and_keeps_going(
        self, service: BatchImportService
    ) -> None:
        """한 행이 걸려도 나머지는 적재된다."""
        result = load(service, [row(), row(amount="-5000"), row()])

        assert result.report.stored_count == 2
        assert result.report.failed_count == 1

    def test_reason_text_is_kept_verbatim(self, service: BatchImportService) -> None:
        result = load(service, [row(amount="-5000")])

        assert "0 보다 커야" in result.rejections[0].message

    def test_upload_validation_treats_it_as_a_warning_not_an_error(self, db_path: Path) -> None:
        """업로드 검증은 **경고**로 넘긴다 — 업무 판단을 만들지 않기 위해서다.

        판단은 저장 단계로 미뤄지고, 그 결과가 이제 기록으로 남는다.
        """
        from procurement.uploads.format import STANDARD_COLUMNS
        from procurement.uploads.validation import validate_rows

        entry: dict[str, object] = {}
        for column in STANDARD_COLUMNS:
            if column.header == "계":  # 금액 컬럼
                entry[column.header] = "-1000"
            elif "일" in column.header:  # 날짜 컬럼
                entry[column.header] = "2026-03-01"
            elif column.header == "사업자등록번호":
                entry[column.header] = "111-11-11111"
            else:
                entry[column.header] = "합성값"
        report = validate_rows([entry])

        assert report.errors == []
        assert any("0 이하" in issue.message for issue in report.warnings)


# ----------------------------------------------------------------------
# 작업 B·D — 추적 기록
# ----------------------------------------------------------------------
class TestRejectionIsRecorded:
    """적재되지 않은 행이 **DB 에 남는다** — 응답이 사라져도."""

    def test_rejection_row_is_stored(self, service: BatchImportService, db_path: Path) -> None:
        load(service, [row(), row(amount="-5000")])

        stored = ImportRejectionRepository(db_path).find_all()

        assert len(stored) == 1
        assert stored[0].reason == REASON_NON_POSITIVE_AMOUNT

    def test_original_values_survive(self, service: BatchImportService, db_path: Path) -> None:
        """원본 값을 그대로 남긴다 — **음수 금액도 그대로**."""
        load(
            service,
            [
                row(
                    amount="-1841700",
                    description="1월 임대료",
                    company_name="합성임대",
                    budget_account="임차료",
                )
            ],
        )

        item = ImportRejectionRepository(db_path).find_all()[0]

        assert item.amount == Decimal("-1841700")
        assert item.description == "1월 임대료"
        assert item.company_name == "합성임대"
        assert item.budget_account == "임차료"

    def test_row_number_points_at_the_original_file(self, service: BatchImportService) -> None:
        """담당자가 원본을 열어 같은 행을 찾을 수 있어야 한다."""
        result = load(service, [row(), row(), row(amount="-100"), row()])

        assert [item.row_number for item in result.rejections] == [3]

    def test_batch_id_links_the_record_to_the_upload(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        result = load(service, [row(amount="-100")])

        item = ImportRejectionRepository(db_path).find_all()[0]
        assert item.batch_id == result.batch.batch_id

    def test_missing_required_value_is_recorded_too(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """금액만 보는 것이 아니다 — 필수값이 비어도 기록된다."""
        load(service, [row(business_no="")])

        stored = ImportRejectionRepository(db_path).find_all()
        assert len(stored) == 1
        assert stored[0].reason == REASON_MISSING_REQUIRED

    def test_blank_description_row_is_still_stored_not_rejected(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """⛔ 적요가 비었다고 버리지 않는다 — 정상 적재된다."""
        load(service, [row(description="")])

        assert len(PurchaseRepository(db_path).find_for_calculation(None)) == 1
        assert ImportRejectionRepository(db_path).find_all() == []

    def test_blank_budget_account_row_is_still_stored(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """⛔ 예산과목 공란도 정상이다(실데이터에 129행 있었다)."""
        load(service, [row(budget_account=None)])

        stored = PurchaseRepository(db_path).find_for_calculation(None)
        assert len(stored) == 1
        assert stored[0].budget_account is None

    def test_missing_resolution_date_does_not_drop_the_row(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        """결의일자가 없어도 행이 사라지지 않는다 — ``None`` 으로 남는다."""
        data = row()
        data["resolution_date"] = ""
        load(service, [data])

        stored = PurchaseRepository(db_path).find_for_calculation(None)
        assert len(stored) == 1
        assert stored[0].resolution_date is None

    def test_service_without_the_repository_still_works(self, db_path: Path) -> None:
        """저장소를 넣지 않으면 기록만 생략된다 — 기존 동작 그대로(하위 호환)."""
        legacy = BatchImportService(
            PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path)),
            ImportBatchRepository(db_path),
            PurchaseRepository(db_path),
        )

        result = load(legacy, [row(), row(amount="-100")])

        assert result.report.stored_count == 1
        assert len(result.rejections) == 1  # 결과에는 담기고
        assert ImportRejectionRepository(db_path).find_all() == []  # DB 에는 안 남는다


class TestCountsAddUp:
    """작업 D — 숫자가 서로 연결된다."""

    def test_source_equals_stored_plus_rejected(self, service: BatchImportService) -> None:
        rows = [row() for _ in range(7)] + [row(amount="-1") for _ in range(3)]

        result = load(service, rows)

        assert result.trace.source_rows == 10
        assert result.trace.stored == 7
        assert result.trace.rejected == 3
        assert result.trace.unexplained == 0
        assert result.trace.complete is True

    def test_unexplained_is_visible_when_it_happens(self) -> None:
        """⛔ 설명되지 않은 행이 있으면 **0 이 아니라 그 수가 보여야** 한다."""
        broken = ImportTrace(source_rows=10, stored=7, rejected=1)

        assert broken.unexplained == 2
        assert broken.complete is False

    def test_no_row_is_loaded_twice(self, service: BatchImportService, db_path: Path) -> None:
        rows = [row(description=f"합성 적요 {index}") for index in range(5)]

        load(service, rows)

        stored = PurchaseRepository(db_path).find_for_calculation(None)
        assert len({purchase.purchase_id for purchase in stored}) == 5

    def test_rejected_row_is_not_in_db1(self, service: BatchImportService, db_path: Path) -> None:
        """⛔ 미적재 행이 구매 테이블에 섞여 들어가면 안 된다 — 계산 대상이 된다."""
        load(service, [row(description="정상"), row(description="음수", amount="-100")])

        descriptions = [
            purchase.description
            for purchase in PurchaseRepository(db_path).find_for_calculation(None)
        ]
        assert descriptions == ["정상"]

    def test_trace_matches_the_review_list(
        self, service: BatchImportService, trace: ImportTraceService, client: TestClient
    ) -> None:
        """대조표의 ``stored`` 와 검토 목록 건수가 같다."""
        load(service, [row() for _ in range(4)] + [row(amount="-1")])

        overview = trace.overview()
        listed = client.get("/reviews?page=1&page_size=20").json()["page"]["total"]

        assert overview.stored == listed == 4
        assert overview.rejected == 1
        assert overview.source_rows == 5


class TestRejectionRepository:
    """저장소 자체의 계약."""

    def test_reason_must_be_known(self, db_path: Path) -> None:
        with pytest.raises(ImportRejectionValidationError):
            ImportRejectionRepository(db_path).record_many(
                [ImportRejection(row_number=1, reason="자동제외")]
            )

    def test_empty_input_is_a_no_op(self, db_path: Path) -> None:
        assert ImportRejectionRepository(db_path).record_many([]) == 0

    def test_counts_by_reason(self, service: BatchImportService, db_path: Path) -> None:
        load(service, [row(amount="-1"), row(amount="-2"), row(business_no="")])

        counts = ImportRejectionRepository(db_path).count_by_reason()

        assert counts[REASON_NON_POSITIVE_AMOUNT] == 2
        assert counts[REASON_MISSING_REQUIRED] == 1

    def test_table_is_created_by_init_db(self, tmp_path: Path) -> None:
        """새 DB 에도, 기존 DB 를 다시 열어도 테이블이 준비된다(멱등)."""
        path = tmp_path / "fresh.db"
        init_db(path)
        init_db(path)

        assert ImportRejectionRepository(path).find_all() == []

    def test_purchase_table_is_untouched(self, db_path: Path) -> None:
        """⛔ DB-1 스키마를 건드리지 않았다 — 신규 테이블만 추가했다."""
        columns = {
            row_["name"]
            for row_ in PurchaseRepository(db_path).execute("PRAGMA table_info(purchase)")
        }

        assert "rejection_id" not in columns
        assert "excluded" not in columns


class TestReasonClassification:
    """사유 분류는 **문장을 코드로 옮기는 일**일 뿐이다."""

    def test_non_positive_amount(self) -> None:
        assert classify_reason(["구매금액은 0 보다 커야 합니다: amount=-100"]) == (
            REASON_NON_POSITIVE_AMOUNT
        )

    def test_missing_required(self) -> None:
        assert classify_reason(["필수값이 누락되었습니다: business_no"]) == REASON_MISSING_REQUIRED

    def test_unknown_message_is_not_dropped(self) -> None:
        """⛔ 모르는 사유라고 버리지 않는다 — ``OTHER`` 로 남긴다."""
        assert classify_reason(["처음 보는 사유"]) == REASON_OTHER


# ----------------------------------------------------------------------
# 작업 C·E — 화면과 응답
# ----------------------------------------------------------------------
class TestTraceApi:
    """``GET /imports/trace``."""

    def test_empty_db_says_everything_is_visible(self, client: TestClient) -> None:
        body = client.get("/imports/trace").json()

        assert body["all_visible"] is True
        assert body["rejected"] == 0

    def test_reports_the_gap(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row() for _ in range(3)] + [row(amount="-1"), row(amount="-2")])

        body = client.get("/imports/trace").json()

        assert body["source_rows"] == 5
        assert body["stored"] == 3
        assert body["rejected"] == 2
        assert body["all_visible"] is False

    def test_rows_carry_enough_to_find_the_original(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1841700", description="1월 임대료")])

        first = client.get("/imports/trace").json()["rows"][0]

        assert first["row_number"] == 1
        assert first["description"] == "1월 임대료"
        assert Decimal(first["amount"]) == Decimal("-1841700")
        assert first["reason_label"]

    def test_batches_are_listed(self, service: BatchImportService, client: TestClient) -> None:
        load(service, [row(), row(amount="-1")], name="1월.xlsx")

        batches = client.get("/imports/trace").json()["batches"]

        assert len(batches) == 1
        assert batches[0]["file_name"] == "1월.xlsx"
        assert batches[0]["stored"] == 1
        assert batches[0]["rejected"] == 1

    def test_upload_response_carries_the_count(self, db_path: Path) -> None:
        """작업 E — 업로드 직후 응답에도 몇 행이 안 들어갔는지 담긴다."""
        from procurement.uploads.upload_response import build_upload_response
        from procurement.uploads.upload_service import UploadResult
        from procurement.uploads.validation import ValidationReport

        batch = load(
            BatchImportService(
                PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path)),
                ImportBatchRepository(db_path),
                PurchaseRepository(db_path),
                ImportRejectionRepository(db_path),
            ),
            [row(), row(amount="-1"), row(amount="-2")],
        )
        result = UploadResult(
            file_name="합성.xlsx",
            report=ValidationReport(total_rows=3),
            stored=True,
            batch=batch,
        )

        response = build_upload_response(result)

        assert response.rejected_rows == 2
        # STEP 13 에서 사유를 (코드 · 표시 이름 · 건수) 목록으로 바꿨다 —
        # ``/imports/trace`` 응답과 같은 모양이라야 화면이 한 가지 방식으로
        # 읽는다. 확인하는 사실은 그대로다.
        reasons = {item.reason: item.count for item in response.rejection_reasons}
        assert reasons[REASON_NON_POSITIVE_AMOUNT] == 2


class TestWordingIsNotADecision:
    """⛔ 화면 문구가 업무 판단을 앞질러 가지 않는다 (지시 4번)."""

    BANNED = ("제외되었습니다", "제외합니다", "검토할 필요가 없습니다", "무시", "폐기")

    def test_notice_avoids_settled_wording(
        self, service: BatchImportService, trace: ImportTraceService
    ) -> None:
        load(service, [row(), row(amount="-1")])

        notice = build_notice(trace.overview())

        for banned in self.BANNED:
            assert banned not in notice, banned
        assert "확인이 필요" in notice

    def test_api_notice_avoids_settled_wording(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        load(service, [row(amount="-1")])

        notice = client.get("/imports/trace").json()["notice"]

        for banned in self.BANNED:
            assert banned not in notice, banned

    def test_reason_labels_avoid_settled_wording(self) -> None:
        from procurement.models.import_rejection import REJECTION_REASON_LABELS

        for label in REJECTION_REASON_LABELS.values():
            for banned in self.BANNED:
                assert banned not in label, label

    def test_no_business_rule_was_created(self) -> None:
        """⛔ "금액 0 이하 = 제외" 같은 **판단 컬럼**을 만들지 않았다."""
        from procurement.models import import_rejection

        names = dir(import_rejection)
        for banned in ("EXCLUDED", "IGNORE", "AUTO_", "CONFIRMED_EXCLUSION"):
            assert not any(banned in name for name in names), banned


class TestScreenShowsTheGap:
    """작업 C — 담당자가 화면에서 알 수 있다."""

    def test_trace_area_exists(self, page: str) -> None:
        assert 'id="review-trace"' in page
        assert "function loadTrace(" in page
        assert "/imports/trace" in page

    def test_trace_failure_does_not_block_review(self, page: str) -> None:
        """대조 정보를 못 받아도 검토 화면은 계속 쓸 수 있어야 한다."""
        body = _function_body(page, "loadTrace")

        assert ".catch(" in body
        assert "reviewError" not in body

    def test_screen_does_not_invent_a_decision(self, page: str) -> None:
        body = _function_body(page, "renderTrace")

        for banned in TestWordingIsNotADecision.BANNED:
            assert banned not in body, banned

    def test_truncation_is_announced(self, page: str) -> None:
        """⛔ 목록을 조용히 자르지 않는다.

        STEP 13 에서 표 그리기를 ``renderRejectionTable`` 로 옮겨 검토 화면과
        업로드 화면이 **같은 것**을 쓰게 했다. 확인하는 사실은 그대로다.
        """
        body = _function_body(page, "renderRejectionTable")

        assert "truncated" in body
        assert "일부만 표시" in body

    def test_ids_stay_unique(self, page: str) -> None:
        import re

        found = re.findall(r'\bid="([^"]+)"', page)
        duplicates = {value for value in found if found.count(value) > 1}
        assert duplicates == set(), duplicates


class TestActorNameDisplay:
    """작업 K — 담당자명이 비었을 때."""

    def test_blank_actor_is_labelled_on_screen(self, page: str) -> None:
        body = _function_body(page, "actorName")

        assert "담당자 미입력" in body

    def test_history_uses_it(self, page: str) -> None:
        assert "actorName(entry.changed_by)" in page

    def test_no_fake_name_is_stored(self, service: BatchImportService, db_path: Path) -> None:
        """⛔ DB 에는 가짜 이름을 넣지 않는다 — ``None`` 그대로 남는다."""
        from procurement.core.purchase_type import SERVICE as SERVICE_TYPE
        from procurement.database.review_repository import ReviewRepository

        load(service, [row()])
        purchase = PurchaseRepository(db_path).find_for_calculation(None)[0]
        assert purchase.purchase_id is not None
        reviews = ReviewRepository(db_path)
        reviews.confirm(purchase.purchase_id, final_purchase_type=SERVICE_TYPE)
        reviews.reopen(purchase.purchase_id)

        entries = reviews.find_history(purchase.purchase_id)
        assert entries[-1].changed_by is None

    def test_input_hint_explains_the_consequence(self, page: str) -> None:
        assert "비우면 이력에 남지 않음" in page


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
