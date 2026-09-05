"""
STEP 121 — 고른 달과 결의일자가 다르면 **파일 전체를 거절**한다.

🟢 2026-09-05 고객 확정 (확인 요청서 ⑯ → ③안)
==============================================

    올릴 때 고른 연도·월과 거래의 **결의일자**가 일치해야 한다. 하나라도 다른
    거래가 있으면 **파일 전체를 거절**한다. 일부 행만 빼고 나머지를 적재하는
    방식은 쓰지 않는다.

왜 전체를 거절하는가
====================
일부만 적재하면 빠진 행이 **어느 달에도 올라가지 않은 채 조용히 사라진다.**
담당자는 매번 몇 건이 빠졌는지 확인해야 하고, 놓치면 그 달 실적이 비어 있는
줄도 모른다. 파일을 통째로 돌려주면 원본을 고쳐 다시 올리게 되므로 빠지는
행이 없다.

무엇을 보고 판정하는가
======================
**결의일자 하나뿐이다**(🟢 §0.10 · STEP 86).

⛔ 신고기준일·계약일자·지급일로 대신하지 않는다.
⛔ 파일명으로 달을 짐작하지 않는다.
⛔ 어긋난 결의일자를 고른 달로 고쳐 맞추지 않는다.

거절은 교체보다 **먼저**다
==========================
::

    파일 읽기 · 행 검증
      → 기간 밖 결의일자 검사   ← ⭐ 여기서 걸리면 끝
      → 겹치는 배치 검사
      → 같은 기간 교체 확인(409)
      → 적재

올릴 수 없는 파일로 「기존 데이터를 지울까요?」를 물을 이유가 없다. 그래서
잘못된 파일을 올려도 **기존 그 달 데이터는 손도 대지 않는다**(§5 · §11).

결의일자가 비어 있는 행
=======================
⛔ 새 규칙을 만들지 않았다(§9). 결의일자는 표준 양식에서 **필수 컬럼**이라
검증 단계에서 이미 오류로 걸리고, 그러면 파일 전체가 저장되지 않는다 —
기존 규칙이 이미 이 STEP 과 같은 방향이다.

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호는 넣지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.review_repository import ReviewRepository
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_BNO = "1000000009"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step121.db"
    init_db(path)
    seed_policies(path)
    for year in (2025, 2026, 2027):
        assert main(["targets", "--year", str(year), "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _won(value: object) -> Decimal:
    return Decimal(str(value))


def _purchase_row(*, day: str, amount: int) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": _BNO,
        "계": amount,
        "신고기준일": day,
        "적요": "합성 거래",
        "예산과목": "일반수용비",
    }
    return [values[header] for header in header_row()]


def _purchase_file(path: Path, rows: list[tuple[str, int]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for day, amount in rows:
        sheet.append(_purchase_row(day=day, amount=amount))
    book.save(path)
    return path


def _upload(
    client: TestClient,
    path: Path,
    *,
    year: int = 2026,
    month: int | None = None,
    replace: bool = False,
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/uploads/purchases",
        json={
            "file_path": str(path),
            "year": year,
            "month": month,
            "replace_existing": replace,
        },
    )
    return response


def _upload_rows(
    client: TestClient,
    tmp_path: Path,
    rows: list[tuple[str, int]],
    *,
    year: int = 2026,
    month: int | None = 8,
    replace: bool = False,
    tag: str = "",
) -> httpx.Response:
    path = _purchase_file(tmp_path / f"upload{tag}.xlsx", rows)
    return _upload(client, path, year=year, month=month, replace=replace)


def _uploaded(client: TestClient, year: int = 2026) -> list[int]:
    payload = client.get("/uploads/purchases/months", params={"year": year}).json()
    return sorted(entry["month"] for entry in payload["months"] if entry["uploaded"])


def _total(client: TestClient, year: int = 2026) -> Decimal:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return _won(payload["total_purchase_amount"])


def _policies(client: TestClient, year: int = 2026) -> list[dict[str, object]]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    rows: list[dict[str, object]] = payload["policies"]
    return rows


def _active_ids(db: Path) -> list[int]:
    import sqlite3

    connection = sqlite3.connect(db)
    try:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT p.purchase_id FROM purchase p "
                "JOIN import_batch b USING (batch_id) "
                "WHERE b.status = 'ACTIVE' ORDER BY p.purchase_id"
            )
        ]
    finally:
        connection.close()


# ======================================================================
# §16-A  고른 달과 모두 맞으면 들어간다
# ======================================================================
class TestAMatchingFileGoesIn:
    def test_a_every_row_in_the_chosen_month(self, client: TestClient, tmp_path: Path) -> None:
        """2026년 8월 선택 · 결의일자 8/1 · 8/15 · 8/31 → 정상."""
        response = _upload_rows(
            client,
            tmp_path,
            [("2026-08-01", 1_000), ("2026-08-15", 2_000), ("2026-08-31", 3_000)],
        )

        assert response.status_code == 200
        assert response.json()["stored"] is True
        assert _total(client) == 6_000
        assert _uploaded(client) == [8]

    def test_a2_the_first_and_last_day_are_inside(self, client: TestClient, tmp_path: Path) -> None:
        """말일 경계가 열려 있다 — 2월처럼 날짜 수가 다른 달도 맞는다."""
        response = _upload_rows(
            client, tmp_path, [("2026-02-01", 1_000), ("2026-02-28", 2_000)], month=2
        )

        assert response.status_code == 200
        assert _total(client) == 3_000


# ======================================================================
# §16-B·C·D  하나라도 다르면 전체 거절
# ======================================================================
class TestOneWrongRowRejectsEverything:
    def test_b_a_different_month_rejects_the_file(self, client: TestClient, tmp_path: Path) -> None:
        """⭐ 8월 선택 · 7/31 한 건 → **세 건 모두** 등록되지 않는다."""
        response = _upload_rows(
            client,
            tmp_path,
            [("2026-08-01", 1_000), ("2026-07-31", 2_000), ("2026-08-20", 3_000)],
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert detail["mismatch_count"] == 1
        assert detail["total_rows"] == 3
        assert detail["found_periods"] == [{"period": "2026-07", "count": 1}]

        assert _total(client) == 0
        assert _uploaded(client) == []

    def test_c_a_different_year_rejects_the_file(self, client: TestClient, tmp_path: Path) -> None:
        """8월 선택 · 2025-12-31 한 건 → 전체 거절."""
        response = _upload_rows(client, tmp_path, [("2026-08-01", 1_000), ("2025-12-31", 2_000)])

        assert response.status_code == 409
        assert response.json()["detail"]["found_periods"] == [{"period": "2025-12", "count": 1}]

        assert _total(client, 2026) == 0
        assert _total(client, 2025) == 0

    def test_d_several_wrong_months_are_all_reported(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """어느 연월이 몇 건인지 알려 준다 — 담당자가 원본을 고칠 수 있도록."""
        response = _upload_rows(
            client,
            tmp_path,
            [
                ("2026-08-05", 1_000),
                ("2026-07-05", 2_000),
                ("2026-06-05", 3_000),
                ("2026-08-25", 4_000),
                ("2026-06-15", 5_000),
            ],
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["mismatch_count"] == 3
        assert detail["found_periods"] == [
            {"period": "2026-06", "count": 2},
            {"period": "2026-07", "count": 1},
        ]
        assert _total(client) == 0

    def test_d2_the_message_says_the_whole_file_was_refused(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 「일부만 빠졌다」로 읽히면 안 된다(§7 · §8)."""
        response = _upload_rows(client, tmp_path, [("2026-08-01", 1_000), ("2026-07-31", 2_000)])

        message = str(response.json()["detail"]["message"])
        assert "2026년 8월" in message
        assert "전체" in message

    def test_d3_the_error_does_not_dump_the_transactions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 오류에 거래 원본을 늘어놓지 않는다 — 연월과 건수만 준다."""
        response = _upload_rows(client, tmp_path, [("2026-08-01", 1_000), ("2026-07-31", 2_000)])

        body = response.text
        assert _BNO not in body
        assert "합성업체" not in body


# ======================================================================
# §5 · §11 · §16-E·F·G  거절이 교체보다 먼저다
# ======================================================================
class TestTheExistingMonthSurvives:
    @pytest.fixture
    def august(self, client: TestClient, tmp_path: Path) -> TestClient:
        """정상적인 8월 데이터가 이미 들어와 있다."""
        assert (
            _upload_rows(client, tmp_path, [("2026-08-10", 8_000_000)], tag="first").status_code
            == 200
        )
        return client

    def test_e_a_bad_replacement_leaves_the_month_alone(
        self, august: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 8월이 있는데 7월이 섞인 8월 파일을 올려도 기존 8월이 그대로다."""
        response = _upload_rows(
            august,
            tmp_path,
            [("2026-08-10", 1_000), ("2026-07-10", 2_000)],
            replace=True,
            tag="bad",
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert _total(august) == 8_000_000
        assert _uploaded(august) == [8]

    def test_e2_it_never_reaches_the_replacement_prompt(
        self, august: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 교체 확인(EXISTING_PERIOD)까지 가지 않는다 — 올릴 수 없는 파일이므로."""
        response = _upload_rows(
            august, tmp_path, [("2026-08-10", 1_000), ("2026-07-10", 2_000)], tag="bad"
        )

        assert response.json()["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert response.json()["detail"]["code"] != "EXISTING_PERIOD"

    def test_f_the_running_total_does_not_move(self, client: TestClient, tmp_path: Path) -> None:
        """1월 100 · 2월 200 · 3월 300 = 600 → 잘못된 3월 파일 → 여전히 600."""
        for month, amount in ((1, 1_000_000), (2, 2_000_000), (3, 3_000_000)):
            assert (
                _upload_rows(
                    client,
                    tmp_path,
                    [(f"2026-{month:02d}-15", amount)],
                    month=month,
                    tag=f"m{month}",
                ).status_code
                == 200
            )
        assert _total(client) == 6_000_000

        response = _upload_rows(
            client,
            tmp_path,
            [("2026-03-20", 9_000_000), ("2026-04-20", 9_000_000)],
            month=3,
            replace=True,
            tag="bad",
        )

        assert response.status_code == 409
        assert _total(client) == 6_000_000
        assert _uploaded(client) == [1, 2, 3]

    def test_g_the_policy_results_do_not_move(self, august: TestClient, tmp_path: Path) -> None:
        """정책별 결과가 그대로다."""
        before = _policies(august)

        assert (
            _upload_rows(
                august,
                tmp_path,
                [("2026-08-10", 1_000), ("2026-07-10", 2_000)],
                replace=True,
                tag="bad",
            ).status_code
            == 409
        )

        assert _policies(august) == before

    def test_e3_the_review_data_survives(
        self, db: Path, august: TestClient, tmp_path: Path
    ) -> None:
        """담당자가 확정해 둔 구매유형도 그대로 남는다(§5)."""
        purchase_id = _active_ids(db)[0]
        assert (
            august.put(
                f"/reviews/{purchase_id}",
                json={"final_purchase_type": "GOODS", "reviewed_by": "담당자"},
            ).status_code
            == 200
        )

        assert (
            _upload_rows(
                august,
                tmp_path,
                [("2026-08-10", 1_000), ("2026-07-10", 2_000)],
                replace=True,
                tag="bad",
            ).status_code
            == 409
        )

        assert _active_ids(db) == [purchase_id]
        review = ReviewRepository(db).find_by_purchase_id(purchase_id)
        assert review is not None and review.final_purchase_type == "GOODS"


# ======================================================================
# §12 · §16-H·I  정상 파일은 예전 흐름 그대로
# ======================================================================
class TestAGoodFileStillReplacesNormally:
    @pytest.fixture
    def august(self, client: TestClient, tmp_path: Path) -> TestClient:
        assert (
            _upload_rows(client, tmp_path, [("2026-08-10", 8_000_000)], tag="first").status_code
            == 200
        )
        return client

    def test_h1_it_asks_before_replacing(self, august: TestClient, tmp_path: Path) -> None:
        """모든 결의일자가 8월이면 교체 확인이 뜬다(STEP 119 흐름)."""
        response = _upload_rows(august, tmp_path, [("2026-08-20", 9_000_000)], tag="good")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EXISTING_PERIOD"

    def test_h2_confirming_replaces_that_month(self, august: TestClient, tmp_path: Path) -> None:
        """승인하면 그 달만 새 데이터가 된다."""
        assert (
            _upload_rows(
                august, tmp_path, [("2026-08-20", 9_000_000)], replace=True, tag="good"
            ).status_code
            == 200
        )

        assert _total(august) == 9_000_000
        assert _uploaded(august) == [8]

    def test_i_cancelling_keeps_the_existing_month(
        self, august: TestClient, tmp_path: Path
    ) -> None:
        """[취소] — 409 를 받고 다시 요청하지 않으면 그대로다."""
        assert (
            _upload_rows(august, tmp_path, [("2026-08-20", 9_000_000)], tag="good").status_code
            == 409
        )

        assert _total(august) == 8_000_000


# ======================================================================
# §3 · §19  결의일자만 본다
# ======================================================================
class TestOnlyTheResolutionDateDecides:
    def test_1_other_date_columns_are_ignored(self, client: TestClient, tmp_path: Path) -> None:
        """결의일자가 8월이면 나머지 날짜가 다른 달이어도 통과한다."""
        row = _purchase_row(day="2026-08-10", amount=1_000)
        headers = list(header_row())
        row[headers.index("신고기준일")] = "2026-07-25"
        row[headers.index("계약일자")] = "2026-06-01"
        row[headers.index("지급일")] = "2026-09-05"
        path = _purchase_file(tmp_path / "x.xlsx", [])
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.load_workbook(path)
        book.active.append(row)
        book.save(path)

        assert _upload(client, path, year=2026, month=8).status_code == 200
        assert _uploaded(client) == [8]

    def test_2_the_resolution_date_alone_can_reject(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """반대로 결의일자만 7월이면, 나머지가 8월이어도 거절한다."""
        row = _purchase_row(day="2026-08-10", amount=1_000)
        headers = list(header_row())
        row[headers.index("결의일자")] = "2026-07-10"
        path = _purchase_file(tmp_path / "y.xlsx", [])
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.load_workbook(path)
        book.active.append(row)
        book.save(path)

        response = _upload(client, path, year=2026, month=8)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"

    def test_3_an_empty_resolution_date_is_still_a_validation_error(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """결의일자가 비면 **기존 규칙**이 잡는다 — ⛔ 새 규칙을 만들지 않았다(§9)."""
        row = _purchase_row(day="2026-08-10", amount=1_000)
        headers = list(header_row())
        row[headers.index("결의일자")] = ""
        path = _purchase_file(tmp_path / "z.xlsx", [])
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.load_workbook(path)
        book.active.append(row)
        book.save(path)

        response = _upload(client, path, year=2026, month=8)

        assert response.status_code == 200  # 행 오류는 200 + stored:false 로 돌려준다
        assert response.json()["stored"] is False
        assert _total(client) == 0


# ======================================================================
# §15 · §16-J  다른 연도·다른 달과 월별 현황
# ======================================================================
class TestNothingElseMoves:
    def test_4_other_years_are_untouched_by_a_rejection(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """2026년 파일이 거절되어도 2025·2027년은 그대로다."""
        for year in (2025, 2027):
            assert (
                _upload_rows(
                    client,
                    tmp_path,
                    [(f"{year}-08-10", 1_000_000)],
                    year=year,
                    month=8,
                    tag=str(year),
                ).status_code
                == 200
            )

        assert (
            _upload_rows(
                client, tmp_path, [("2026-08-10", 1_000), ("2026-07-10", 2_000)], tag="bad"
            ).status_code
            == 409
        )

        assert _total(client, 2025) == 1_000_000
        assert _total(client, 2027) == 1_000_000
        assert _uploaded(client, 2025) == [8]
        assert _uploaded(client, 2027) == [8]

    def test_j1_a_rejected_month_stays_unuploaded(self, client: TestClient, tmp_path: Path) -> None:
        """거절된 달은 「미업로드」로 남는다 — 올라간 것이 없으므로."""
        assert (
            _upload_rows(
                client, tmp_path, [("2026-08-10", 1_000), ("2026-07-10", 2_000)], tag="bad"
            ).status_code
            == 409
        )

        assert _uploaded(client) == []

    def test_j2_an_existing_month_stays_uploaded(self, client: TestClient, tmp_path: Path) -> None:
        """기존 8월이 있었으면 거절 뒤에도 「업로드 완료」 그대로다."""
        assert (
            _upload_rows(client, tmp_path, [("2026-08-10", 8_000_000)], tag="ok").status_code == 200
        )

        assert (
            _upload_rows(
                client,
                tmp_path,
                [("2026-08-10", 1_000), ("2026-07-10", 2_000)],
                replace=True,
                tag="bad",
            ).status_code
            == 409
        )

        assert _uploaded(client) == [8]


# ======================================================================
# §7  화면이 「전체가 거절되었다」고 말하는가
# ======================================================================
class TestTheScreenSaysTheWholeFileWasRefused:
    @pytest.fixture
    def page(self, client: TestClient) -> str:
        response = client.get("/")
        assert response.status_code == 200
        body: str = response.text
        return body

    def test_5_it_handles_the_mismatch_code(self, page: str) -> None:
        assert "UPLOAD_PERIOD_MISMATCH" in page

    def test_6_it_says_nothing_was_registered(self, page: str) -> None:
        """⭐ 「일부만 빠졌다」가 아니라 「하나도 등록되지 않았다」고 쓴다."""
        assert "하나도 등록되지 않았습니다" in page

    def test_7_it_reassures_about_the_existing_data(self, page: str) -> None:
        """기존 데이터가 무사하다는 사실을 함께 알린다."""
        assert "기존에 등록되어 있던 데이터는 그대로 있습니다" in page

    def test_8_it_tells_the_user_what_to_do(self, page: str) -> None:
        assert "결의일자를 확인하신 뒤 다시 올려 주십시오" in page
