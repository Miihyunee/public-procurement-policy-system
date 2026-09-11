"""STEP 15 — 담당자가 고른 **한 달치만** 안전하게 본다.

여러 달이 쌓인 뒤에도 다음이 성립해야 합니다.

* 기간을 고르면 그 달의 **현재 배치**만 보인다
* 같은 달을 다시 올렸으면 **새 배치만** 보인다 — 대체된 배치는 섞이지 않는다
* 기간은 기존 조건(검색·상태·후보·이력·정렬·페이지)과 **AND** 로 걸린다
* 미적재 목록과 CSV 가 **같은 조건**으로 움직인다

⛔ **업무규칙을 만들지 않습니다.** 금액 0 이하 행의 처리(Q5-8)를 비롯해 어떤
판단도 하지 않습니다. 여기서 보는 것은 **고른 기간의 데이터만 정확히
조회되는가** 뿐입니다.

⚠️ 기간은 **배치**로 좁힙니다. 어느 날짜로 기간을 나눌지는 아직 확정되지 않은
업무규칙(D-24)이라, 날짜를 다시 계산하지 않습니다.

⚠️ 데이터는 전부 **합성**입니다.
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
from procurement.core.purchase_type import SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.rejection_query import RejectionQuery, RejectionQueryError
from procurement.importers.trace_service import ImportTraceService
from procurement.models.import_rejection import REASON_NON_POSITIVE_AMOUNT
from procurement.reviews.query import ReviewQuery, ReviewQueryError

#: 월별 기간 ``(시작, 끝)``. ⛔ 코드가 달을 만들지 않고 호출자가 준다.
MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
    "2026-04": (date(2026, 4, 1), date(2026, 4, 30)),
    "2026-05": (date(2026, 5, 1), date(2026, 5, 31)),
    "2026-06": (date(2026, 6, 1), date(2026, 6, 30)),
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "period.db"
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


def upload(
    service: BatchImportService,
    label: str,
    *,
    normal: int,
    rejected: int,
    tag: str | None = None,
    name: str | None = None,
) -> BatchImportResult:
    """한 달치를 올립니다. 적요에 태그를 넣어 어느 배치에서 왔는지 알아봅니다."""
    mark = tag or label
    start, end = MONTHS[label]
    rows = [row(description=f"{mark} 임대료 {index}") for index in range(normal)]
    rows += [
        row(amount=f"-{index + 1}", description=f"{mark} 음수 {index}") for index in range(rejected)
    ]
    return service.import_batch(
        rows, file_name=name or f"{label}.xlsx", period_start=start, period_end=end
    )


@pytest.fixture
def loaded(service: BatchImportService) -> dict[str, int]:
    """여섯 달치. 4월은 재업로드해 이전 배치가 대체된 상태."""
    ids: dict[str, int] = {}
    plan = {
        "2026-01": (4, 2),
        "2026-02": (3, 1),
        "2026-03": (5, 3),
        "2026-04": (2, 4),
        "2026-05": (6, 1),
        "2026-06": (1, 0),
    }
    for label, (normal, rejected) in plan.items():
        result = upload(service, label, normal=normal, rejected=rejected)
        assert result.batch.batch_id is not None
        ids[label] = result.batch.batch_id

    replaced = upload(
        service, "2026-04", normal=3, rejected=2, tag="4월재", name="2026-04-재업로드.xlsx"
    )
    assert replaced.batch.batch_id is not None
    ids["2026-04-old"] = ids["2026-04"]
    ids["2026-04"] = replaced.batch.batch_id
    return ids


def read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def descriptions(client: TestClient, query: str = "") -> list[str]:
    body = client.get("/reviews?page=1&page_size=100" + query).json()
    return [item["source"]["description"] for item in body["items"]]


# ----------------------------------------------------------------------
# 기간 목록
# ----------------------------------------------------------------------
class TestPeriodList:
    """``GET /imports/periods`` — 화면이 고를 수 있는 기간."""

    def test_empty_when_nothing_uploaded(self, client: TestClient) -> None:
        assert client.get("/imports/periods").json() == {"items": [], "total": 0}

    def test_one_entry_per_period(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get("/imports/periods").json()

        labels = [item["label"] for item in body["items"]]
        assert labels == sorted(MONTHS, reverse=True)
        assert body["total"] == len(MONTHS)

    def test_reuploaded_period_points_at_the_new_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 대체된 배치를 선택지로 보여주지 않는다 (지시 §27)."""
        items = client.get("/imports/periods").json()["items"]

        april = next(item for item in items if item["label"] == "2026-04")
        assert april["batch_id"] == loaded["2026-04"]
        assert april["batch_id"] != loaded["2026-04-old"]
        assert [item["batch_id"] for item in items].count(loaded["2026-04-old"]) == 0

    def test_label_comes_from_the_batch_period(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """달에 맞아떨어지지 않는 기간은 **범위 그대로** 보여준다."""
        service.import_batch(
            [row()],
            file_name="분기.xlsx",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
        )

        label = client.get("/imports/periods").json()["items"][0]["label"]

        assert label == "2026-01-01 ~ 2026-03-31"

    def test_counts_come_with_the_period(self, loaded: dict[str, int], client: TestClient) -> None:
        items = client.get("/imports/periods").json()["items"]

        march = next(item for item in items if item["label"] == "2026-03")
        assert march["stored"] == 5
        assert march["rejected"] == 3

    def test_single_current_batch_per_period(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """정상이라면 기간마다 현재 배치는 하나다."""
        items = client.get("/imports/periods").json()["items"]

        assert {item["current_batch_count"] for item in items} == {1}


# ----------------------------------------------------------------------
# 검토 — 기간 필터
# ----------------------------------------------------------------------
class TestReviewByPeriod:
    """지시 §5·§6·§16 — 고른 달만, 기존 조건과 AND."""

    def test_default_is_everything(self, loaded: dict[str, int], client: TestClient) -> None:
        """⛔ 기본값은 전체다 — 최신 달을 자동으로 고르지 않는다 (지시 §20)."""
        total = client.get("/reviews?page=1&page_size=100").json()["page"]["total"]

        assert total == 4 + 3 + 5 + 3 + 6 + 1

    def test_one_period_only(self, loaded: dict[str, int], client: TestClient) -> None:
        found = descriptions(client, f"&batch_id={loaded['2026-03']}")

        assert len(found) == 5
        assert all(value.startswith("2026-03") for value in found)

    def test_neighbouring_months_do_not_leak(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        found = descriptions(client, f"&batch_id={loaded['2026-03']}")

        assert not any("2026-02" in value or "2026-04" in value for value in found)

    def test_every_month_is_reachable(self, loaded: dict[str, int], client: TestClient) -> None:
        counts = {
            label: len(descriptions(client, f"&batch_id={loaded[label]}")) for label in MONTHS
        }

        assert counts == {
            "2026-01": 4,
            "2026-02": 3,
            "2026-03": 5,
            "2026-04": 3,
            "2026-05": 6,
            "2026-06": 1,
        }

    def test_period_plus_search(self, loaded: dict[str, int], client: TestClient) -> None:
        found = descriptions(client, f"&batch_id={loaded['2026-03']}&search=임대료 1")

        assert found == ["2026-03 임대료 1"]

    def test_period_plus_status(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-05']}&status=PENDING"
        ).json()

        assert body["page"]["total"] == 6

    def test_period_plus_candidates(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-01']}&candidates=NONE"
        ).json()

        assert body["page"]["total"] == 4

    def test_period_plus_history(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-01']}&history=NO_HISTORY"
        ).json()

        assert body["page"]["total"] == 4

    def test_period_plus_sort_and_page(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/reviews?page=1&page_size=2&batch_id={loaded['2026-05']}"
            "&sort=description&direction=desc"
        ).json()

        assert body["page"]["total"] == 6
        assert body["page"]["total_pages"] == 3
        assert len(body["items"]) == 2

    def test_all_conditions_together(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-03']}"
            "&status=PENDING&candidates=NONE&history=NO_HISTORY&search=임대료"
            "&sort=amount&direction=desc"
        ).json()

        assert body["page"]["total"] == 5
        assert all(item["source"]["description"].startswith("2026-03") for item in body["items"])

    def test_condition_progress_follows_the_period(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = client.get(f"/reviews?page=1&page_size=100&batch_id={loaded['2026-03']}").json()

        assert body["condition"]["total"] == 5
        assert body["progress"]["total"] == 22  # 전체 진행률은 전체 기준 그대로

    def test_empty_result_is_not_an_error(self, loaded: dict[str, int], client: TestClient) -> None:
        """지시 §26 — 조건에 맞는 것이 없어도 오류가 아니다."""
        response = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-06']}&status=CONFIRMED"
        )

        assert response.status_code == 200
        assert response.json()["page"]["total"] == 0

    def test_unknown_batch_returns_nothing(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = client.get("/reviews?page=1&page_size=100&batch_id=999999").json()

        assert body["page"]["total"] == 0

    def test_bad_batch_id_is_refused(self) -> None:
        with pytest.raises(ReviewQueryError):
            ReviewQuery(batch_id=0)

    def test_review_csv_follows_the_period(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        rows = read_csv(client.get(f"/reviews/export.csv?batch_id={loaded['2026-03']}").content)

        assert len(rows) - 1 == 5
        assert all("2026-03" in " ".join(line) for line in rows[1:])


class TestReuploadedPeriodIsIsolated:
    """지시 §15 — 대체된 배치는 어디에도 섞이지 않는다."""

    def test_review_shows_only_the_new_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        found = descriptions(client, f"&batch_id={loaded['2026-04']}")

        assert len(found) == 3
        assert all(value.startswith("4월재") for value in found)

    def test_old_batch_is_not_reachable_even_by_id(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """대체된 배치 ID 로 물어도 나오지 않는다 — 구매 행이 이미 빠져 있다."""
        body = client.get(f"/reviews?page=1&page_size=100&batch_id={loaded['2026-04-old']}").json()

        assert body["page"]["total"] == 0

    def test_rejections_show_only_the_new_batch(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = client.get(f"/imports/rejections?batch_id={loaded['2026-04']}").json()

        assert body["total"] == 2
        assert all(item["description"].startswith("4월재") for item in body["items"])

    def test_csv_shows_only_the_new_batch(self, loaded: dict[str, int], client: TestClient) -> None:
        rows = read_csv(client.get(f"/imports/trace.csv?batch_id={loaded['2026-04']}").content)

        assert len(rows) - 1 == 2
        assert all("4월재" in " ".join(line) for line in rows[1:])

    def test_old_rejections_are_kept_in_the_database(
        self, loaded: dict[str, int], db_path: Path
    ) -> None:
        """⛔ 지우지 않는다 — 조회에서 빠질 뿐이다."""
        repository = ImportRejectionRepository(db_path)

        assert any(item.batch_id == loaded["2026-04-old"] for item in repository.find_all())
        assert not any(item.batch_id == loaded["2026-04-old"] for item in repository.find_current())

    def test_history_still_shows_both(self, loaded: dict[str, int], client: TestClient) -> None:
        items = client.get("/imports/batches?period_start=2026-04-01&period_end=2026-04-30").json()[
            "items"
        ]

        assert [item["is_current"] for item in items] == [True, False]


# ----------------------------------------------------------------------
# 미적재 — 기간 조건
# ----------------------------------------------------------------------
class TestRejectionsByPeriod:
    """지시 §10 — 기존 조건을 유지한 채 기간이 더해진다."""

    def test_all_by_default(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get("/imports/rejections?page_size=500").json()

        assert body["total"] == 2 + 1 + 3 + 2 + 1 + 0

    def test_one_period(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(f"/imports/rejections?batch_id={loaded['2026-03']}").json()

        assert body["total"] == 3
        assert all(item["description"].startswith("2026-03") for item in body["items"])

    def test_period_plus_reason(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/imports/rejections?batch_id={loaded['2026-03']}&reason={REASON_NON_POSITIVE_AMOUNT}"
        ).json()

        assert body["total"] == 3

    def test_period_plus_search(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(f"/imports/rejections?batch_id={loaded['2026-03']}&search=음수 1").json()

        assert body["total"] == 1
        assert body["items"][0]["description"] == "2026-03 음수 1"

    def test_period_plus_sort(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get(
            f"/imports/rejections?batch_id={loaded['2026-03']}&sort=amount&direction=asc"
        ).json()
        amounts = [float(item["amount"]) for item in body["items"]]

        assert amounts == sorted(amounts)

    def test_period_plus_page(self, loaded: dict[str, int], client: TestClient) -> None:
        first = client.get(
            f"/imports/rejections?batch_id={loaded['2026-03']}&page=1&page_size=2"
        ).json()
        second = client.get(
            f"/imports/rejections?batch_id={loaded['2026-03']}&page=2&page_size=2"
        ).json()

        assert first["total"] == second["total"] == 3
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1

    def test_empty_period_is_not_an_error(self, loaded: dict[str, int], client: TestClient) -> None:
        response = client.get(f"/imports/rejections?batch_id={loaded['2026-06']}")

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_bad_batch_id_is_refused(self) -> None:
        with pytest.raises(RejectionQueryError):
            RejectionQuery(batch_id=0)


# ----------------------------------------------------------------------
# CSV — 화면과 같은 조건
# ----------------------------------------------------------------------
class TestCsvMatchesTheScreen:
    """지시 §11·§13·§14."""

    def _both(self, client: TestClient, query: str) -> tuple[int, int]:
        listed = client.get("/imports/rejections?page_size=500&" + query).json()["total"]
        rows = read_csv(client.get("/imports/trace.csv?" + query).content)
        return listed, len(rows) - 1

    def test_period_condition(self, loaded: dict[str, int], client: TestClient) -> None:
        listed, exported = self._both(client, f"batch_id={loaded['2026-03']}")

        assert listed == exported == 3

    def test_period_and_reason(self, loaded: dict[str, int], client: TestClient) -> None:
        listed, exported = self._both(
            client, f"batch_id={loaded['2026-01']}&reason={REASON_NON_POSITIVE_AMOUNT}"
        )

        assert listed == exported == 2

    def test_period_and_search(self, loaded: dict[str, int], client: TestClient) -> None:
        listed, exported = self._both(client, f"batch_id={loaded['2026-03']}&search=음수 2")

        assert listed == exported == 1

    def test_sort_condition_is_applied(self, loaded: dict[str, int], client: TestClient) -> None:
        rows = read_csv(
            client.get(
                f"/imports/trace.csv?batch_id={loaded['2026-03']}&sort=amount&direction=asc"
            ).content
        )
        amounts = [float(line[rows[0].index("금액")]) for line in rows[1:]]

        assert amounts == sorted(amounts)

    def test_page_condition_is_ignored(self, loaded: dict[str, int], client: TestClient) -> None:
        """⛔ 페이지를 줘도 CSV 는 조건에 맞는 **전부**다 (검토 CSV 와 같은 계약)."""
        whole = client.get(f"/imports/trace.csv?batch_id={loaded['2026-03']}").content
        paged = client.get(
            f"/imports/trace.csv?batch_id={loaded['2026-03']}&page=2&page_size=1"
        ).content

        assert whole == paged
        assert len(read_csv(whole)) - 1 == 3

    def test_bad_condition_is_refused(self, client: TestClient) -> None:
        assert client.get("/imports/trace.csv?reason=자동제외").status_code == 422
        assert client.get("/imports/trace.csv?sort=score").status_code == 422

    def test_format_is_unchanged(self, loaded: dict[str, int], client: TestClient) -> None:
        """STEP 13 규격을 그대로 지킨다."""
        content = client.get(f"/imports/trace.csv?batch_id={loaded['2026-03']}").content
        rows = read_csv(content)

        assert content.startswith(b"\xef\xbb\xbf")
        assert content.count(b"\r\n") == 4  # 머리글 + 3행
        assert len(rows[0]) == 12
        assert rows[0][0] == "원본 행 번호"
        assert rows[0][-1] == "업로드 배치 ID"


# ----------------------------------------------------------------------
# 업로드 이력 — 기간 필터
# ----------------------------------------------------------------------
class TestHistoryByPeriod:
    """지시 §9 — 조회만 한다."""

    def test_all_by_default(self, loaded: dict[str, int], client: TestClient) -> None:
        body = client.get("/imports/batches").json()

        assert body["total"] == len(MONTHS) + 1  # 4월 재업로드 포함
        assert body["current"] == len(MONTHS)

    def test_one_period_includes_superseded(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        body = client.get("/imports/batches?period_start=2026-04-01&period_end=2026-04-30").json()

        assert body["total"] == 2
        assert body["current"] == 1

    def test_other_periods_are_excluded(self, loaded: dict[str, int], client: TestClient) -> None:
        items = client.get("/imports/batches?period_start=2026-03-01&period_end=2026-03-31").json()[
            "items"
        ]

        assert [item["file_name"] for item in items] == ["2026-03.xlsx"]

    def test_half_a_period_is_refused(self, client: TestClient) -> None:
        assert client.get("/imports/batches?period_start=2026-03-01").status_code == 422
        assert client.get("/imports/batches?period_end=2026-03-31").status_code == 422

    def test_detail_still_works(self, loaded: dict[str, int], client: TestClient) -> None:
        detail = client.get(f"/imports/batches/{loaded['2026-04-old']}").json()

        assert detail["is_current"] is False
        assert detail["stored"] == 2
        assert detail["rejected"] == 4


# ----------------------------------------------------------------------
# 확정 · Undo 와 기간
# ----------------------------------------------------------------------
class TestConfirmAndUndoWithinAPeriod:
    """지시 §18 — 확정·Undo 후에도 그 기간 안에서 숫자가 맞는다."""

    def test_confirm_stays_inside_the_period(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        batch = loaded["2026-03"]
        first = client.get(f"/reviews?page=1&page_size=1&batch_id={batch}").json()["items"][0]
        purchase_id = first["source"]["purchase_id"]

        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "합성담당"},
        )

        confirmed = client.get(
            f"/reviews?page=1&page_size=100&batch_id={batch}&status=CONFIRMED"
        ).json()
        assert confirmed["page"]["total"] == 1
        # 다른 달은 그대로다.
        other = client.get(
            f"/reviews?page=1&page_size=100&batch_id={loaded['2026-02']}&status=CONFIRMED"
        ).json()
        assert other["page"]["total"] == 0

    def test_undo_stays_inside_the_period(self, loaded: dict[str, int], client: TestClient) -> None:
        batch = loaded["2026-03"]
        first = client.get(f"/reviews?page=1&page_size=1&batch_id={batch}").json()["items"][0]
        purchase_id = first["source"]["purchase_id"]
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "합성담당"},
        )

        client.post(f"/reviews/{purchase_id}/reopen", json={})

        body = client.get(f"/reviews?page=1&page_size=100&batch_id={batch}&status=REOPENED").json()
        assert body["page"]["total"] == 1
        # 조건 진행률은 **지금 건 조건 전체** 기준이다. 여기서는 상태까지 걸어
        # 두었으므로 1건이 맞다. 기간만 걸면 5건이 된다.
        assert body["condition"]["total"] == 1
        assert (
            client.get(f"/reviews?page=1&page_size=100&batch_id={batch}").json()["condition"][
                "total"
            ]
            == 5
        )

    def test_condition_progress_is_per_period(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        batch = loaded["2026-03"]
        first = client.get(f"/reviews?page=1&page_size=1&batch_id={batch}").json()["items"][0]
        client.put(
            f"/reviews/{first['source']['purchase_id']}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "합성담당"},
        )

        body = client.get(f"/reviews?page=1&page_size=100&batch_id={batch}").json()

        assert body["condition"]["confirmed"] == 1
        assert body["condition"]["total"] == 5
        assert body["progress"]["confirmed"] == 1  # 전체 진행률도 1


class TestNoBusinessRuleLeaked:
    """⛔ 이번 STEP 에서도 업무 판단이 새어 들어가지 않았다."""

    def test_period_filter_changes_nothing(
        self, loaded: dict[str, int], client: TestClient, db_path: Path
    ) -> None:
        before = PurchaseRepository(db_path).find_for_calculation(None)

        client.get(f"/reviews?page=1&page_size=100&batch_id={loaded['2026-03']}")
        client.get(f"/imports/rejections?batch_id={loaded['2026-03']}")
        client.get(f"/imports/trace.csv?batch_id={loaded['2026-03']}")

        assert PurchaseRepository(db_path).find_for_calculation(None) == before

    def test_rejected_rows_never_enter_the_review_list(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        for label in MONTHS:
            found = descriptions(client, f"&batch_id={loaded[label]}")
            assert all("음수" not in value for value in found), label

    def test_no_batch_mutating_endpoint(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]

        for path, methods in paths.items():
            if path.startswith("/imports"):
                assert set(methods) <= {"get", "post"}, (path, methods)
