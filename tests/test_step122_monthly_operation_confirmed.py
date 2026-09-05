"""
STEP 120 — 월별 운영 확정과 **연도·월이 무엇으로 정해지는가**.

🟢 2026-09-05 고객 확정
=======================

1. 지출데이터는 **월별로** 올린다.
2. 같은 달을 다시 올리면 되묻고, 승인하면 그 달만 교체한다.
3. 메인화면에 그 사실을 안내한다.
4. **2025년 데이터는 2025년, 2026년 데이터는 2026년**으로 관리한다.
5. 지금까지 받은 파일은 **검증·예시용**이다 — 그 파일의 건수·금액을 업무규칙으로
   쓰지 않는다.

이 파일이 고정하는 것
=====================
확정된 운영이 실제로 그렇게 도는지, 그리고 **연도·월이 무엇으로 정해지는지**를
드러낸다. STEP 119 가 만든 월별 현황·교체·겹침 차단은 그대로 두고, 여기서는
**연도 분리**와 **예시 데이터가 코드에 스며들지 않았는지**를 본다.

두 축이 무엇인가 (§9 · §10)
============================

===================  ==================================================
**배치 기간**        올린 사람이 고른 연도·월 (``2026년 8월`` → 8/1~8/31)
**연도·월 귀속**     파일 안의 **결의일자** (🟢 §0.10 · STEP 86)
===================  ==================================================

🟢 **2026-09-05 고객 확정(STEP 121)** — 둘은 **서로 맞아야 한다.** 고른 기간
밖의 결의일자가 하나라도 있으면 파일 전체를 거절한다(확인 요청서 ⑯ → ③안).

이 STEP 을 쓸 때는 그것이 정해지지 않아 「받아들이는 현재 동작」을 기록해 두었고,
답이 온 뒤 :class:`TestWhatDecidesTheYearAndMonth` 에서 기대값을 뒤집었다.
거절 동작의 상세한 시험은 ``test_step123_upload_period_mismatch.py`` 에 있다.

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호·파일명은 넣지 않는다.
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
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_BNO = "1000000009"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step120.db"
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
    year: int,
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
    year: int,
    month: int,
    amount: int,
    replace: bool = False,
    tag: str = "",
) -> httpx.Response:
    path = _purchase_file(
        tmp_path / f"{year}-{month:02d}{tag}.xlsx",
        [(f"{year}-{month:02d}-15", amount)],
    )
    return _upload(client, path, year=year, month=month, replace=replace)


def _uploaded(client: TestClient, year: int) -> list[int]:
    payload = client.get("/uploads/purchases/months", params={"year": year}).json()
    return sorted(entry["month"] for entry in payload["months"] if entry["uploaded"])


def _total(client: TestClient, year: int) -> Decimal:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return _won(payload["total_purchase_amount"])


def _policy(client: TestClient, code: str, year: int) -> dict[str, object]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == code))


# ======================================================================
# §3 · §12  월별로 올리고 한 해로 쌓는다
# ======================================================================
class TestTheMonthsAccumulateIntoTheYear:
    def test_1_three_months_add_up(self, client: TestClient, tmp_path: Path) -> None:
        """1월 100 · 2월 200 · 3월 300 → 누적 600."""
        for month, amount in ((1, 1_000_000), (2, 2_000_000), (3, 3_000_000)):
            assert (
                _upload_month(client, tmp_path, year=2026, month=month, amount=amount).status_code
                == 200
            )

        assert _uploaded(client, 2026) == [1, 2, 3]
        assert _total(client, 2026) == 6_000_000

    def test_2_replacing_the_middle_month_recomputes(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """2월을 250 으로 교체 → 누적 650. ⛔ 850 이 되지 않는다."""
        for month, amount in ((1, 1_000_000), (2, 2_000_000), (3, 3_000_000)):
            assert (
                _upload_month(client, tmp_path, year=2026, month=month, amount=amount).status_code
                == 200
            )

        assert (
            _upload_month(
                client, tmp_path, year=2026, month=2, amount=2_500_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _total(client, 2026) == 6_500_000
        assert _uploaded(client, 2026) == [1, 2, 3]


# ======================================================================
# §4 · §16  같은 달 재업로드 — 되묻고, 취소하면 그대로
# ======================================================================
class TestTheSameMonthAsksFirst:
    @pytest.fixture
    def august(self, client: TestClient, tmp_path: Path) -> TestClient:
        assert (
            _upload_month(client, tmp_path, year=2026, month=8, amount=8_000_000).status_code == 200
        )
        return client

    def test_3_it_refuses_without_confirmation(self, august: TestClient, tmp_path: Path) -> None:
        """확인 없이는 교체하지 않는다 — 409, DB 는 그대로."""
        response = _upload_month(august, tmp_path, year=2026, month=8, amount=9_000_000, tag="b")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EXISTING_PERIOD"
        assert _total(august, 2026) == 8_000_000

    def test_4_confirming_replaces_only_that_month(
        self, august: TestClient, tmp_path: Path
    ) -> None:
        """승인하면 그 달만 새 데이터가 된다."""
        assert (
            _upload_month(
                august, tmp_path, year=2026, month=8, amount=9_000_000, replace=True, tag="b"
            ).status_code
            == 200
        )

        assert _total(august, 2026) == 9_000_000
        assert _uploaded(august, 2026) == [8]


# ======================================================================
# §7  연도 분리 — 서로를 건드리지 않는다
# ======================================================================
class TestTheYearsAreIndependent:
    @pytest.fixture
    def both(self, client: TestClient, tmp_path: Path) -> TestClient:
        for month, amount in ((3, 3_000_000), (8, 8_000_000)):
            assert (
                _upload_month(
                    client, tmp_path, year=2025, month=month, amount=amount, tag="y25"
                ).status_code
                == 200
            )
            assert (
                _upload_month(
                    client, tmp_path, year=2026, month=month, amount=amount * 10, tag="y26"
                ).status_code
                == 200
            )
        return client

    def test_5_each_year_keeps_its_own_months(self, both: TestClient) -> None:
        assert _uploaded(both, 2025) == [3, 8]
        assert _uploaded(both, 2026) == [3, 8]
        assert _uploaded(both, 2027) == []

    def test_6_each_year_keeps_its_own_total(self, both: TestClient) -> None:
        assert _total(both, 2025) == 11_000_000
        assert _total(both, 2026) == 110_000_000

    def test_7_replacing_2026_leaves_2025_alone(self, both: TestClient, tmp_path: Path) -> None:
        """⭐ 2026년 8월을 교체해도 2025년은 그대로다."""
        before = _total(both, 2025)

        assert (
            _upload_month(
                both, tmp_path, year=2026, month=8, amount=1_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _total(both, 2025) == before == 11_000_000
        assert _uploaded(both, 2025) == [3, 8]

    def test_8_replacing_2025_leaves_2026_alone(self, both: TestClient, tmp_path: Path) -> None:
        """반대도 같다 — 2025년을 교체해도 2026년은 그대로다."""
        before = _total(both, 2026)

        assert (
            _upload_month(
                both, tmp_path, year=2025, month=8, amount=1_000, replace=True, tag="new"
            ).status_code
            == 200
        )

        assert _total(both, 2026) == before == 110_000_000
        assert _uploaded(both, 2026) == [3, 8]

    def test_9_the_policy_rates_stay_within_their_year(
        self, both: TestClient, tmp_path: Path
    ) -> None:
        """정책별 결과도 연도별로 따로 선다."""
        before = _policy(both, "STARTUP", 2025)

        assert (
            _upload_month(
                both, tmp_path, year=2026, month=3, amount=99_000_000, replace=True, tag="big"
            ).status_code
            == 200
        )

        assert _policy(both, "STARTUP", 2025) == before


# ======================================================================
# §9 · §10  연도·월을 정하는 것은 무엇인가
# ======================================================================
class TestWhatDecidesTheYearAndMonth:
    """두 축이 무엇인지, 그리고 **이제는 서로 맞아야 한다**는 것.

    * **배치 기간** — 올린 사람이 고른 연도·월
    * **연도·월 귀속** — 파일 안의 결의일자

    .. note::
        🟢 **2026-09-05 고객 확정(STEP 121) — 확인 요청서 ⑯ 은 ③안으로 정해졌다.**

        이 STEP 을 쓸 때는 「섞여 있을 때 받아 줄 것인가」가 정해지지 않아
        **받아들이는 현재 동작**을 기록해 두고, 답이 오면 뒤집기로 했다.
        답이 왔으므로 **여기서 뒤집는다** — 고른 기간 밖의 결의일자가 하나라도
        있으면 파일 전체를 거절한다.

        어긋난 파일을 거절하는 상세한 시험은
        ``test_step123_upload_period_mismatch.py`` 에 있다. 이 클래스는 **두 축이
        무엇인가**만 남겨 둔다.
    """

    @pytest.fixture
    def august(self, client: TestClient, tmp_path: Path) -> TestClient:
        """「2026년 8월」로 올리고 파일도 모두 8월이다."""
        path = _purchase_file(
            tmp_path / "august.xlsx",
            [("2026-08-10", 1_000), ("2026-08-20", 2_000)],
        )
        assert _upload(client, path, year=2026, month=8).status_code == 200
        return client

    def test_10_a_mixed_file_is_now_refused_whole(self, client: TestClient, tmp_path: Path) -> None:
        """⭐ 8월·7월·2025년 12월이 섞인 파일은 **전체가 거절**된다."""
        path = _purchase_file(
            tmp_path / "mixed.xlsx",
            [("2026-08-10", 1_000), ("2026-07-10", 2_000), ("2025-12-10", 3_000)],
        )

        response = _upload(client, path, year=2026, month=8)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "UPLOAD_PERIOD_MISMATCH"
        assert _total(client, 2026) == 0
        assert _total(client, 2025) == 0

    def test_11_the_resolution_date_decides_the_year(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 연도 귀속은 여전히 결의일자다 — 2025년 거래는 2025년으로 간다."""
        path = _purchase_file(tmp_path / "y2025.xlsx", [("2025-12-10", 3_000)])
        assert _upload(client, path, year=2025, month=12).status_code == 200

        assert _total(client, 2025) == 3_000
        assert _uploaded(client, 2025) == [12]
        assert _total(client, 2026) == 0

    def test_12_the_resolution_date_decides_the_month(self, august: TestClient) -> None:
        """월 귀속도 결의일자다 — 이제 고른 달과 반드시 같다."""
        assert _uploaded(august, 2026) == [8]
        assert _total(august, 2026) == 3_000

    def test_13_the_batch_period_is_what_the_uploader_chose(
        self, db: Path, august: TestClient
    ) -> None:
        """배치 기간은 **고른 값 그대로**다 — 파일 내용으로 넓히지 않는다."""
        import sqlite3

        connection = sqlite3.connect(db)
        try:
            periods = list(
                connection.execute(
                    "SELECT period_start, period_end FROM import_batch WHERE status = 'ACTIVE'"
                )
            )
        finally:
            connection.close()

        assert periods == [("2026-08-01", "2026-08-31")]


