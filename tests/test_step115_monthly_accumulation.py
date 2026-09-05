"""
월별 지출 업로드가 **한 해로 쌓이는가** — 그리고 달마다 따로 고쳐지는가.

🟢 2026-09-05 고객 확정

    지출 데이터는 매월 올린다. 대시보드의 실적은 그 해 **현재까지의 누적**이다.
    4월을 올렸다고 1~3월이 사라지면 안 된다.

무엇을 지키는가 (지시서 §5 · §6 · §7 · §8 · §16)
================================================

1. 달을 올릴 때마다 **쌓인다** (1월 → 1~2월 → 1~3월).
2. **같은 달**을 다시 올리면 그 달만 **교체**된다 — ⛔ 더해지지 않는다.
3. 같은 달 교체가 **다른 달을 건드리지 않는다.**
4. 확인 없이 교체하지 않는다 — 409 로 되묻고 **DB 는 그대로**다.
5. **연도가 섞이지 않는다.**
6. 정책별 실적·지출비율·목표 대비 달성률이 **누적값 기준**으로 다시 계산된다.
7. 목표율은 **고정**이고 실적만 달마다 움직인다.

.. note::
    월을 주지 않으면 예전처럼 한 해가 한 덩어리입니다 — 한 해치를 한 번에
    올리던 방식이 그대로 동작합니다.

.. note::
    합성 데이터만 씁니다. 실제 기업명·사업자등록번호는 넣지 않습니다.
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
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — 체크섬만 맞춘 값이며 실제 업체의 번호가 아닙니다.
_MATCHED = "1000000009"  # 정책 목록에 든 업체
_OTHER = "1000000014"  # 목록에 없는 업체


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "monthly.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    assert main(["targets", "--year", "2027", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _purchase_row(*, day: str, amount: int, business_no: str = _MATCHED) -> list[object]:
    values = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
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
    business_no: str = _MATCHED,
    replace: bool = False,
    tag: str = "",
) -> httpx.Response:
    day = f"{year}-{month:02d}-15"
    path = _purchase_file(
        tmp_path / f"{year}-{month:02d}{tag}.xlsx",
        [_purchase_row(day=day, amount=amount, business_no=business_no)],
    )
    return _upload(client, path, year=year, month=month, replace=replace)


def _total(client: TestClient, year: int = 2026) -> str:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return str(payload["total_purchase_amount"])


def _policy(client: TestClient, code: str, year: int = 2026) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == code))


def _register_startup(client: TestClient, tmp_path: Path) -> None:
    """합성 창업기업 목록 한 곳을 등록합니다 (2026~2027 유효)."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    sheet.append([_MATCHED, "합성기업", "가나다", "2026-01-01", "2027-12-31"])
    path = tmp_path / "startup.xlsx"
    book.save(path)
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": "STARTUP"}
    )
    assert response.status_code == 200, response.text
    client.post("/purchases/rematch")


class TestTheMonthsAddUp:
    """§5 — 달을 올릴 때마다 쌓인다."""

    def test_one_two_three_months(self, client: TestClient, tmp_path: Path) -> None:
        assert _upload_month(client, tmp_path, month=1, amount=100_000).status_code == 200
        assert _total(client) == "100000"

        assert _upload_month(client, tmp_path, month=2, amount=200_000).status_code == 200
        assert _total(client) == "300000"  # ⭐ 1월이 남아 있다

        assert _upload_month(client, tmp_path, month=3, amount=300_000).status_code == 200
        assert _total(client) == "600000"


