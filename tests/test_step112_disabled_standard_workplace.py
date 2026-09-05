"""
장애인표준사업장 정책 — 지금 무엇이 되고 무엇이 막혀 있는가.

⚠️ **실제 장애인표준사업장 파일이 실행환경에 없습니다.** 그래서 이 파일은
합성 데이터로 구조를 확인합니다. 실제 파일이 오면 같은 경로를 태웁니다 —
⛔ 새 업로드 경로를 만들지 않았습니다.

무엇을 지키는가 (지시서 §2 · §8 · §10 · §12 · §13)
==================================================

1. ``DISABLED_STANDARD_WORKPLACE`` 는 ``DISABLED`` 와 **다른 정책**이다.
2. 목표율은 서로 **섞이지 않는다** (표준사업장 0.8% · 장애인기업 1%).
3. 목록 미등록 → **조회불가**. 등록했는데 매칭 0건 → **미해당(0원)**.
4. 판정 기준일은 **결의일자**. 시작일·종료일 당일은 인정.
5. 한 거래가 두 정책에 해당하면 **각각** 들어간다. ⛔ 차감 없음.
6. 🔴 **종료일이 없는 파일은 지금 등록되지 않는다** — 그 현재 동작을 적어 둔다.

.. warning::
    ⛔ ``타인증구분``(사회적기업·여성기업·장애인기업…)과 ``인증유형``
    (일반·자회사)으로 정책을 다시 나누지 않습니다. 그 파일은 **장애인표준
    사업장 목록**이며, 그 안의 다른 인증 표시는 참고 정보입니다.

.. note::
    합성 데이터만 씁니다. 실제 기업명·사업자등록번호는 넣지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.core.open_ended_certification import allows_open_ended
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값입니다.
_WORKPLACE = "1000000009"
_ELSEWHERE = "1000000014"

#: 인증 유효기간.
_FROM = date(2026, 3, 1)
_TO = date(2026, 9, 30)

#: 정책 코드 — ⛔ 추측하지 않고 현재 등록값을 씁니다.
_CODE = "DISABLED_STANDARD_WORKPLACE"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "workplace.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _company_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase(db: Path, business_no: str, *, resolution: date, amount: str) -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name="합성업체",
            resolution_date=resolution,
            amount=Decimal(amount),
        )
    )


def _upload(client: TestClient, path: Path, code: str = _CODE) -> dict[str, Any]:
    response = client.post("/companies/upload", json={"file_path": str(path), "policy_code": code})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _row(client: TestClient, code: str = _CODE) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    return dict(next(r for r in payload["policies"] if r["policy_code"] == code))


class TestItIsADifferentPolicyFromDisabled:
    """§2 — ⛔ 장애인기업과 혼동하지 않는다."""

    def test_the_two_policies_are_separate(self, db: Path) -> None:
        repository = PolicyRepository(db)
        workplace = repository.find_by_policy_code(_CODE)
        disabled = repository.find_by_policy_code("DISABLED")
        assert workplace is not None and disabled is not None
        assert workplace.policy_id != disabled.policy_id
        assert workplace.policy_name == "장애인표준사업장"
        assert disabled.policy_name == "장애인기업"
        # §7 — 판정 기준일은 둘 다 결의일자다.
        assert workplace.evaluation_basis == "RESOLUTION_DATE"

    def test_the_targets_do_not_bleed_into_each_other(self, client: TestClient) -> None:
        """§10 · §16 — 정책별 목표가 섞이지 않는다."""
        items = client.get("/policy-targets", params={"year": 2026}).json()["items"]
        rates = {i["policy_code"]: i["target_rate"] for i in items}
        assert Decimal(rates[_CODE]) == Decimal("0.8")
        assert Decimal(rates["DISABLED"]) == Decimal("1")

    def test_registering_one_does_not_certify_the_other(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _WORKPLACE, resolution=date(2026, 5, 1), amount="1000")
        path = _company_file(
            tmp_path / "workplace.xlsx",
            [[_WORKPLACE, "합성사업장", "가나다", _FROM.isoformat(), _TO.isoformat()]],
        )
        _upload(client, path)
        client.post("/purchases/rematch")

        assert _row(client)["purchase_amount"] == "1000"
        # 장애인기업 목록은 올린 적이 없다 → 여전히 조회불가.
        assert _row(client, "DISABLED")["status"] == "COMPANY_DATA_NOT_REGISTERED"


class TestNotRegisteredVersusNoMatch:
    """§12 — 조회불가와 미해당은 다르다."""

    def test_before_any_upload_it_is_unavailable(self, client: TestClient, db: Path) -> None:
        _purchase(db, _WORKPLACE, resolution=date(2026, 5, 1), amount="1000")
        row = _row(client)
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None  # ⛔ 0원이 아니다
        assert row["achievement_rate"] is None  # ⛔ 0% 도 아니다

    def test_a_registered_list_with_no_match_is_zero_not_unavailable(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⭐ §12 의 핵심 — 목록은 있는데 맞는 업체가 하나도 없는 경우.

        이때는 **셀 수 있었고 해당 업체가 없었을 뿐**입니다. 조회불가로
        처리하면 "판단할 수 없다" 는 거짓말이 됩니다.
        """
        _purchase(db, _ELSEWHERE, resolution=date(2026, 5, 1), amount="1000")
        path = _company_file(
            tmp_path / "workplace.xlsx",
            [[_WORKPLACE, "합성사업장", "가나다", _FROM.isoformat(), _TO.isoformat()]],
        )
        _upload(client, path)
        client.post("/purchases/rematch")

        row = _row(client)
        assert row["status"] != "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] == "0"  # 미해당 — 셀 수 있었고 0원이다
        assert Decimal(row["target_rate"]) == Decimal("0.8")
        assert Decimal(row["achievement_rate"]) == Decimal("0")