# ======================================================================
# §8 · §16 · §19  예시 파일이 코드에 스며들지 않았는가
# ======================================================================
class TestNoSampleDataLeakedIntoTheCode:
    """⛔ 검증용 파일의 값은 **결과**이지 규칙이 아니다."""

    @staticmethod
    def _source_files() -> list[Path]:
        return sorted((Path("src") / "procurement").rglob("*.py"))

    def test_14_no_measured_figure_is_hardcoded(self) -> None:
        """검증에서 나온 숫자가 소스에 들어 있지 않다.

        ⛔ 「6월은 2건」·「2026년은 2,079건」·특정 금액 같은 값을 코드가 알고
        있으면, 다음 달 파일이 올라왔을 때 틀린 답을 낸다.
        """
        import ast

        # 검증에서 나온 값들 — ⛔ 어느 것도 소스에 있으면 안 된다.
        forbidden = {
            2079,
            10349192149,
            1525413644,
            882639071,
            357049330,
            210860703,
            80013376,
            55435904,
            150778,
        }
        offenders: list[str] = []
        for path in self._source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # bool 은 int 의 부분집합이라 먼저 걸러 낸다.
                if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
                    continue
                if isinstance(node.value, int) and node.value in forbidden:
                    offenders.append(f"{path}:{node.lineno}: {node.value}")
        assert offenders == []

    def test_15_no_customer_file_is_referenced(self) -> None:
        """⛔ 고객이 주신 **그 파일**을 소스가 참조하지 않는다.

        .. note::
            표준 양식 안내에 나오는 ``2026년_구매실적.xlsx`` 같은 **예시 이름**은
            여기에 해당하지 않는다 — 사용자가 만들 파일의 생김새를 보여 주는
            글일 뿐, 특정 파일을 찾아 읽는 코드가 아니다.
        """
        offenders: list[str] = []
        for path in self._source_files():
            text = path.read_text(encoding="utf-8")
            for marker in (
                "공공구매 데이터 원본",
                "복지공장 현황",
                "인증리스트",
                "여성(상세)",
                "장애인(상세)",
                "창업(상세)",
                ".claude/uploads",  # 올려 주신 파일이 놓인 자리를 코드가 뒤지지 않는다
            ):
                if marker in text:
                    offenders.append(f"{path}: {marker}")
        assert offenders == []

    def test_16_no_company_is_special_cased(self) -> None:
        """⛔ 특정 업체를 특별 취급하는 사업자번호 목록이 없다.

        무서운 것은 사업자번호가 **글자로 등장하는 것**이 아니라, 그 값으로
        **판정이 갈리는 것**이다. 그래서 「사업자번호 여러 개를 모아 둔 상수」를
        찾는다 — 그런 목록이 생기는 순간 명단이 코드에 박힌다.

        .. note::
            설명글과 표준 양식의 **예시 한 개**(``220-81-62517``)는 판정에 쓰이지
            않는다. 이 시험은 그것을 잡지 않고, **모아 둔 것**만 잡는다.
        """
        import ast
        import re

        pattern = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
        offenders: list[str] = []
        for path in self._source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.List | ast.Tuple | ast.Set):
                    continue
                numbers = [
                    element.value
                    for element in node.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    and pattern.match(element.value)
                ]
                if numbers:
                    offenders.append(f"{path}:{node.lineno}: {numbers}")
        assert offenders == []