class TestTheSameMonthIsReplaced:
    """§6 — 같은 달 수정본은 **교체**된다. ⛔ 더해지지 않는다."""

    def test_it_asks_before_replacing(self, client: TestClient, tmp_path: Path) -> None:
        """§6 · 확인 없이 교체하지 않는다 — 그리고 DB 가 그대로다."""
        _upload_month(client, tmp_path, month=1, amount=100_000)

        response = _upload_month(client, tmp_path, month=1, amount=120_000, tag="-fix")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "EXISTING_PERIOD"
        assert detail["month"] == 1
        assert "2026년 1월" in detail["message"]  # ⭐ 연이 아니라 그 달을 가리킨다
        assert _total(client) == "100000"  # ⛔ 거절했으니 아무것도 바뀌지 않았다

    def test_the_corrected_file_replaces_that_month(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _upload_month(client, tmp_path, month=1, amount=100_000)
        assert (
            _upload_month(
                client, tmp_path, month=1, amount=120_000, replace=True, tag="-fix"
            ).status_code
            == 200
        )
        # ⛔ 220,000 이 아니다.
        assert _total(client) == "120000"

    def test_replacing_one_month_leaves_the_others(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """§7 — 3월을 고쳐도 1·2월은 그대로."""
        _upload_month(client, tmp_path, month=1, amount=100_000)
        _upload_month(client, tmp_path, month=2, amount=200_000)
        _upload_month(client, tmp_path, month=3, amount=300_000)
        assert _total(client) == "600000"

        _upload_month(client, tmp_path, month=3, amount=350_000, replace=True, tag="-fix")
        # 1월 100,000 + 2월 200,000 + 3월 350,000
        assert _total(client) == "650000"


class TestTheYearsStayApart:
    """§8 — 2027년을 올려도 2026년 누적이 변하지 않는다."""

    def test_uploading_next_year_does_not_touch_this_one(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _upload_month(client, tmp_path, year=2026, month=1, amount=100_000)
        _upload_month(client, tmp_path, year=2026, month=2, amount=200_000)
        assert _total(client, 2026) == "300000"

        assert (
            _upload_month(client, tmp_path, year=2027, month=1, amount=500_000).status_code == 200
        )
        assert _total(client, 2026) == "300000"  # ⭐ 그대로
        assert _total(client, 2027) == "500000"


class TestTheAchievementFollowsTheAccumulation:
    """§4 · §9 · §10 — 목표는 고정, 실적만 달마다 움직인다."""

    def test_the_policy_amount_and_rate_grow_each_month(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _register_startup(client, tmp_path)

        # 1월: 정책 40,000 / 전체 100,000
        _upload_month(client, tmp_path, month=1, amount=40_000)
        _upload_month(client, tmp_path, month=1, amount=60_000, business_no=_OTHER, tag="-o")
        # 같은 달 두 파일은 교체되므로, 한 달 안의 여러 건은 한 파일에 담는다.
        day = "2026-01-15"
        combined = _purchase_file(
            tmp_path / "2026-01-both.xlsx",
            [
                _purchase_row(day=day, amount=40_000),
                _purchase_row(day=day, amount=60_000, business_no=_OTHER),
            ],
        )
        _upload(client, combined, month=1, replace=True)
        client.post("/purchases/rematch")

        row = _policy(client, "STARTUP")
        assert _total(client) == "100000"
        assert row["purchase_amount"] == "40000"
        assert Decimal(row["target_rate"]) == Decimal("3.4")

        # 2월: 정책 60,000 / 전체 200,000  →  누적 정책 100,000 / 전체 300,000
        day = "2026-02-15"
        february = _purchase_file(
            tmp_path / "2026-02-both.xlsx",
            [
                _purchase_row(day=day, amount=60_000),
                _purchase_row(day=day, amount=140_000, business_no=_OTHER),
            ],
        )
        _upload(client, february, month=2)
        client.post("/purchases/rematch")

        row = _policy(client, "STARTUP")
        assert _total(client) == "300000"
        assert row["purchase_amount"] == "100000"  # ⭐ 누적
        # 지출비율 100,000 / 300,000 = 33.33% · 목표 3.4% → 달성률 980.39%
        rate = Decimal(row["purchase_amount"]) / Decimal(_total(client)) * 100
        expected = (rate / Decimal("3.4") * 100).quantize(Decimal("0.01"))
        assert Decimal(row["achievement_rate"]).quantize(Decimal("0.01")) == expected
        # 목표는 움직이지 않았다.
        assert Decimal(row["target_rate"]) == Decimal("3.4")

    def test_an_unregistered_policy_stays_unavailable_while_months_pile_up(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """§13 회귀 — 누적이 조회불가/미해당 구분을 흐리지 않는다."""
        _register_startup(client, tmp_path)
        _upload_month(client, tmp_path, month=1, amount=100_000)
        _upload_month(client, tmp_path, month=2, amount=200_000)
        client.post("/purchases/rematch")

        assert _policy(client, "STARTUP")["purchase_amount"] == "300000"
        # 목록을 올린 적 없는 정책은 여전히 조회불가 — ⛔ 0원이 아니다.
        unavailable = _policy(client, "DISABLED")
        assert unavailable["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert unavailable["purchase_amount"] is None


class TestTheWholeYearUploadStillWorks:
    """월을 주지 않으면 예전 동작 그대로 — 한 해가 한 덩어리."""

    def test_a_yearly_upload_needs_no_month(self, client: TestClient, tmp_path: Path) -> None:
        path = _purchase_file(
            tmp_path / "2026.xlsx", [_purchase_row(day="2026-05-01", amount=1_000_000)]
        )
        assert _upload(client, path).status_code == 200
        assert _total(client) == "1000000"

        # 같은 한 해를 다시 올리면 예전처럼 되묻는다.
        again = _purchase_file(
            tmp_path / "2026-fix.xlsx", [_purchase_row(day="2026-05-01", amount=900_000)]
        )
        response = _upload(client, again)
        assert response.status_code == 409
        assert response.json()["detail"]["month"] is None
        assert "2026년 데이터가" in response.json()["detail"]["message"]
