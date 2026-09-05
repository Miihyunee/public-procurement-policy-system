"""STEP 24 — 검토 CSV 두 개의 기간(batch_id) **HTTP 회귀**.

STEP 24 에서 두 CSV(``/reviews/export.csv`` · ``/reviews/history.csv``)에 결함을
일부러 주입해 현재 테스트가 잡는지 확인했고, **세 가지가 아무에게도 걸리지
않았습니다.**

=========================================  ==================================
빠져나간 결함                                이 파일에서 막는 것
=========================================  ==================================
``export.csv`` 결과가 비면 전체로 되돌림       빈 조건은 **빈 파일**이다
``export.csv`` 음수 ``batch_id`` 를 무시       ``0`` 뿐 아니라 ``-1`` 도 422
``history.csv`` 음수 ``batch_id`` 를 무시      〃
=========================================  ==================================

기존 테스트는 ``batch_id=0`` 만 확인하고 있었고, 대체된·존재하지 않는 배치가
CSV 에서 **0행인지**를 직접 본 곳이 없었습니다. 목록 쪽 테스트가 대신 잡아 주긴
했지만, CSV 가 자기 경로를 갖게 되면(STEP 20 의 ``history.csv`` 처럼) 그 보호가
사라집니다.

.. warning::
    ⛔ **조건에 맞는 것이 없으면 빈 파일이 나와야 합니다.** "비었으니 전체라도
    주자" 는 친절이 가장 위험합니다 — 담당자는 걸러진 파일로 알고 엽니다.

⛔ 업무규칙은 하나도 건드리지 않습니다. 기간은 여전히 날짜가 아니라 **현재
배치** 기준이고(Q5-9), 미적재 행의 처리 방식도 확인 전입니다(Q5-8).

⚠️ 데이터는 전부 **합성**입니다. 기대 건수는 넣은 행에서 계산합니다.
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

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
}

#: 달마다 넣을 적재 행 수. 기대값은 전부 여기서 계산한다.
PLAN: dict[str, int] = {"2026-01": 4, "2026-02": 3, "2026-03": 5}

#: 2026-03 재업로드분. 이전 배치는 대체된다.
REUPLOAD = 2

#: 두 CSV 모두 같은 규약을 따른다.
BOTH_CSVS = ("/reviews/export.csv", "/reviews/history.csv")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "review-csv.db"
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


def row(description: str) -> dict[str, Any]:
    return {
        "business_no": "111-11-11111",
        "company_name": "합성거래처",
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-20",
        "resolution_date": "2026-03-25",
        "issue_date": "2026-03-10",
        "description": description,
        "budget_account": "임차료",
        "amount": "1000000",
    }


def upload(
    service: BatchImportService, label: str, *, normal: int, tag: str | None = None
) -> BatchImportResult:
    mark = tag or label
    start, end = MONTHS[label]
    return service.import_batch(
        [row(f"{mark} 임대료 {index}") for index in range(normal)],
        file_name=f"{label}.xlsx",
        period_start=start,
        period_end=end,
    )


def batch_of(result: BatchImportResult) -> int:
    batch_id = result.batch.batch_id
    assert batch_id is not None
    return batch_id


@pytest.fixture
def loaded(service: BatchImportService) -> dict[str, int]:
    """세 달치. 2026-03 은 재업로드해 이전 배치가 대체된 상태."""
    ids = {label: batch_of(upload(service, label, normal=n)) for label, n in PLAN.items()}
    ids["2026-03-old"] = ids["2026-03"]
    ids["2026-03"] = batch_of(upload(service, "2026-03", normal=REUPLOAD, tag="3월재"))
    return ids


@pytest.fixture
def with_history(loaded: dict[str, int], client: TestClient) -> dict[str, int]:
    """각 현재 배치에서 한 건씩 확정해 변경 이력을 만든다."""
    for label in PLAN:
        first = ids_in(client, loaded[label])[0]
        response = client.put(f"/reviews/{first}", json={"final_purchase_type": SERVICE})
        assert response.status_code == 200, response.text
    return loaded


def ids_in(client: TestClient, batch_id: int) -> list[int]:
    body = client.get(f"/reviews?batch_id={batch_id}&page=1&page_size=100").json()
    return [item["source"]["purchase_id"] for item in body["items"]]


def csv_rows(client: TestClient, path: str, query: str = "") -> list[list[str]]:
    response = client.get(path + query)
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))[1:]


def header_of(client: TestClient, path: str, query: str = "") -> list[str]:
    text = client.get(path + query).content.decode("utf-8-sig")
    return next(csv.reader(io.StringIO(text, newline="")))


# ----------------------------------------------------------------------
# 🔒 빠져나갔던 결함 ① — 결과가 비면 전체로 되돌림
# ----------------------------------------------------------------------
class TestEmptyMeansEmpty:
    """⛔ 조건에 맞는 것이 없으면 **빈 파일**이다.

    "비었으니 전체라도 주자" 는 친절이 가장 위험하다 — 담당자는 걸러진 파일로
    알고 엽니다. 돌연변이 M7·M10 이 여기서 걸립니다.
    """

    def test_export_csv_for_a_superseded_batch_is_empty(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        rows = csv_rows(client, "/reviews/export.csv", f"?batch_id={with_history['2026-03-old']}")

        assert rows == []

    def test_export_csv_for_an_unknown_batch_is_empty(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        assert csv_rows(client, "/reviews/export.csv", "?batch_id=999999") == []

    def test_history_csv_for_a_superseded_batch_is_empty(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        rows = csv_rows(client, "/reviews/history.csv", f"?batch_id={with_history['2026-03-old']}")

        assert rows == []

    def test_history_csv_for_an_unknown_batch_is_empty(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        assert csv_rows(client, "/reviews/history.csv", "?batch_id=999999") == []

    def test_an_empty_file_still_has_its_header(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """빈 결과라도 머리글은 남는다 — 엑셀에서 열 수 있어야 하므로."""
        for path in BOTH_CSVS:
            assert header_of(client, path, "?batch_id=999999") == header_of(client, path), path

    def test_empty_is_not_silently_replaced_by_everything(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """🔒 핵심 — 빈 결과와 전체가 **다른 크기**여야 한다."""
        for path in BOTH_CSVS:
            whole = csv_rows(client, path)
            empty = csv_rows(client, path, "?batch_id=999999")
            assert whole, path
            assert empty == [], path
            assert len(empty) != len(whole), path

    def test_a_period_with_no_confirmations_gives_an_empty_history(
        self, loaded: dict[str, int], client: TestClient
    ) -> None:
        """아무도 확정하지 않은 기간의 이력은 빈 파일이다(전체가 아니다)."""
        first = ids_in(client, loaded["2026-01"])[0]
        client.put(f"/reviews/{first}", json={"final_purchase_type": SERVICE})

        assert csv_rows(client, "/reviews/history.csv", f"?batch_id={loaded['2026-02']}") == []
        assert len(csv_rows(client, "/reviews/history.csv")) == 1


# ----------------------------------------------------------------------
# 🔒 빠져나갔던 결함 ② — 음수 batch_id 가 조건 없음으로 취급됨
# ----------------------------------------------------------------------
class TestBadValuesAreRefusedByBothCsvs:
    """지시 — ``0`` 뿐 아니라 ``-1`` · ``abc`` 도 거부한다.

    기존 테스트는 ``batch_id=0`` 만 보고 있었습니다. 돌연변이 M9·M11 이 여기서
    걸립니다.
    """

    @pytest.mark.parametrize("bad", ["0", "-1", "-99", "abc", "1.5"])
    def test_export_csv_refuses(
        self, with_history: dict[str, int], client: TestClient, bad: str
    ) -> None:
        assert client.get(f"/reviews/export.csv?batch_id={bad}").status_code == 422

    @pytest.mark.parametrize("bad", ["0", "-1", "-99", "abc", "1.5"])
    def test_history_csv_refuses(
        self, with_history: dict[str, int], client: TestClient, bad: str
    ) -> None:
        assert client.get(f"/reviews/history.csv?batch_id={bad}").status_code == 422

    def test_both_csvs_agree_with_the_list(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 한쪽만 거부하면 화면과 파일의 조건이 갈라진다."""
        for bad in ("0", "-1", "abc"):
            listed = client.get(f"/reviews?batch_id={bad}&page=1&page_size=20").status_code
            for path in BOTH_CSVS:
                assert client.get(f"{path}?batch_id={bad}").status_code == listed, f"{path} {bad}"

    def test_a_refused_request_returns_no_file(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """🔒 거부했으면 **파일이 아니라 오류**가 나와야 한다."""
        for path in BOTH_CSVS:
            response = client.get(f"{path}?batch_id=-1")
            assert response.status_code == 422, path
            assert not response.content.startswith(b"\xef\xbb\xbf"), path


# ----------------------------------------------------------------------
# 현재 / 대체 / 없는 배치가 정확히 구분되는가
# ----------------------------------------------------------------------
class TestBatchesAreToldApart:
    """지시 — 세 가지가 서로 다른 결과여야 한다."""

    def test_export_csv_current_batch_has_its_own_rows(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        rows = csv_rows(client, "/reviews/export.csv", f"?batch_id={with_history['2026-03']}")

        assert len(rows) == REUPLOAD
        assert all("3월재" in line[3] for line in rows)

    def test_export_csv_each_period_matches_what_was_uploaded(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        for label, count in PLAN.items():
            if label == "2026-03":
                continue
            rows = csv_rows(client, "/reviews/export.csv", f"?batch_id={with_history[label]}")
            assert len(rows) == count, label

    def test_export_csv_periods_add_up_to_the_whole(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        parts = sum(
            len(csv_rows(client, "/reviews/export.csv", f"?batch_id={with_history[label]}"))
            for label in PLAN
        )

        assert parts == len(csv_rows(client, "/reviews/export.csv"))

    def test_history_csv_current_batch_only(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        rows = csv_rows(client, "/reviews/history.csv", f"?batch_id={with_history['2026-03']}")

        assert len(rows) == 1
        assert rows[0][1] == str(with_history["2026-03"])

    def test_history_csv_periods_add_up_to_the_whole(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        parts = sum(
            len(csv_rows(client, "/reviews/history.csv", f"?batch_id={with_history[label]}"))
            for label in PLAN
        )

        assert parts == len(csv_rows(client, "/reviews/history.csv"))

    def test_no_csv_row_belongs_to_the_superseded_batch(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        old = str(with_history["2026-03-old"])
        history = csv_rows(client, "/reviews/history.csv")

        assert all(line[1] != old for line in history)

    def test_the_superseded_records_still_exist(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """⛔ 기록을 지우지 않는다 — 현재 조회에서 빠질 뿐이다."""
        listed = client.get("/imports/batches").json()["items"]
        old = [b for b in listed if b["batch_id"] == with_history["2026-03-old"]][0]

        assert old["is_current"] is False


# ----------------------------------------------------------------------
# 두 CSV 는 서로 다른 표다 (STEP 20 규약 유지)
# ----------------------------------------------------------------------
class TestTheTwoCsvsStayDistinct:
    """⚠️ 현재 상태 표와 변경 기록 표를 섞지 않는다."""

    def test_headers_differ(self, with_history: dict[str, int], client: TestClient) -> None:
        assert header_of(client, "/reviews/export.csv") != header_of(client, "/reviews/history.csv")

    def test_history_keeps_undo_records(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """확정 → 취소면 두 줄이다 — 줄어들지 않는다."""
        batch_id = with_history["2026-01"]
        target = ids_in(client, batch_id)[0]
        before = len(csv_rows(client, "/reviews/history.csv", f"?batch_id={batch_id}"))
        client.post(f"/reviews/{target}/reopen", json={"reopened_by": None})

        after = csv_rows(client, "/reviews/history.csv", f"?batch_id={batch_id}")

        assert len(after) == before + 1
        assert after[-1][6] == "REOPENED"

    def test_both_keep_the_csv_conventions(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        for path in BOTH_CSVS:
            raw = client.get(f"{path}?batch_id={with_history['2026-01']}").content
            assert raw.startswith(b"\xef\xbb\xbf"), path
            assert b"\r\n" in raw, path


# ----------------------------------------------------------------------
# STEP 26 — 배치 상세 링크가 만드는 URL 이 실제로 동작하는가
# ----------------------------------------------------------------------
class TestBatchDetailLinkUrlWorks:
    """화면이 만드는 URL 과 서버가 답하는 내용을 **한 자리에서** 맞춰 본다.

    화면 쪽 검사(``test_upload_history_screen.py``)는 링크가 이 모양으로 만들어
    지는지를 보고, 여기서는 그 모양이 **실제로 통하는지**를 봅니다. 둘이 갈라지면
    담당자는 눌러도 아무것도 못 받습니다.
    """

    #: 화면의 ``historyCsvLink()`` 가 만드는 것과 같은 모양.
    LINK = "/reviews/history.csv?batch_id={batch_id}"

    def test_the_link_for_a_current_batch_returns_that_batch(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        batch_id = with_history["2026-01"]

        rows = csv_rows(client, self.LINK.format(batch_id=batch_id))

        assert rows
        assert {line[1] for line in rows} == {str(batch_id)}

    def test_the_link_for_a_superseded_batch_returns_an_empty_file(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """대체된 배치를 골랐을 때 화면이 예고한 대로 비어 있어야 한다."""
        old = with_history["2026-03-old"]

        assert csv_rows(client, self.LINK.format(batch_id=old)) == []

    def test_every_batch_in_the_history_can_be_asked_for(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """이력 화면에 보이는 배치는 **전부** 눌러도 오류가 나지 않는다."""
        listed = client.get("/imports/batches").json()["items"]
        assert listed

        for batch in listed:
            response = client.get(self.LINK.format(batch_id=batch["batch_id"]))
            assert response.status_code == 200, batch["batch_id"]
            assert response.content.startswith(b"\xef\xbb\xbf"), batch["batch_id"]

    def test_the_link_keeps_the_agreed_file_name(
        self, with_history: dict[str, int], client: TestClient
    ) -> None:
        """화면의 ``download`` 이름과 서버가 붙이는 이름이 같아야 한다."""
        batch_id = with_history["2026-01"]

        disposition = client.get(self.LINK.format(batch_id=batch_id)).headers["content-disposition"]

        assert "review-history.csv" in disposition