# ======================================================================
# §5 · §6  메인화면 안내와 월별 현황
# ======================================================================
class TestTheScreenExplainsIt:
    @pytest.fixture
    def page(self, client: TestClient) -> str:
        response = client.get("/")
        assert response.status_code == 200
        body: str = response.text
        return body

    def test_17_the_upload_card_says_monthly_uploads_are_possible(self, page: str) -> None:
        """⭐ §5 가 요구한 문장이 화면에 있다."""
        assert "월별로 업로드" in page
        assert 'id="upload-monthly-guide"' in page

    def test_18_it_also_explains_the_replacement(self, page: str) -> None:
        """이미 등록된 월을 다시 올리면 교체된다는 사실도 알린다."""
        assert "이미 등록된 월을 다시 올리면" in page
        assert "삭제하고 새 데이터로 교체" in page

    def test_19_it_uses_the_existing_notice_style(self, page: str) -> None:
        """⛔ 새 UI 부품을 만들지 않았다 — 기존 안내 스타일을 그대로 쓴다."""
        assert '<div class="notice" id="upload-monthly-guide">' in page

    def test_20_the_monthly_status_card_is_still_there(self, page: str) -> None:
        """STEP 119 의 월별 현황 카드를 그대로 둔다."""
        assert "월별 지출데이터 적재 현황" in page
        assert 'id="upload-months"' in page

    def test_21_still_no_monthly_achievement_chart(self, page: str) -> None:
        """⛔ 월별 달성률 차트를 만들지 않았다."""
        assert "월별 달성률" not in page
