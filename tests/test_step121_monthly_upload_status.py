"""
STEP 119 — 월별 지출데이터 **적재 현황**과 같은 달 재업로드 교체.

무엇을 만들었는가
=================
「올해 몇 월 데이터까지 올라왔는가」를 대시보드에서 한눈에 보게 했다.

⭐ **월별 달성률이 아니다.** 달마다 올렸는지 안 올렸는지 하나만 답한다
(지시서 §5 · §19 「월별 달성률 추이 그래프 추가 금지」).

판정 기준은 계산과 **같은 데이터**다 — 활성 배치의 행을 **결의일자**로 가른다.
그래서 「업로드 완료」로 보이는 달은 반드시 누적 실적에도 들어가 있다(§7).

⛔ 새 표를 만들지 않았다(§12) — ``import_batch`` 와 ``purchase`` 로 답할 수
있었다. ⛔ 「몇 건 이상이면 완료」 같은 기준도 두지 않았다.

같은 달 재업로드
================
기존 흐름(STEP 113 · PM-005)을 그대로 쓴다.

1. 파일 검증 → 오류가 있으면 **적재 계층을 부르지 않는다**(기존 데이터 무사)
2. 그 달에 데이터가 있으면 **409** 로 되묻는다(DB 는 그대로)
3. 사용자가 [취소] → 아무것도 바뀌지 않는다
4. 사용자가 [교체] → ``replace_existing: true`` 로 다시 요청 → 그 달만 교체

⛔ **구매유형 확정을 새 거래에 옮기지 않는다**(§4). 교체된 거래는 새
``purchase_id`` 로 들어오며, 옛 확정은 옛 거래에 남는다. 적요·사업자번호·금액
같은 값으로 이어 붙이지 않는다 — 실제 데이터에서 그 조합이 같은 거래가
존재하기 때문이다(STEP 118: 42묶음 121건).

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호는 넣지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

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
    path = tmp_path / "step119.db"
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


def _purchase_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _broken_file(path: Path) -> Path:
    """머리글이 맞지 않는 파일 — 검증에서 걸린다."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["엉뚱한", "머리글", "입니다"])
    sheet.append([1, 2, 3])
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


def _upload_month(
    client: TestClient,
    tmp_path: Path,
    *,
    year: int = 2026,
    month: int,
    amount: int,
    replace: bool = False,
    tag: str = "",
) -> httpx.Response:
    path = _purchase_file(
        tmp_path / f"{year}-{month:02d}{tag}.xlsx",
        [_purchase_row(day=f"{year}-{month:02d}-15", amount=amount)],
    )
    return _upload(client, path, year=year, month=month, replace=replace)


def _months(client: TestClient, year: int = 2026) -> dict[int, bool]:
    payload = client.get("/uploads/purchases/months", params={"year": year}).json()
    assert payload["year"] == year
    return {entry["month"]: entry["uploaded"] for entry in payload["months"]}


def _uploaded(client: TestClient, year: int = 2026) -> list[int]:
    return sorted(month for month, done in _months(client, year).items() if done)


