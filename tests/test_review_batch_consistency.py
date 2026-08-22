"""STEP 21 — ``GET /reviews`` 의 기간(batch_id) 정합성.

``page`` 를 주면 :class:`ReviewQuery` 경로로, 주지 않으면 ``limit``/``offset``
을 쓰던 **예전 경로**로 갑니다. 예전 경로는 ``batch_id`` 를 아예 보지 않아,
조건을 줘도 전체가 내려왔습니다(STEP 20 발견).

.. warning::
    ⛔ **조건이 잘못됐다고 전체 조회로 되돌리지 않습니다.** 걸러지지 않은 목록을
    걸러진 것으로 읽는 것이, 오류를 받는 것보다 훨씬 위험합니다.

⛔ 업무규칙은 하나도 바뀌지 않았습니다. 기간은 여전히 날짜가 아니라 **현재
배치**이고(Q5-9 미확정), current/superseded 판정도 기존 방식 그대로입니다.

⚠️ 데이터는 전부 **합성**입니다. 건수는 넣은 행에서 계산해 비교합니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
}

#: 각 달에 넣을 (적재될 행, 미적재될 행) 수.
PLAN: dict[str, tuple[int, int]] = {"2026-01": (5, 2), "2026-02": (4, 1), "2026-03": (6, 3)}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "batch-consistency.db"
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
    batch_id = result.batch.batch_id
    assert batch_id is not None
    return batch_id


@pytest.fixture
def loaded(service: BatchImportService) -> dict[str, int]:
    """세 달치를 올리고 ``{기간: 현재 배치 ID}`` 를 돌려줍니다."""
    return {
        label: batch_of(upload(service, label, normal=normal, rejected=rejected))
        for label, (normal, rejected) in PLAN.items()
    }


def legacy_ids(client: TestClient, query: str = "") -> list[int]:
    """``page`` 없이 부른 결과의 구매 ID."""
    response = client.get("/reviews" + query)
    assert response.status_code == 200, response.text
    return [item["source"]["purchase_id"] for item in response.json()["items"]]


def paged_ids(client: TestClient, query: str = "") -> list[int]:
    """``page`` 를 붙여 부른 결과의 구매 ID(한 쪽에 전부 담기게)."""
    joiner = "&" if query else "?"
    response = client.get(f"/reviews{query}{joiner}page=1&page_size=200")
    assert response.status_code == 200, response.text
    return [item["source"]["purchase_id"] for item in response.json()["items"]]


# ----------------------------------------------------------------------
# A. batch_id 없는 기존 호출
# ----------------------------------------------------------------------
class TestWithoutBatchIdNothingChanged:
    """지시 §3-1 — 기존 호출은 그대로."""

    def test_everything_is_returned(self, loaded: dict[str, int], client: TestClient) -> None:
        expected = sum(normal for normal, _ in PLAN.values())

        assert len(legacy_ids(client)) == expected

    def test_legacy_envelope_has_no_page(self, loaded: dict[str, int], client: TestClient) -> None:
        """⛔ 응답 구조를 바꾸지 않았다 — 예전 호출은 여전히 ``page`` 가 없다."""
        body = client.get("/reviews").json()

        assert body.get("page") is None
        assert "items" in body
        assert "progress" in body

    def test_limit_and_offset_still_work(self, loaded: dict[str, int], client: TestClient) -> None:
        everything = legacy_ids(client)

        assert legacy_ids(client, "?limit=3") == everything[:3]
        assert legacy_ids(client, "?limit=3&offset=2") == everything[2:5]

    def test_review_filter_still_works(self, loaded: dict[str, int], client: TestClient) -> None:
        target = legacy_ids(client)[0]
        client.put(f"/reviews/{target}", json={"final_purchase_type": SERVICE})

        assert legacy_ids(client, "?review_filter=CONFIRMED") == [target]

    def test_bad_filter_is_still_rejected(self, client: TestClient) -> None:
        assert client.get("/reviews?review_filter=없는필터").status_code == 422


# ----------------------------------------------------------------------
# B·C·D. page 유무와 무관하게 같은 범위
# ----------------------------------------------------------------------
class TestBatchIdAppliesOnBothPaths:
    """지시 §3-2 — ``page`` 유무와 관계없이 같은 조건."""

    def test_legacy_path_now_filters(self, loaded: dict[str, int], client: TestClient) -> None:
        """🐞 STEP 20 에서 발견한 문제 — 이 호출이 전체를 돌려줬었다."""
        found = legacy_ids(client, f"?batch_id={loaded['2026-03']}")

        assert len(found) == PLAN["2026-03"][0]

    def test_paged_path_still_filters(self, loaded: dict[str, int], client: TestClient) -> None:
        found = paged_ids(client, f"?batch_id={loaded['2026-03']}")

        assert len(found) == PLAN["2026-03"][0]

    def test_both_paths_cover_the_same_rows(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """지시 §5-D — 두 경로의 구매 ID 집합이 같아야 한다."""
        for label, batch_id in loaded.items():
            assert set(legacy_ids(client, f"?batch_id={batch_id}")) == set(
                paged_ids(client, f"?batch_id={batch_id}")
            ), label

    def test_each_period_is_separate(self, loaded: dict[str, int], client: TestClient) -> None:
        for label, batch_id in loaded.items():
            assert len(legacy_ids(client, f"?batch_id={batch_id}")) == PLAN[label][0], label

    def test_periods_add_up_to_the_whole(self, loaded: dict[str, int], client: TestClient) -> None:
        parts = sum(len(legacy_ids(client, f"?batch_id={b}")) for b in loaded.values())

        assert parts == len(legacy_ids(client))

    def test_batch_id_combines_with_limit(self, loaded: dict[str, int], client: TestClient) -> None:
        """기간을 먼저 적용하고 그 안에서 잘라야 한다."""
        batch_id = loaded["2026-03"]
        whole = legacy_ids(client, f"?batch_id={batch_id}")

        assert legacy_ids(client, f"?batch_id={batch_id}&limit=2") == whole[:2]

    def test_batch_id_combines_with_review_filter(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        march = loaded["2026-03"]
        january = loaded["2026-01"]
        client.put(
            f"/reviews/{legacy_ids(client, f'?batch_id={january}')[0]}",
            json={"final_purchase_type": SERVICE},
        )

        found = legacy_ids(client, f"?batch_id={march}&review_filter=CONFIRMED")

        assert found == [], "다른 기간의 확정이 섞였다"


# ----------------------------------------------------------------------
# E. 잘못된 batch_id — 전체로 되돌아가지 않는다
# ----------------------------------------------------------------------
class TestBadBatchIdNeverFallsBack:
    """지시 §3-5 — 조건이 잘못됐다고 전체를 돌려주면 안 된다."""

    def test_zero_is_rejected_on_both_paths(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        assert client.get("/reviews?batch_id=0").status_code == 422
        assert client.get("/reviews?batch_id=0&page=1&page_size=20").status_code == 422

    def test_negative_is_rejected_on_both_paths(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        assert client.get("/reviews?batch_id=-1").status_code == 422
        assert client.get("/reviews?batch_id=-1&page=1&page_size=20").status_code == 422

    def test_the_message_is_the_shared_one(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """⚠️ 두 경로가 **같은 검사**를 쓰므로 안내도 같다."""
        legacy = client.get("/reviews?batch_id=0").json()["detail"]
        paged = client.get("/reviews?batch_id=0&page=1&page_size=20").json()["detail"]

        assert legacy == paged
        assert "1 이상" in legacy

    def test_unknown_batch_returns_nothing(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """없는 배치는 0건이다 — ⛔ 전체가 아니다."""
        assert legacy_ids(client, "?batch_id=999999") == []
        assert paged_ids(client, "?batch_id=999999") == []

    def test_non_number_is_rejected(self, client: TestClient) -> None:
        assert client.get("/reviews?batch_id=abc").status_code == 422


# ----------------------------------------------------------------------
# F·G. current / superseded
# ----------------------------------------------------------------------
class TestCurrentAndSuperseded:
    """지시 §9 — 기존 current/superseded 의미를 그대로 쓴다."""

    def test_superseded_batch_returns_nothing(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        old = batch_of(upload(service, "2026-03", normal=6))
        upload(service, "2026-03", normal=4, tag="재업로드")

        assert legacy_ids(client, f"?batch_id={old}") == []
        assert paged_ids(client, f"?batch_id={old}") == []

    def test_current_batch_returns_its_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=6)
        new = batch_of(upload(service, "2026-03", normal=4, tag="재업로드"))

        assert len(legacy_ids(client, f"?batch_id={new}")) == 4
        assert len(paged_ids(client, f"?batch_id={new}")) == 4

    def test_whole_list_drops_the_superseded_rows(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """기간을 안 골라도 대체된 배치는 현재 데이터가 아니다."""
        upload(service, "2026-03", normal=6)
        upload(service, "2026-03", normal=4, tag="재업로드")

        assert len(legacy_ids(client)) == 4

    def test_no_new_supersede_logic_was_added(self) -> None:
        """⛔ 기간 조건은 배치 비교 한 줄뿐 — 대체 판정을 다시 하지 않는다."""
        from procurement.reviews.review_service import keeps_batch

        source = keeps_batch.__doc__ or ""

        assert "SUPERSEDED" in source
        assert "find_for_calculation" in source


# ----------------------------------------------------------------------
# H·I. CSV 회귀
# ----------------------------------------------------------------------
class TestCsvIsUnaffected:
    """지시 §8 — 이번 수정으로 CSV 가 달라지면 안 된다."""

    def test_review_csv_matches_the_list(self, loaded: dict[str, int], client: TestClient) -> None:
        batch_id = loaded["2026-03"]
        shown = len(paged_ids(client, f"?batch_id={batch_id}"))

        raw = client.get(f"/reviews/export.csv?batch_id={batch_id}").content

        assert raw.count(b"\r\n") - 1 == shown
        assert raw.startswith(b"\xef\xbb\xbf")

    def test_review_csv_without_batch_is_whole(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        raw = client.get("/reviews/export.csv").content

        assert raw.count(b"\r\n") - 1 == len(legacy_ids(client))

    def test_history_csv_still_scoped(self, loaded: dict[str, int], client: TestClient) -> None:
        """STEP 20 기능 유지."""
        batch_id = loaded["2026-03"]
        client.put(
            f"/reviews/{legacy_ids(client, f'?batch_id={batch_id}')[0]}",
            json={"final_purchase_type": SERVICE},
        )

        raw = client.get(f"/reviews/history.csv?batch_id={batch_id}").content

        assert raw.count(b"\r\n") - 1 == 1
        assert raw.startswith(b"\xef\xbb\xbf")

    def test_history_csv_keeps_undo_records(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 취소해도 확정 기록이 지워지지 않는다."""
        batch_id = loaded["2026-03"]
        target = legacy_ids(client, f"?batch_id={batch_id}")[0]
        client.put(f"/reviews/{target}", json={"final_purchase_type": SERVICE})
        client.post(f"/reviews/{target}/reopen", json={"reopened_by": None})

        raw = client.get(f"/reviews/history.csv?batch_id={batch_id}").content

        assert raw.count(b"\r\n") - 1 == 2

    def test_csv_rejects_zero_like_the_list(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        for path in ("/reviews/export.csv", "/reviews/history.csv", "/reviews"):
            assert client.get(f"{path}?batch_id=0").status_code == 422, path


# ----------------------------------------------------------------------
# 공통 조건 — 한 곳에서만 정의한다
# ----------------------------------------------------------------------
class TestTheConditionLivesInOnePlace:
    """지시 §4 — 두 경로에 조건을 복제하지 않는다."""

    def test_one_shared_predicate(self) -> None:
        from procurement.reviews import review_service

        source = Path(review_service.__file__).read_text(encoding="utf-8")

        assert source.count("def keeps_batch(") == 1
        # 조건식 자체는 그 함수 안에만 있어야 한다.
        assert source.count("purchase.batch_id == batch_id") == 1

    def test_one_shared_validator(self) -> None:
        from procurement.reviews import query

        source = Path(query.__file__).read_text(encoding="utf-8")

        assert source.count("def validate_batch_id(") == 1
        assert source.count("배치 ID 는 1 이상이어야 합니다") == 1

    def test_every_review_path_uses_them(self) -> None:
        from procurement.reviews import review_service

        source = Path(review_service.__file__).read_text(encoding="utf-8")

        # _keeps(검색 경로) · list_targets(예전 경로) · history_of_batch(이력 CSV)
        assert source.count("keeps_batch(") == 4  # 정의 1 + 사용 3
