"""STEP 23 — ``GET /imports/rejections`` 의 기간(batch_id) **HTTP 회귀**.

STEP 22 감사에서 이 API 의 동작은 정확한 것으로 확인됐지만, 다음 두 가지가
**엔드포인트 수준 테스트로 고정되어 있지 않았습니다.**

* 대체된(SUPERSEDED) 배치 ID 로 물었을 때 0건인가
* ``batch_id`` 가 잘못된 값일 때 422 인가 — ⛔ 전체로 되돌아가지 않는가

``RejectionQuery(batch_id=0)`` 이 예외를 내는지는 객체 수준으로 확인하고 있었지만,
그 검사가 **HTTP 경로에 실제로 연결되어 있는지**는 아무도 확인하지 않았습니다.
STEP 21 의 결함(``/reviews`` 예전 경로가 ``batch_id`` 를 무시)이 오래 눈에 띄지
않았던 이유가 정확히 그것이었으므로, 같은 종류의 빈틈을 여기서 막습니다.

.. warning::
    ⛔ **조건이 잘못됐다고 전체 조회로 되돌리지 않습니다.** 걸러지지 않은 목록을
    걸러진 것으로 읽는 것이, 오류를 받는 것보다 훨씬 위험합니다.

⛔ 업무규칙은 하나도 건드리지 않습니다. 미적재 행은 여전히 "원본에는 존재하지만
현재 검토 대상 DB 에 적재되지 않은 행" 일 뿐이며, 그 처리 방식은 확인 전입니다
(Q5-8). 기간도 날짜가 아니라 **현재 배치** 기준 그대로입니다(Q5-9).

⚠️ 데이터는 전부 **합성**입니다. 기대 건수는 넣은 행에서 계산하며, 실데이터
건수(130 등)를 **하드코딩하지 않습니다**.
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
from procurement.models.import_rejection import REASON_NON_POSITIVE_AMOUNT

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
    "2026-04": (date(2026, 4, 1), date(2026, 4, 30)),
}

#: 달마다 넣을 ``(적재될 행, 미적재될 행)``. 기대값은 전부 여기서 계산한다.
PLAN: dict[str, tuple[int, int]] = {
    "2026-01": (4, 2),
    "2026-02": (3, 1),
    "2026-03": (5, 3),
    "2026-04": (2, 4),
}

#: 2026-04 재업로드분 ``(적재, 미적재)``. 이전 배치는 대체된다.
REUPLOAD: tuple[int, int] = (3, 2)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "rejection-http.db"
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
) -> BatchImportResult:
    mark = tag or label
    start, end = MONTHS[label]
    rows = [row(description=f"{mark} 임대료 {index}") for index in range(normal)]
    rows += [
        row(amount=f"-{index + 1}", description=f"{mark} 음수 {index}") for index in range(rejected)
    ]
    return service.import_batch(rows, file_name=f"{label}.xlsx", period_start=start, period_end=end)


def batch_of(result: BatchImportResult) -> int:
    batch_id = result.batch.batch_id
    assert batch_id is not None
    return batch_id


@pytest.fixture
def loaded(service: BatchImportService) -> dict[str, int]:
    """네 달치. 2026-04 는 재업로드해 이전 배치가 대체된 상태."""
    ids: dict[str, int] = {}
    for label, (normal, rejected) in PLAN.items():
        ids[label] = batch_of(upload(service, label, normal=normal, rejected=rejected))
    ids["2026-04-old"] = ids["2026-04"]
    ids["2026-04"] = batch_of(
        upload(service, "2026-04", normal=REUPLOAD[0], rejected=REUPLOAD[1], tag="4월재")
    )
    return ids


def current_rejected_total() -> int:
    """현재 배치 기준 미적재 합계. 대체된 4월 대신 재업로드분을 센다."""
    others = sum(rejected for label, (_, rejected) in PLAN.items() if label != "2026-04")
    return others + REUPLOAD[1]


def listed(client: TestClient, query: str = "") -> dict[str, Any]:
    response = client.get("/imports/rejections" + query)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def keys_of(body: dict[str, Any]) -> set[tuple[int | None, int]]:
    """행을 가리키는 열쇠 — ``(배치, 원본 행 번호)``."""
    return {(item["batch_id"], item["row_number"]) for item in body["items"]}


def csv_rows(client: TestClient, query: str = "") -> list[list[str]]:
    response = client.get("/imports/trace.csv" + query)
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))[1:]


# ----------------------------------------------------------------------
# 1·2. 잘못된 batch_id → 422 (⛔ 전체로 fallback 금지)
# ----------------------------------------------------------------------
class TestBadBatchIdIsRefusedOverHttp:
    """지시 1·2 — 객체가 아니라 **엔드포인트**가 거부하는지 본다."""

    def test_zero_is_refused(self, loaded: dict[str, int], client: TestClient) -> None:
        assert client.get("/imports/rejections?batch_id=0").status_code == 422

    def test_negative_is_refused(self, loaded: dict[str, int], client: TestClient) -> None:
        assert client.get("/imports/rejections?batch_id=-1").status_code == 422

    def test_non_number_is_refused(self, loaded: dict[str, int], client: TestClient) -> None:
        assert client.get("/imports/rejections?batch_id=abc").status_code == 422

    def test_the_refusal_says_why(self, loaded: dict[str, int], client: TestClient) -> None:
        detail = client.get("/imports/rejections?batch_id=0").json()["detail"]

        assert "1 이상" in str(detail)

    def test_it_never_falls_back_to_everything(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """🔒 가장 중요 — 잘못된 조건이 전체 목록으로 되돌아가면 안 된다.

        걸러지지 않은 목록을 걸러진 것으로 읽는 것이 가장 위험하다.
        """
        whole = listed(client)["total"]
        assert whole == current_rejected_total()

        for bad in ("0", "-1", "abc"):
            response = client.get(f"/imports/rejections?batch_id={bad}")
            assert response.status_code == 422, bad
            assert "items" not in response.text or response.json().get("total") != whole, bad

    def test_bad_value_is_refused_with_other_conditions_too(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """다른 조건과 함께 와도 마찬가지다 — 조건 하나가 조용히 빠지지 않는다."""
        query = "?batch_id=0&reason=" + REASON_NON_POSITIVE_AMOUNT + "&page=1&page_size=10"

        assert client.get("/imports/rejections" + query).status_code == 422


# ----------------------------------------------------------------------
# 3·4·5. 없는 배치 / 대체된 배치 / 현재 배치
# ----------------------------------------------------------------------
class TestBatchScoping:
    """지시 3·4·5 — 어떤 배치를 물었는지에 따라 정확히 답한다."""

    def test_unknown_batch_is_empty_not_an_error(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        response = client.get("/imports/rejections?batch_id=999999")

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_superseded_batch_is_empty(self, loaded: dict[str, int], client: TestClient) -> None:
        """🔒 대체된 배치의 미적재 기록이 조회되면 안 된다.

        기록은 DB 에 남아 있지만(지우지 않는다), **현재 조회 대상이 아니다.**
        """
        response = client.get(f"/imports/rejections?batch_id={loaded['2026-04-old']}")

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_current_batch_returns_its_own_rows(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = listed(client, f"?batch_id={loaded['2026-04']}")

        assert body["total"] == REUPLOAD[1]
        assert all(item["batch_id"] == loaded["2026-04"] for item in body["items"])

    def test_every_period_returns_what_it_was_given(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        for label, (_, rejected) in PLAN.items():
            if label == "2026-04":
                continue
            body = listed(client, f"?batch_id={loaded[label]}")
            assert body["total"] == rejected, label

    def test_periods_add_up_to_the_whole(self, loaded: dict[str, int], client: TestClient) -> None:
        parts = sum(listed(client, f"?batch_id={loaded[label]}")["total"] for label in PLAN)

        assert parts == listed(client)["total"]

    def test_neighbouring_periods_do_not_leak(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        january = keys_of(listed(client, f"?batch_id={loaded['2026-01']}"))
        february = keys_of(listed(client, f"?batch_id={loaded['2026-02']}"))

        assert january and february
        assert january.isdisjoint(february)

    def test_the_old_records_still_exist_in_the_database(
        self, db_path: Path, loaded: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 기록을 지우지 않는다 — 현재 조회에서 빠질 뿐이다."""
        kept = ImportRejectionRepository(db_path).find_all()

        assert any(item.batch_id == loaded["2026-04-old"] for item in kept)