def _total(client: TestClient, year: int = 2026) -> Decimal:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return _won(payload["total_purchase_amount"])


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
# §16-A · §16-J  월별 현황이 실제 상태와 맞는가
# ======================================================================
class TestTheMonthlyStatusIsHonest:
    def test_a1_nothing_uploaded_shows_twelve_empty_months(self, client: TestClient) -> None:
        """아무것도 안 올렸으면 열두 달 모두 미업로드다 — 칸이 빠지지 않는다."""
        months = _months(client)

        assert sorted(months) == list(range(1, 13))
        assert not any(months.values())

    def test_a2_the_first_month_shows_up_alone(self, client: TestClient, tmp_path: Path) -> None:
        """1월을 올리면 1월만 완료, 2~12월은 미업로드."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200

        assert _uploaded(client) == [1]

    def test_j1_the_status_matches_the_database(self, client: TestClient, tmp_path: Path) -> None:
        """군데군데 빠진 달도 그대로 드러난다."""
        for month in (1, 2, 3, 5, 7, 8):
            assert _upload_month(client, tmp_path, month=month, amount=1_000_000).status_code == 200

        assert _uploaded(client) == [1, 2, 3, 5, 7, 8]
        months = _months(client)
        assert months[4] is False
        assert months[6] is False
        assert months[12] is False

    def test_j2_another_year_is_not_mixed_in(self, client: TestClient, tmp_path: Path) -> None:
        """⛔ 연도가 섞이지 않는다."""
        assert (
            _upload_month(client, tmp_path, year=2026, month=3, amount=1_000_000).status_code == 200
        )
        assert (
            _upload_month(client, tmp_path, year=2027, month=9, amount=2_000_000).status_code == 200
        )

        assert _uploaded(client, 2026) == [3]
        assert _uploaded(client, 2027) == [9]
        assert _uploaded(client, 2025) == []

    def test_j3_the_month_comes_from_the_resolution_date(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 달을 가르는 것은 **결의일자**다 — 신고기준일이 아니다(§2.2).

        결의일자 3월 · 신고기준일 2월인 거래를 3월로 올린다. 3월만 완료여야 한다.
        """
        row = _purchase_row(day="2026-03-10", amount=1_000_000)
        headers = list(header_row())
        row[headers.index("신고기준일")] = "2026-02-25"
        row[headers.index("계약일자")] = "2026-02-01"
        row[headers.index("지급일")] = "2026-04-05"
        path = _purchase_file(tmp_path / "mixed.xlsx", [row])
        assert _upload(client, path, year=2026, month=3).status_code == 200

        assert _uploaded(client) == [3]

    def test_j4_a_superseded_batch_is_not_counted(self, client: TestClient, tmp_path: Path) -> None:
        """대체된 배치는 세지 않는다 — 계산에서도 빠지는 행이기 때문이다."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200
        assert (
            _upload_month(
                client, tmp_path, month=1, amount=5_000_000, replace=True, tag="b"
            ).status_code
            == 200
        )

        assert _uploaded(client) == [1]
        assert _total(client) == 5_000_000  # ⛔ 100만 + 500만 = 600만이 아니다


# ======================================================================
# §16-B · §16-E  누적과 교체
# ======================================================================
class TestTheMonthsAccumulate:
    def test_b_a_new_month_adds_to_the_total(self, client: TestClient, tmp_path: Path) -> None:
        """1월 → 1~2월 누적. ⛔ 1월이 사라지지 않는다."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200
        assert _total(client) == 1_000_000

        assert _upload_month(client, tmp_path, month=2, amount=2_000_000).status_code == 200

        assert _total(client) == 3_000_000
        assert _uploaded(client) == [1, 2]

    def test_e_replacing_a_month_recomputes_the_total(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 지시서 §8 예시 — 1월 100 · 2월 200 · 3월 300 → 2월을 250 으로 교체 → 650."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200
        assert _upload_month(client, tmp_path, month=2, amount=2_000_000).status_code == 200
        assert _upload_month(client, tmp_path, month=3, amount=3_000_000).status_code == 200
        assert _total(client) == 6_000_000

        assert (
            _upload_month(
                client, tmp_path, month=2, amount=2_500_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _total(client) == 6_500_000  # ⛔ 8,500,000 이 아니다
        assert _uploaded(client) == [1, 2, 3]


# ======================================================================
# §16-C · §16-D  확인 팝업 — 묻기 전에는 바꾸지 않는다
# ======================================================================
class TestItAsksBeforeReplacing:
    @pytest.fixture
    def august(self, client: TestClient, tmp_path: Path) -> TestClient:
        assert _upload_month(client, tmp_path, month=8, amount=8_000_000).status_code == 200
        return client

    def test_c1_the_first_upload_of_a_month_asks_nothing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """처음 올리는 달은 되묻지 않는다."""
        response = _upload_month(client, tmp_path, month=8, amount=8_000_000)

        assert response.status_code == 200
        assert response.json()["stored"] is True

    def test_c2_a_second_upload_of_the_same_month_is_refused_with_409(
        self, august: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 확인 없이는 교체하지 않는다 — 409 로 되묻고 **DB 는 그대로**다."""
        response = _upload_month(august, tmp_path, month=8, amount=9_000_000, tag="b")

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "EXISTING_PERIOD"
        assert detail["year"] == 2026
        assert detail["month"] == 8
        assert "8월" in detail["message"]  # 어느 달인지 문장에 들어 있다

    def test_d_cancelling_leaves_everything_alone(self, august: TestClient, tmp_path: Path) -> None:
        """[취소] — 409 를 받고 다시 요청하지 않으면 아무것도 바뀌지 않는다."""
        assert (
            _upload_month(august, tmp_path, month=8, amount=9_000_000, tag="b").status_code == 409
        )

        assert _total(august) == 8_000_000  # 기존 8월 그대로
        assert _uploaded(august) == [8]

    def test_c3_confirming_replaces_that_month(self, august: TestClient, tmp_path: Path) -> None:
        """[기존 데이터 삭제 후 업로드] — 그 달만 새 데이터로 바뀐다."""
        response = _upload_month(august, tmp_path, month=8, amount=9_000_000, replace=True, tag="b")

        assert response.status_code == 200
        assert _total(august) == 9_000_000
        assert _uploaded(august) == [8]


# ======================================================================
# §16-F · §16-G · §9  삭제 범위는 그 달 하나뿐이다
# ======================================================================
class TestOnlyThatMonthIsReplaced:
    @pytest.fixture
    def seeded(self, client: TestClient, tmp_path: Path) -> TestClient:
        for month in range(1, 13):
            assert (
                _upload_month(client, tmp_path, month=month, amount=month * 1_000_000).status_code
                == 200
            )
        assert (
            _upload_month(client, tmp_path, year=2025, month=8, amount=500_000).status_code == 200
        )
        assert (
            _upload_month(client, tmp_path, year=2027, month=8, amount=700_000).status_code == 200
        )
        return client

    def test_f_the_other_months_survive(self, seeded: TestClient, tmp_path: Path) -> None:
        """8월을 교체해도 1~7월과 9~12월이 남는다."""
        before = _total(seeded)  # 1+2+…+12 = 78,000,000
        assert before == 78_000_000

        assert (
            _upload_month(
                seeded, tmp_path, month=8, amount=80_000_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _uploaded(seeded) == list(range(1, 13))
        assert _total(seeded) == 78_000_000 - 8_000_000 + 80_000_000

    def test_g_the_other_years_survive(self, seeded: TestClient, tmp_path: Path) -> None:
        """2026년 8월을 교체해도 2025년·2027년은 그대로다."""
        assert (
            _upload_month(
                seeded, tmp_path, month=8, amount=80_000_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _total(seeded, 2025) == 500_000
        assert _total(seeded, 2027) == 700_000
        assert _uploaded(seeded, 2025) == [8]
        assert _uploaded(seeded, 2027) == [8]


# ======================================================================
# §10 · §16-H  검증에 실패하면 기존 데이터를 지우지 않는다
# ======================================================================
class TestABadFileDestroysNothing:
    def test_h1_a_broken_file_leaves_the_existing_month(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 머리글이 틀린 파일을 올려도 기존 8월이 그대로 남는다."""
        assert _upload_month(client, tmp_path, month=8, amount=8_000_000).status_code == 200

        response = _upload(
            client, _broken_file(tmp_path / "broken.xlsx"), year=2026, month=8, replace=True
        )

        assert response.status_code == 200
        assert response.json()["stored"] is False  # 저장하지 않았다
        assert _total(client) == 8_000_000
        assert _uploaded(client) == [8]

    def test_h2_a_broken_file_does_not_even_reach_the_confirmation(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """검증이 먼저다 — 교체 확인(409)을 묻기도 전에 걸린다."""
        assert _upload_month(client, tmp_path, month=8, amount=8_000_000).status_code == 200

        response = _upload(client, _broken_file(tmp_path / "broken.xlsx"), year=2026, month=8)

        assert response.status_code == 200  # ⛔ 409 가 아니다
        assert response.json()["stored"] is False
        assert _total(client) == 8_000_000


# ======================================================================
# §4 · §16-I  구매유형 확정을 새 거래에 옮기지 않는다
# ======================================================================
class TestTheConfirmationIsNotCarriedOver:
    """⛔ 이 STEP 에서 가장 조심한 부분이다.

    같은 속성(사업자번호·금액·적요·결의일자)을 가진 거래가 실제 데이터에
    존재하므로(STEP 118: 42묶음 121건), 그 값으로 이어 붙이면 A 거래의
    구매유형이 B 거래에 들어간다. ⛔ 그런 추정 연결을 만들지 않았다.
    """

    @pytest.fixture
    def confirmed(self, db: Path, client: TestClient, tmp_path: Path) -> int:
        assert _upload_month(client, tmp_path, month=8, amount=8_000_000).status_code == 200
        purchase_id = _active_ids(db)[0]
        assert (
            client.put(
                f"/reviews/{purchase_id}",
                json={"final_purchase_type": "GOODS", "reviewed_by": "담당자"},
            ).status_code
            == 200
        )
        return purchase_id

    def test_i1_the_replaced_purchase_is_a_new_review_target(
        self, db: Path, client: TestClient, tmp_path: Path, confirmed: int
    ) -> None:
        """교체된 거래는 **새 검토 대상**이다 — 확정이 자동으로 따라오지 않는다."""
        assert (
            _upload_month(
                client, tmp_path, month=8, amount=8_000_000, replace=True, tag="b"
            ).status_code
            == 200
        )

        current = _active_ids(db)
        assert current != [confirmed]
        assert ReviewRepository(db).find_by_purchase_id(current[0]) is None

    def test_i2_the_old_confirmation_is_not_deleted_either(
        self, db: Path, client: TestClient, tmp_path: Path, confirmed: int
    ) -> None:
        """⛔ 옛 확정을 지우지도 않는다 — 누가 무엇을 골랐는지는 사실이다."""
        assert (
            _upload_month(
                client, tmp_path, month=8, amount=8_000_000, replace=True, tag="b"
            ).status_code
            == 200
        )

        kept = ReviewRepository(db).find_by_purchase_id(confirmed)
        assert kept is not None
        assert kept.final_purchase_type == "GOODS"

    def test_i3_no_code_joins_purchases_by_their_values(self) -> None:
        """소스에 「같은 값이면 같은 거래」로 잇는 코드가 없다.

        ⛔ 적요·사업자번호·금액·결의일자를 묶어 옛 확정을 옮기는 경로가 생기면,
        구분되지 않는 121건에서 엉뚱한 거래에 유형이 붙는다.
        """
        import ast

        offenders: list[str] = []
        for path in (Path("src") / "procurement").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                name = node.name.lower()
                if any(
                    word in name
                    for word in ("carry_over", "inherit_review", "migrate_review", "copy_review")
                ):
                    offenders.append(f"{path}:{node.lineno}:{node.name}")
        assert offenders == []


# ======================================================================
# §7 · §17  현황과 누적 계산이 어긋나지 않는가 · 기존 규칙 불변
# ======================================================================
class TestTheStatusAndTheTotalAgree:
    def test_1_every_uploaded_month_is_in_the_total(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 「업로드 완료」로 보이는 달은 반드시 누적 실적에 들어 있다."""
        for month in (1, 4, 11):
            assert (
                _upload_month(client, tmp_path, month=month, amount=month * 1_000_000).status_code
                == 200
            )

        assert _uploaded(client) == [1, 4, 11]
        assert _total(client) == (1 + 4 + 11) * 1_000_000

    def test_2_a_whole_year_upload_still_works(self, client: TestClient, tmp_path: Path) -> None:
        """월을 주지 않는 예전 방식도 그대로 — 들어 있는 달이 완료로 보인다."""
        path = _purchase_file(
            tmp_path / "year.xlsx",
            [
                _purchase_row(day="2026-02-10", amount=1_000_000),
                _purchase_row(day="2026-09-10", amount=2_000_000),
            ],
        )
        assert _upload(client, path, year=2026).status_code == 200

        assert _uploaded(client) == [2, 9]
        assert _total(client) == 3_000_000

    def test_3_the_endpoint_does_not_report_achievement(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 월별 달성률·금액을 내보내지 않는다 — 적재 여부 하나만 답한다."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200

        payload: dict[str, Any] = client.get(
            "/uploads/purchases/months", params={"year": 2026}
        ).json()

        assert set(payload) == {"year", "months"}
        assert set(payload["months"][0]) == {"month", "uploaded"}


# ======================================================================
# §15  화면이 그 현황을 그리는가
# ======================================================================
class TestTheScreenShowsIt:
    @pytest.fixture
    def page(self, client: TestClient) -> str:
        response = client.get("/")
        assert response.status_code == 200
        body: str = response.text
        return body

    def test_4_the_dashboard_has_a_monthly_upload_card(self, page: str) -> None:
        assert "월별 지출데이터 적재 현황" in page
        assert 'id="upload-months"' in page

    def test_5_it_fetches_the_month_endpoint(self, page: str) -> None:
        assert "/uploads/purchases/months?year=" in page

    def test_6_both_states_are_named_in_words_not_only_colour(self, page: str) -> None:
        """색만으로 알리지 않는다 — 「업로드 완료」·「미업로드」 글자를 함께 둔다."""
        assert "업로드 완료" in page
        assert "미업로드" in page

    def test_7_the_replace_prompt_warns_about_the_confirmations(self, page: str) -> None:
        """교체 팝업이 구매유형 확정이 옮겨지지 않는다는 사실을 알린다."""
        assert "삭제하고 새로 업로드한 데이터로" in page
        assert "구매유형은 새 데이터에 자동으로 옮겨지지" in page

    def test_8_no_monthly_achievement_chart_was_added(self, page: str) -> None:
        """⛔ 고객이 요청하지 않은 월별 달성률 추이 그래프를 만들지 않았다."""
        assert "월별 달성률" not in page


# ======================================================================
# §8 · §9  🔴 겹치는 기간은 적재하지 않는다 (STEP 119 에서 잡은 결함)
# ======================================================================
class TestOverlappingPeriodsAreRefused:
    """실제 DB 사본에서 잡은 결함이다.

    실제 2026년 자료는 **한 해치 통짜**(``1/1~12/31``)로 올라와 있었다. 그
    상태에서 6월치를 올리면 기간이 «같지» 않아 교체 확인(409)이 뜨지 않고,
    6월 배치가 **하나 더** 생겨 6월이 두 배치에 남았다 — 2건이던 6월이 3건이
    되고 총액이 늘었다. 지시서 §8 이 금지한 「기존 200만원이 남아 850만원이
    되는」 상태 그대로다.

    ⛔ **교체로 해결할 수 없다.** 겹친 한 해 배치를 대체하면 1~5월까지 함께
    사라진다(§9 금지). ⛔ 그렇다고 한 해 배치를 달 단위로 쪼개는 것은 새 업무
    규칙이다 — 여기서 정하지 않는다.

    그래서 **적재하지 않고 그대로 둔다.** §10 이 요구하는 「기존 데이터 유지
    또는 완전 교체 중 하나의 일관된 상태」 중 앞쪽이다.
    """

    @pytest.fixture
    def whole_year(self, client: TestClient, tmp_path: Path) -> TestClient:
        """한 해치를 통짜로 올려 둔 상태 — 실제 DB 가 놓여 있던 자리."""
        path = _purchase_file(
            tmp_path / "year.xlsx",
            [
                _purchase_row(day="2026-01-10", amount=1_000_000),
                _purchase_row(day="2026-06-10", amount=2_000_000),
            ],
        )
        assert _upload(client, path, year=2026).status_code == 200
        return client

    def test_o1_a_month_upload_over_a_year_batch_is_refused(
        self, whole_year: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 6월치를 올리면 409 로 거절한다 — ⛔ 조용히 더해지지 않는다."""
        response = _upload_month(whole_year, tmp_path, month=6, amount=9_000_000)

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "OVERLAPPING_PERIOD"
        assert detail["month"] == 6
        assert detail["existing"][0]["period_start"] == "2026-01-01"

    def test_o2_the_confirmation_flag_does_not_force_it_through(
        self, whole_year: TestClient, tmp_path: Path
    ) -> None:
        """⛔ ``replace_existing`` 으로도 뚫리지 않는다 — 다른 달이 사라지기 때문이다."""
        response = _upload_month(whole_year, tmp_path, month=6, amount=9_000_000, replace=True)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "OVERLAPPING_PERIOD"

    def test_o3_nothing_changed(self, whole_year: TestClient, tmp_path: Path) -> None:
        """거절 뒤에도 기존 데이터가 그대로다 — 건수도 총액도."""
        before = _total(whole_year)
        assert _upload_month(whole_year, tmp_path, month=6, amount=9_000_000).status_code == 409

        assert _total(whole_year) == before == 3_000_000
        assert _uploaded(whole_year) == [1, 6]

    def test_o4_the_same_whole_year_range_still_replaces_normally(
        self, whole_year: TestClient, tmp_path: Path
    ) -> None:
        """같은 범위(연도 전체)로 다시 올리는 길은 그대로 열려 있다."""
        path = _purchase_file(
            tmp_path / "year2.xlsx",
            [_purchase_row(day="2026-06-10", amount=5_000_000)],
        )
        assert _upload(client=whole_year, path=path, year=2026).status_code == 409  # 확인 요구

        assert _upload(whole_year, path, year=2026, replace=True).status_code == 200
        assert _total(whole_year) == 5_000_000
        assert _uploaded(whole_year) == [6]

    def test_o5_month_batches_do_not_block_each_other(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """달 단위끼리는 겹치지 않으므로 평소대로 쌓인다."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200
        assert _upload_month(client, tmp_path, month=2, amount=2_000_000).status_code == 200

        assert _total(client) == 3_000_000

    def test_o6_a_year_upload_over_month_batches_is_refused_too(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """반대 방향도 같다 — 달치가 있는데 한 해치를 올리면 거절한다."""
        assert _upload_month(client, tmp_path, month=1, amount=1_000_000).status_code == 200

        path = _purchase_file(
            tmp_path / "year.xlsx", [_purchase_row(day="2026-01-10", amount=9_000_000)]
        )
        response = _upload(client, path, year=2026)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "OVERLAPPING_PERIOD"
        assert _total(client) == 1_000_000

    def test_o7_another_year_is_never_in_the_way(self, client: TestClient, tmp_path: Path) -> None:
        """⛔ 다른 연도는 겹치지 않는다 — 막지 않는다."""
        path = _purchase_file(
            tmp_path / "y2025.xlsx", [_purchase_row(day="2025-06-10", amount=1_000_000)]
        )
        assert _upload(client, path, year=2025).status_code == 200

        assert (
            _upload_month(client, tmp_path, year=2026, month=6, amount=2_000_000).status_code == 200
        )
        assert _total(client, 2025) == 1_000_000
        assert _total(client, 2026) == 2_000_000
