"""
STEP 160 — 고객 원본을 손대지 않고 올린다: 집계 행과 앞선 해 결의 건.

무엇이 문제였나
===============
실제 고객 원본(2,305행)을 올리려 하면 두 곳에서 막혔습니다(STEP 159 실측).

① **집계 행 13건** — 거래처·사업자번호·결의일자·신고기준일이 모두 빈 채
   금액만 있는 소계·합계 줄입니다. 13행의 「계」 합계만 485억이었습니다.
   그 13행이 오류가 되어, 「한 행이라도 오류면 저장하지 않는다」에 걸려
   정상 거래 2,292행까지 함께 막혔습니다.

② **앞선 해 결의 5건** — 2025-12-29~31 에 결의하고 2026-01 에 신고한
   건입니다. 「고른 기간 밖의 결의일자가 하나라도 있으면 파일 전체 거절」에
   걸립니다. 12월 말 결의 → 1월 신고는 해마다 생깁니다.

무엇을 바꿨나 (🟢 2026-09-10 PM 확정 A-1 · B-1)
================================================
① 네 칸이 **모두** 빈 행은 집계 행으로 보고 거래자료에서 뺍니다.
② 결의일자가 **대상 기간보다 앞선 해**인 행은 그 해 실적으로 두고 이번
   등록에서 뺍니다.

둘 다 **몇 행을 뺐는지 결과에 적습니다.**

⛔ 연도 귀속 기준은 그대로 결의일자입니다 — 신고기준일로 바꾸지 않습니다.
⛔ 빈 칸이 하나라도 있으면 빼는 식으로 넓히지 않습니다.
⛔ 금액이 크다고, 적요가 「합계」라고 해서 빼지 않습니다.
⛔ 같은 해 안에서 달이 어긋나는 것은 예전처럼 파일 전체를 거절합니다 —
   다른 달 파일을 잘못 고른 경우이기 때문입니다.
⛔ 음수·0원 규칙(NON_POSITIVE_AMOUNT), 배치 교체, 계산 로직은 그대로입니다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.uploads.aggregate_rows import AGGREGATE_KEY_HEADERS, is_aggregate_row

#: 고객 원본과 같은 머리글 모양. ⛔ 고객 자료가 아니다 — 칸 이름만 같다.
HEADERS = (
    "번호",
    "신고기준일",
    "적요",
    "거래처명",
    "사업자번호",
    "공급가액",
    "세액",
    "계",
    "결의일자",
    "예산과목",
)

#: ⛔ 실제 업체의 번호가 아니다. 검증자릿수만 맞춘 합성 사업자번호.
SYNTHETIC = ("1000000009", "1000000014", "1000000028", "1000000033")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def deal(
    number: int,
    *,
    resolution: str = "2026-03-15",
    issue: str = "2026-03-10",
    name: str | None = "합성기업",
    business_no: str | None = SYNTHETIC[0],
    amount: object = 110000,
    note: str = "합성 지출",
    budget: str = "임차료",
) -> list[object]:
    """거래 한 줄."""
    return [number, issue, note, name, business_no, 100000, 10000, amount, resolution, budget]


def aggregate(note: str = "", amount: object = 4_000_000_000) -> list[object]:
    """집계 한 줄 — 네 칸이 모두 비어 있다."""
    return [None, None, note, None, None, None, None, amount, None, None]


def book(path: Path, rows: list[list[object]]) -> str:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "원본"
    sheet.append(list(HEADERS))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return str(path)


def validate(client: TestClient, file_path: str) -> dict[str, Any]:
    response = client.post("/uploads/purchases/validate", json={"file_path": file_path})
    assert response.status_code == 200, response.text
    return dict(response.json())


def upload(client: TestClient, file_path: str, **payload: object) -> tuple[int, dict[str, Any]]:
    response = client.post("/uploads/purchases", json={"file_path": file_path, **payload})
    return int(response.status_code), dict(response.json())


# ======================================================================
# [A] 집계 행
# ======================================================================
class TestAggregateRowsAreSetAsideNotTreatedAsErrors:
    def test_1_all_four_key_columns_blank_is_an_aggregate_row(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """A-1 — 네 칸이 모두 빈 행은 오류가 아니라 집계 행이다."""
        path = book(tmp_path / "a.xlsx", [deal(1), aggregate("합계"), deal(2)])

        result = validate(client, path)
        assert result["ok"] is True
        assert result["file_errors"] == []
        assert result["error_rows"] == 0
        assert result["aggregate_rows"] == 1
        assert result["total_rows"] == 2  # 거래만 센다
        assert result["source_rows"] == 3  # 엑셀에서 세는 숫자
        assert any("집계 행 1건" in line for line in result["summary_lines"])

        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["stored"] is True
        assert stored["stored_rows"] == 2
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 2

    def test_2_one_key_column_with_a_value_is_not_an_aggregate_row(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A-2 — 네 칸 중 하나라도 값이 있으면 집계 행이 아니다 → 기존 검증."""
        for header in AGGREGATE_KEY_HEADERS:
            row = aggregate("소계")
            position = {
                "기업명": 3, "사업자등록번호": 4, "결의일자": 8, "신고기준일": 1
            }[header]
            row[position] = "2026-03-15" if "일" in header else "값있음"

            result = validate(client, book(tmp_path / f"{position}.xlsx", [deal(1), row]))
            assert result["aggregate_rows"] == 0, header
            assert result["error_rows"] == 1, header  # 나머지 필수값이 비어 오류
            assert result["ok"] is False, header

    def test_3_a_huge_amount_alone_does_not_make_it_an_aggregate_row(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A-3 — ⛔ 금액이 크다는 이유로 빼지 않는다."""
        path = book(tmp_path / "big.xlsx", [deal(1, amount=8_808_740_570)])

        result = validate(client, path)
        assert result["aggregate_rows"] == 0
        assert result["ok"] is True
        assert result["valid_rows"] == 1

    def test_4_the_word_total_alone_does_not_make_it_an_aggregate_row(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A-4 — ⛔ 적요가 「합계」라는 이유로 빼지 않는다."""
        path = book(tmp_path / "word.xlsx", [deal(1, note="합계", amount=4_000_000_000)])

        result = validate(client, path)
        assert result["aggregate_rows"] == 0
        assert result["ok"] is True
        assert result["valid_rows"] == 1

    def test_the_excel_row_numbers_survive_the_removal(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 집계 행을 뺀 뒤에도 오류가 **원본 행 번호**를 가리킨다.

        번호를 다시 이어 붙이면 담당자가 엑셀에서 엉뚱한 줄을 찾게 된다.
        """
        path = book(
            tmp_path / "numbers.xlsx",
            [aggregate(), aggregate(), deal(1, amount="숫자아님")],
        )
        result = validate(client, path)
        assert result["aggregate_rows"] == 2
        assert [issue["row_number"] for issue in result["issues"]] == [4]  # 2·3행이 집계

    def test_a_blank_row_is_not_counted_as_an_aggregate_row(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 아무것도 없는 빈 줄은 예전처럼 읽지도 않는다 — 집계로 세지 않는다."""
        path = book(tmp_path / "blank.xlsx", [deal(1), [None] * len(HEADERS)])
        result = validate(client, path)
        assert result["aggregate_rows"] == 0
        assert result["total_rows"] == 1

    def test_the_rule_itself(self) -> None:
        """판정 규칙 자체 — 네 칸이 모두 비어야 한다."""
        assert AGGREGATE_KEY_HEADERS == ("기업명", "사업자등록번호", "결의일자", "신고기준일")
        assert is_aggregate_row({"계": 100, "적요": "합계"}) is True
        assert is_aggregate_row({header: "  " for header in AGGREGATE_KEY_HEADERS}) is True
        assert is_aggregate_row({"기업명": "합성기업"}) is False


# ======================================================================
# [B] 앞선 해 결의일자
# ======================================================================
class TestRowsResolvedInAnEarlierYearBelongToThatYear:
    def test_5_6_a_prior_year_row_is_left_out_of_this_upload(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """B-5·6 — 2026년 등록에서 2025-12-29·2025-12-31 은 빠진다."""
        path = book(
            tmp_path / "b.xlsx",
            [
                deal(1, resolution="2025-12-29", issue="2026-01-02"),
                deal(2, resolution="2025-12-31", issue="2026-01-06"),
                deal(3, resolution="2026-01-05", issue="2026-01-05"),
            ],
        )

        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["stored"] is True
        assert stored["prior_year_rows"] == 2
        assert stored["stored_rows"] == 1
        assert any("앞선 해인 2건" in line for line in stored["summary_lines"])

        kept = sqlite3.connect(db).execute("SELECT resolution_date FROM purchase").fetchall()
        assert kept == [("2026-01-05",)]

    def test_7_the_first_day_of_the_target_year_is_kept(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """B-7 — 2026-01-01 은 정상 대상이다."""
        path = book(tmp_path / "first.xlsx", [deal(1, resolution="2026-01-01")])
        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["prior_year_rows"] == 0
        assert stored["stored_rows"] == 1

    def test_8_the_issue_date_does_not_decide_the_year(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """B-8 — ⛔ 신고기준일이 2026년이어도 결의일자가 2025년이면 빠진다."""
        path = book(
            tmp_path / "issue.xlsx",
            [deal(1, resolution="2025-12-30", issue="2026-01-05"), deal(2)],
        )
        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["prior_year_rows"] == 1
        assert stored["stored_rows"] == 1

    def test_9_the_same_rows_are_normal_when_that_year_is_the_target(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """B-9 — ⛔ 연도를 적어 두지 않았다. 2025년으로 올리면 그대로 들어간다."""
        path = book(
            tmp_path / "c.xlsx",
            [
                deal(1, resolution="2025-12-29", issue="2026-01-02"),
                deal(2, resolution="2025-12-31", issue="2026-01-06"),
            ],
        )
        status, stored = upload(client, path, year=2025)
        assert status == 200, stored
        assert stored["prior_year_rows"] == 0
        assert stored["stored_rows"] == 2
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 2

    def test_a_wrong_month_file_is_still_refused_whole(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 같은 해 안에서 달이 어긋나면 예전처럼 파일 전체를 거절한다.

        다른 달 파일을 잘못 고른 경우다. 조용히 빼면 담당자는 그 달 실적이
        들어간 줄 안다(STEP 121 규칙 유지).
        """
        path = book(tmp_path / "month.xlsx", [deal(1, resolution="2026-03-15")])
        status, body = upload(client, path, year=2026, month=1)
        assert status == 409, body
        assert body["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 0

    def test_a_later_year_is_still_refused_whole(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ **뒤에 오는** 해는 빼지 않는다 — 파일을 잘못 고른 것이다."""
        path = book(tmp_path / "later.xlsx", [deal(1, resolution="2027-01-05")])
        status, body = upload(client, path, year=2026)
        assert status == 409, body
        assert body["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 0


# ======================================================================
# [C] 기존 금액 규칙 회귀
# ======================================================================
class TestTheAmountRulesAreUnchanged:
    def test_10_a_negative_row_is_still_non_positive_amount(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """C-10 — 음수는 예전 그대로 미적재로 기록된다."""
        path = book(tmp_path / "neg.xlsx", [deal(1, amount=-6468), deal(2)])
        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["stored_rows"] == 1
        assert [(r["reason"], r["count"]) for r in stored["rejection_reasons"]] == [
            ("NON_POSITIVE_AMOUNT", 1)
        ]

    def test_11_a_zero_row_is_still_non_positive_amount(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """C-11 — 0원도 예전 그대로다."""
        path = book(tmp_path / "zero.xlsx", [deal(1, amount=0), deal(2)])
        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["stored_rows"] == 1
        assert [(r["reason"], r["count"]) for r in stored["rejection_reasons"]] == [
            ("NON_POSITIVE_AMOUNT", 1)
        ]

    def test_the_three_kinds_of_exclusion_are_told_apart(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 담당자가 세 가지를 구분할 수 있어야 한다(§3)."""
        path = book(
            tmp_path / "mixed.xlsx",
            [
                deal(1),
                deal(2, amount=-100),
                deal(3, resolution="2025-12-30", issue="2026-01-02"),
                aggregate("합계"),
            ],
        )
        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["aggregate_rows"] == 1       # 거래자료가 아님
        assert stored["prior_year_rows"] == 1      # 그 해 실적
        assert stored["rejected_rows"] == 1        # 금액 규칙
        assert stored["error_rows"] == 0           # 오류는 없음
        assert stored["stored_rows"] == 1
        assert stored["unexplained_rows"] == 0     # 사라진 행이 없다

    def test_an_error_still_stops_the_whole_file(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 「전부 검증 → 전부 저장」은 그대로다."""
        path = book(tmp_path / "err.xlsx", [deal(1), deal(2, amount="숫자아님"), aggregate()])
        status, body = upload(client, path, year=2026)
        assert status == 200, body
        assert body["stored"] is False
        assert body["error_rows"] == 1
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 0


# ======================================================================
# [D] 실제 고객 원본과 같은 **모양**의 합성 파일
# ======================================================================
class TestTheShapeOfTheRealWorkbook:
    def test_the_measured_shape_goes_through_end_to_end(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """STEP 159 실측 구성(2,305 = 2,292 + 13, 그중 앞선 해 5)을 재현한다.

        ⛔ 고객 자료가 아니다 — 건수 구성만 같게 만든 합성 파일이다.
        실제 파일 자체의 검증은 ``scripts/step159_verify_purchase_original.py``
        로 개발 PC 에서 따로 수행한다(저장소에 고객 자료를 두지 않는다).
        """
        rows: list[list[object]] = []
        for index in range(2_292):
            if index < 5:
                rows.append(deal(index + 1, resolution="2025-12-30", issue="2026-01-02"))
            elif index < 5 + 129:
                rows.append(deal(index + 1, amount=-6468))
            elif index < 5 + 131:
                rows.append(deal(index + 1, amount=0))
            else:
                rows.append(deal(index + 1, business_no=SYNTHETIC[index % len(SYNTHETIC)]))
        for _ in range(13):
            rows.append(aggregate())
        path = book(tmp_path / "shape.xlsx", rows)

        result = validate(client, path)
        assert result["source_rows"] == 2_305
        assert result["aggregate_rows"] == 13
        assert result["total_rows"] == 2_292
        assert result["error_rows"] == 0
        assert result["ok"] is True

        status, stored = upload(client, path, year=2026)
        assert status == 200, stored
        assert stored["aggregate_rows"] == 13
        assert stored["prior_year_rows"] == 5
        assert stored["rejected_rows"] == 131
        assert stored["stored_rows"] == 2_292 - 5 - 131
        assert stored["unexplained_rows"] == 0

        count = sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0]
        assert count == 2_156