class TestTheResolutionDateBoundaries:
    """§8 — 시작일·종료일 당일은 인정, 밖은 제외."""

    @pytest.mark.parametrize(
        ("resolution", "expected"),
        [
            (date(2026, 2, 28), "0"),  # 시작일 하루 전 → 제외
            (_FROM, "1000"),  # 시작일 당일 → 인정
            (date(2026, 6, 15), "1000"),  # 기간 중 → 인정
            (_TO, "1000"),  # 종료일 당일 → 인정
            (date(2026, 10, 1), "0"),  # 종료일 다음날 → 제외
        ],
    )
    def test_only_purchases_inside_the_period_count(
        self, client: TestClient, db: Path, tmp_path: Path, resolution: date, expected: str
    ) -> None:
        _purchase(db, _WORKPLACE, resolution=resolution, amount="1000")
        path = _company_file(
            tmp_path / "workplace.xlsx",
            [[_WORKPLACE, "합성사업장", "가나다", _FROM.isoformat(), _TO.isoformat()]],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _row(client)["purchase_amount"] == expected

    def test_the_issue_date_is_not_the_basis(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 신고기준일로 판정하지 않는다."""
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_WORKPLACE,
                company_name="합성업체",
                resolution_date=date(2026, 12, 1),  # 기간 밖
                issue_date=date(2026, 5, 1),  # 기간 안 — ⛔ 쓰이면 안 된다
                amount=Decimal("1000"),
            )
        )
        path = _company_file(
            tmp_path / "workplace.xlsx",
            [[_WORKPLACE, "합성사업장", "가나다", _FROM.isoformat(), _TO.isoformat()]],
        )
        _upload(client, path)
        client.post("/purchases/rematch")
        assert _row(client)["purchase_amount"] == "0"


class TestPoliciesStayIndependent:
    """§13 — 같은 거래가 두 정책에 각각 들어간다. ⛔ 차감 없음."""

    def test_one_purchase_counts_in_both(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _purchase(db, _WORKPLACE, resolution=date(2026, 5, 1), amount="1000")
        for code in (_CODE, "DISABLED"):
            _upload(
                client,
                _company_file(
                    tmp_path / f"{code}.xlsx",
                    [[_WORKPLACE, "합성업체", "가나다", _FROM.isoformat(), _TO.isoformat()]],
                ),
                code,
            )
        client.post("/purchases/rematch")

        assert _row(client)["purchase_amount"] == "1000"
        assert _row(client, "DISABLED")["purchase_amount"] == "1000"
        # 분모는 한 번만 센다.
        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        assert payload["total_purchase_amount"] == "1000"


class TestAFileWithNoEndDateIsRefusedToday:
    """🔴 §8 · §19 — 지금 무슨 일이 일어나는지 적어 둔다.

    실제 장애인표준사업장 자료에는 「인증일자」만 있고 **종료일 칸이 없습니다.**
    그런데 종료일을 비울 수 있는 정책은 사회적기업·사회적협동조합 둘뿐이라
    (🟢 2026-09-04 고객 확정 · DECISIONS §0.27.3), 이 정책은 종료일이 비면
    등록되지 않습니다.

    .. warning::
        ⛔ 고객 확정 없이 이 정책을 그 명단에 넣지 않았습니다. 넣으면
        「인증일자 이후 영원히 유효」라는, 아무도 정한 적 없는 규칙이 실적
        숫자를 만들게 됩니다. ⛔ 임의의 종료일을 지어내지도 않았습니다.

        이 시험은 **규칙이 아니라 현재 상태**를 적습니다. 고객이 답하면
        기대값을 바꾸고 사유를 적습니다.
    """

    def test_the_policy_is_not_on_the_open_ended_list(self) -> None:
        assert allows_open_ended(_CODE) is False
        assert allows_open_ended("SOCIAL_ENTERPRISE") is True

    def test_a_blank_end_date_stops_the_whole_upload(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        path = _company_file(
            tmp_path / "no-end.xlsx",
            [[_WORKPLACE, "합성사업장", "가나다", _FROM.isoformat(), None]],
        )
        result = _upload(client, path)

        assert result["stored"] is False
        assert any("유효종료일" in issue for issue in result["issues"])
        # 화면은 여전히 «조회불가» — ⛔ 0% 로 떨어뜨리지 않는다.
        assert _row(client)["status"] == "COMPANY_DATA_NOT_REGISTERED"