# ----------------------------------------------------------------------
# 6. batch_id 없는 전체 조회
# ----------------------------------------------------------------------
class TestWithoutBatchId:
    """지시 6 — 조건이 없으면 현재 배치 전체."""

    def test_everything_current_is_returned(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        assert listed(client)["total"] == current_rejected_total()

    def test_superseded_rows_are_not_counted(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """재업로드해도 미적재가 불어나지 않는다(STEP 13 에서 겪은 문제)."""
        naive_sum = sum(rejected for _, rejected in PLAN.values()) + REUPLOAD[1]

        assert listed(client)["total"] < naive_sum
        assert listed(client)["total"] == current_rejected_total()

    def test_no_row_belongs_to_a_superseded_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        batches = {item["batch_id"] for item in listed(client, "?page_size=500")["items"]}

        assert loaded["2026-04-old"] not in batches


# ----------------------------------------------------------------------
# 7. page / page_size 조합에서도 조건 유지
# ----------------------------------------------------------------------
class TestPagingKeepsTheBatch:
    """지시 7 — 쪽을 넘겨도 기간 조건이 풀리지 않는다."""

    def test_page_one_matches_the_default_call(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        batch_id = loaded["2026-03"]
        default = listed(client, f"?batch_id={batch_id}")
        explicit = listed(client, f"?batch_id={batch_id}&page=1")

        assert default["total"] == explicit["total"]
        assert keys_of(default) == keys_of(explicit)

    def test_walking_every_page_gives_the_same_set(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """단일 페이지 건수만 보지 않고 **전부 순회해 집합**을 비교한다."""
        batch_id = loaded["2026-03"]
        whole = listed(client, f"?batch_id={batch_id}")

        walked: set[tuple[int | None, int]] = set()
        page = 1
        while True:
            body = listed(client, f"?batch_id={batch_id}&page={page}&page_size=2")
            walked |= keys_of(body)
            if page * 2 >= body["total"]:
                break
            page += 1

        assert walked == keys_of(whole)
        assert len(walked) == PLAN["2026-03"][1]

    def test_every_page_stays_inside_the_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        batch_id = loaded["2026-03"]

        for page in (1, 2):
            body = listed(client, f"?batch_id={batch_id}&page={page}&page_size=2")
            assert all(item["batch_id"] == batch_id for item in body["items"]), page

    def test_total_is_the_condition_not_the_page(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """``total`` 은 조건에 맞는 **전체**다 — 그 쪽에 담긴 수가 아니다."""
        batch_id = loaded["2026-03"]
        body = listed(client, f"?batch_id={batch_id}&page=1&page_size=1")

        assert body["total"] == PLAN["2026-03"][1]
        assert len(body["items"]) == 1

    def test_a_page_past_the_end_is_empty_not_everything(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 범위를 넘은 쪽이 전체로 되돌아가면 안 된다."""
        body = listed(client, f"?batch_id={loaded['2026-03']}&page=99&page_size=10")

        assert body["items"] == []
        assert body["total"] == PLAN["2026-03"][1]

    def test_large_page_size_still_scoped(self, loaded: dict[str, int], client: TestClient) -> None:
        batch_id = loaded["2026-03"]
        body = listed(client, f"?batch_id={batch_id}&page=1&page_size=500")

        assert body["total"] == PLAN["2026-03"][1]
        assert all(item["batch_id"] == batch_id for item in body["items"])


# ----------------------------------------------------------------------
# 8. 목록과 CSV 가 갈라지지 않는다
# ----------------------------------------------------------------------
class TestListAndCsvAgree:
    """지시 8 — 화면에서 보던 것과 다른 파일이 내려오면 안 된다."""

    def test_same_count_for_every_batch_id(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        for label in PLAN:
            batch_id = loaded[label]
            assert listed(client, f"?batch_id={batch_id}")["total"] == len(
                csv_rows(client, f"?batch_id={batch_id}")
            ), label

    def test_same_count_without_a_batch(self, loaded: dict[str, int], client: TestClient) -> None:
        assert listed(client)["total"] == len(csv_rows(client))

    def test_superseded_is_empty_in_both(self, loaded: dict[str, int], client: TestClient) -> None:
        old = loaded["2026-04-old"]

        assert listed(client, f"?batch_id={old}")["total"] == 0
        assert csv_rows(client, f"?batch_id={old}") == []

    def test_unknown_batch_is_empty_in_both(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        assert listed(client, "?batch_id=999999")["total"] == 0
        assert csv_rows(client, "?batch_id=999999") == []

    def test_bad_value_is_refused_by_both(self, loaded: dict[str, int], client: TestClient) -> None:
        """⛔ 한쪽만 거부하면 화면과 파일의 조건이 갈라진다."""
        for bad in ("0", "-1", "abc"):
            assert client.get(f"/imports/rejections?batch_id={bad}").status_code == 422, bad
            assert client.get(f"/imports/trace.csv?batch_id={bad}").status_code == 422, bad

    def test_same_rows_not_just_the_same_count(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """건수만 같고 내용이 다르면 소용없다 — 행 번호까지 맞춘다."""
        batch_id = loaded["2026-03"]
        body = listed(client, f"?batch_id={batch_id}")
        screen = [str(item["row_number"]) for item in body["items"]]
        exported = [line[0] for line in csv_rows(client, f"?batch_id={batch_id}")]

        assert screen == exported

    def test_csv_ignores_the_page_but_keeps_the_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """기존 규약 — CSV 는 쪽 조건만 무시하고 나머지는 그대로 따른다."""
        batch_id = loaded["2026-03"]
        paged = csv_rows(client, f"?batch_id={batch_id}&page=2&page_size=1")

        assert len(paged) == PLAN["2026-03"][1]


# ----------------------------------------------------------------------
# 업무규칙이 새지 않는다
# ----------------------------------------------------------------------
class TestNoBusinessRuleLeaked:
    """⛔ 조회는 조회일 뿐 — 미적재 행의 처리 방식을 정하지 않는다."""

    def test_filtering_does_not_change_the_records(
        self, db_path: Path, loaded: dict[str, int], client: TestClient
    ) -> None:
        before = len(ImportRejectionRepository(db_path).find_all())

        for label in PLAN:
            client.get(f"/imports/rejections?batch_id={loaded[label]}")
        client.get("/imports/rejections?batch_id=999999")
        client.get("/imports/rejections?batch_id=0")

        assert len(ImportRejectionRepository(db_path).find_all()) == before

    def test_rejected_rows_never_appear_in_the_review_list(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = client.get("/reviews?page=1&page_size=100").json()

        assert all("음수" not in item["source"]["description"] for item in body["items"])

    def test_no_endpoint_changes_a_rejected_row(self, client: TestClient) -> None:
        """⛔ 미적재 행을 승인·복구·삭제하는 길이 없다."""
        paths = {
            route.path
            for route in client.app.routes  # type: ignore[attr-defined]
            if "rejection" in getattr(route, "path", "")
        }
        for path in paths:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                assert client.request(method, path).status_code in (404, 405), f"{method} {path}"
