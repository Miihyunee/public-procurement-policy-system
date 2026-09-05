"""
STEP 117 — 자활용사촌: **거래한 것**과 **생산가능품목을 산 것**은 다르다.

무엇을 지키는가
===============
자활용사촌 목표는 「**생산가능품목 총 구매액**의 7% 이상」이다. 그래서 분모가
기관 전체 구매금액이 아니라 **생산가능품목 구매액**이고, 분자도 자활용사촌
업체와의 거래 전부가 아니라 **그 중 생산가능품목** 거래다.

두 값 모두 「이 거래가 무슨 품목인가」를 알아야 구할 수 있다. 그 연결키가
없다는 것이 이 파일이 고정하는 사실이다.

⛔ **「자활용사촌 업체와 거래했다」 ≠ 「생산가능품목을 구매했다」**

실제 파일에서 확인한 것 (§1·§2·§3 계측 · 2026-09-05)
=====================================================

자활용사촌 자료 (시트 6개)

====================================  =========================================
복지공장 현황 32행                     사업자번호 31개(고유 30) · ⛔ 인증 시작·종료일 **없음**
생산품목 현황 222행                    G2B 물품목록번호 197개(고유 171) · ⛔ **사업자번호 없음**
물품분류정리 148행                     순번 + 물품분류**명** · ⛔ 분류번호 없음
폐업·승인취소 15행                     ⛔ 사업자번호 없음 · 폐업일은 비고 문장
====================================  =========================================

지출 원본 (2,305행 · 컬럼 10개)

    번호 · 신고기준일 · 적요 · 거래처명 · 사업자번호 ·
    공급가액 · 세액 · 계 · 결의일자 · 예산과목

⛔ G2B 물품목록번호 **없음** · 물품분류번호 **없음** · 품목코드 **없음** ·
계약번호 **없음** · 구매번호 **없음** · 결의번호 **없음**.
「번호」는 1·2·3… 연번이며 적요에 8~10자리 코드가 든 행도 **0건**이다.

→ **양쪽에 공통으로 있는 품목 식별자가 하나도 없다.** 연결키 부재.

그리고 사업자번호로도 이어지지 않는다
=====================================
자활용사촌 사업자 30곳과 2026년 계산대상 2,079건을 사업자등록번호로 맞춰
보았다 — **0건 · 0원**. 겹치는 사업자가 한 곳도 없다.

그래서 분자 후보조차 비어 있다. ⛔ 그렇다고 0% 로 적지 않는다 — 분모를 구할
수 없다는 사실이 먼저이기 때문이다.

.. note::
    아래 시험은 전부 **합성 데이터**다. 실제 고객 결과가 아니며, 실제
    자활용사촌 실적을 대신하지 않는다(지시서 §12).
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
from procurement.core.target_scope import CALCULABLE_SCOPES, PRODUCIBLE_ITEMS
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.uploads.company_format import POLICY_SCOPED_COMPANY_COLUMNS
from procurement.uploads.format import header_row

_CODE = "SELF_SUPPORT_VILLAGE"

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다. 체크섬만 맞춘 값이다.
_VILLAGE = "1000000009"  # 합성 자활용사촌 업체
_OTHER = "1000000014"  # 목록 밖 합성 업체


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step117.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _won(value: object) -> Decimal:
    """금액은 API 가 **문자열**로 준다 — 자릿수를 잃지 않으려고 그렇게 둔 값이다."""
    return Decimal(str(value))


def _company_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase_row(
    *, day: str, amount: int, business_no: str, note: str = "합성 거래"
) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
        "계": amount,
        "신고기준일": day,
        "적요": note,
        "예산과목": "일반수용비",
    }
    return [values[header] for header in header_row()]


def _upload_purchases(
    client: TestClient, path: Path, *, year: int = 2026, month: int | None = None
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/uploads/purchases",
        json={"file_path": str(path), "year": year, "month": month},
    )
    return response


def _upload_companies(client: TestClient, path: Path, *, policy_code: str) -> httpx.Response:
    response: httpx.Response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    return response


def _policy_row(client: TestClient, code: str = _CODE, year: int = 2026) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == code))


# ======================================================================
# §3 · §11 · §17-1·2·7  거래한 것과 품목을 산 것은 다르다
# ======================================================================
class TestTradingIsNotBuyingTheItem:
    """⭐ 이 STEP 의 핵심 — 명단을 등록해도 실적이 만들어지지 않는다."""

    @pytest.fixture
    def registered(self, client: TestClient, tmp_path: Path) -> TestClient:
        """합성 자활용사촌 명단을 등록하고, 그 업체와 크게 거래한다."""
        companies = _company_file(
            tmp_path / "village.xlsx",
            [[_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code=_CODE).status_code == 200
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-05", amount=9_000_000, business_no=_VILLAGE),
                _purchase_row(day="2026-03-06", amount=1_000_000, business_no=_OTHER),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        return client

    def test_1_the_traded_amount_does_not_become_the_numerator(
        self, registered: TestClient
    ) -> None:
        """⛔ 자활용사촌 업체와 900만원을 거래해도 달성률이 나오지 않는다.

        그 900만원이 **생산가능품목** 구매라는 근거가 없기 때문이다.
        """
        row = _policy_row(registered)

        assert row["achievement_rate"] is None
        assert row["status"] == "CALCULATION_ON_HOLD"

        # 「이 업체와 900만원을 거래했다」는 사실 자체는 보여 준다 — 아는 사실이므로.
        # ⛔ 그러나 그것이 생산가능품목 구매액(분자)이 된 것은 아니다.
        assert _won(row["purchase_amount"]) == 9_000_000

    def test_2_the_institution_total_does_not_become_the_denominator(
        self, registered: TestClient
    ) -> None:
        """⛔ 분모를 기관 전체 구매금액(1,000만원)으로 대신하지 않는다.

        대신했다면 900 ÷ 1,000 = 90% → 달성률 1285.71% 가 나왔을 것이다.
        나오지 않는 것이 맞다.
        """
        payload = registered.get("/dashboard/summary", params={"year": 2026}).json()
        assert _won(payload["total_purchase_amount"]) == 10_000_000

        row = _policy_row(registered)
        assert row["achievement_rate"] is None
        assert row["shortage_rate"] is None

    def test_3_the_target_stays_seven_percent_of_producible_items(
        self, db: Path, registered: TestClient
    ) -> None:
        """목표 7% 는 그대로 저장돼 있다 — 못 내는 것은 달성률뿐이다.

        .. note::
            대시보드 요약 줄의 ``target_rate`` 는 「계산 보류」일 때 ``None``
            이다 — 달성률을 낼 수 없는 자리에 목표만 띄우면 「7% 중 얼마」로
            읽히기 때문이다. 목표 자체는 목표 화면(``/policy-targets``)에 있다.
        """
        assert _policy_row(registered)["target_rate"] is None

        targets = PolicyTargetRepository(db).list_by_year(2026)
        village = next(row for row in targets if row.policy_id == _policy_id(db, _CODE))

        assert village.target_rate == Decimal("7")
        assert village.scope == PRODUCIBLE_ITEMS


# ======================================================================
# §2 · §4 · §17-3·4·5  연결키가 양쪽에 없다
# ======================================================================
class TestThereIsNoKeyToJoinOn:
    def test_4_the_purchase_upload_form_has_no_item_identifier(self) -> None:
        """지출 표준 양식에 품목 식별자 칸이 없다 — 받을 자리조차 없다."""
        headers = set(header_row())
        for name in (
            "물품목록번호",
            "물품분류번호",
            "품목코드",
            "세부품명",
            "계약번호",
            "구매번호",
        ):
            assert not any(name in header for header in headers), name

    def test_5_the_company_upload_form_has_no_item_identifier(self) -> None:
        """기업정보 양식도 마찬가지다 — 명단은 업체를 담지 품목을 담지 않는다."""
        headers = {column.header for column in POLICY_SCOPED_COMPANY_COLUMNS}
        for name in ("물품목록번호", "물품분류번호", "품목", "G2B"):
            assert not any(name in header for header in headers), name

    def test_6_nothing_in_the_database_stores_an_item_identifier(self, db: Path) -> None:
        """DB 어디에도 품목 식별자를 담는 칸이 없다.

        ⛔ 담을 자리가 없다는 것은 **연결할 수 없다**는 뜻이다. 자리를 만드는
        것은 「무엇을 생산가능품목으로 볼지」가 정해진 다음의 일이다.
        """
        import sqlite3

        connection = sqlite3.connect(db)
        try:
            tables = [
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            ]
            offenders: list[str] = []
            for table in tables:
                for column in connection.execute(f"PRAGMA table_info({table})"):
                    name = str(column[1]).lower()
                    if any(word in name for word in ("g2b", "item_code", "product_code")):
                        offenders.append(f"{table}.{column[1]}")
        finally:
            connection.close()

        assert offenders == []

    def test_7_producible_items_is_not_a_calculable_scope(self) -> None:
        """⛔ 분모를 구하는 코드가 없으므로 계산 가능 목록에 들어 있지 않다."""
        assert PRODUCIBLE_ITEMS not in CALCULABLE_SCOPES


# ======================================================================
# §4-⑥ · §11 · §17-8  적요·거래처명으로 품목을 정하지 않는다
# ======================================================================
class TestNothingGuessesFromText:
    def test_8_a_description_naming_a_produced_item_changes_nothing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """적요에 생산품목명이 그대로 적혀 있어도 실적이 되지 않는다.

        ⛔ 「화학접착제」라고 적혀 있다고 생산가능품목 구매로 인정하면, 담당자가
        확인하지 않은 분류가 그대로 7% 목표의 분자가 된다.
        """
        companies = _company_file(
            tmp_path / "village.xlsx",
            [[_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code=_CODE).status_code == 200
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(
                    day="2026-03-05",
                    amount=5_000_000,
                    business_no=_VILLAGE,
                    note="화학접착제 구매",
                )
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")

        row = _policy_row(client)
        assert row["achievement_rate"] is None
        assert row["status"] == "CALCULATION_ON_HOLD"

    def test_9_the_similarity_experiments_stay_out_of_the_calculation(self) -> None:
        """``experiments/`` 의 검색 코드가 계산 경로에 연결되어 있지 않다.

        ⛔ BM25·FUSE 점수는 담당자의 확정이 아니다.
        """
        import ast

        calculation_path = (
            Path("src") / "procurement" / "calculators",
            Path("src") / "procurement" / "dashboard",
        )
        offenders: list[str] = []
        for root in calculation_path:
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                        "procurement.experiments"
                    ):
                        offenders.append(f"{path}:{node.lineno}")
                    if isinstance(node, ast.Import):
                        offenders += [
                            f"{path}:{node.lineno}"
                            for alias in node.names
                            if alias.name.startswith("procurement.experiments")
                        ]
        assert offenders == []


# ======================================================================
# §13 · §14 · §15 · §17-10·11·12  다른 것을 건드리지 않는가
# ======================================================================
class TestItTouchesNothingElse:
    def test_10_registering_the_village_leaves_other_policies_alone(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """자활용사촌 명단 등록이 다른 정책의 결과를 바꾸지 않는다."""
        before = client.get("/dashboard/summary", params={"year": 2026}).json()["policies"]

        companies = _company_file(
            tmp_path / "village.xlsx",
            [[_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code=_CODE).status_code == 200

        after = client.get("/dashboard/summary", params={"year": 2026}).json()["policies"]
        for was in before:
            if was["policy_code"] == _CODE:
                continue
            assert next(row for row in after if row["policy_code"] == was["policy_code"]) == was

    def test_11_the_monthly_accumulation_survives(self, client: TestClient, tmp_path: Path) -> None:
        """STEP 113 — 자활용사촌 명단 등록이 월별 지출 누적을 깨지 않는다."""
        january = _purchase_file(
            tmp_path / "jan.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_VILLAGE)],
        )
        assert _upload_purchases(client, january, month=1).status_code == 200

        companies = _company_file(
            tmp_path / "village.xlsx",
            [[_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code=_CODE).status_code == 200

        february = _purchase_file(
            tmp_path / "feb.xlsx",
            [_purchase_row(day="2026-02-15", amount=2_000_000, business_no=_VILLAGE)],
        )
        assert _upload_purchases(client, february, month=2).status_code == 200

        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        assert _won(payload["total_purchase_amount"]) == 3_000_000  # 1월이 살아 있다

    def test_12_the_version_structure_still_holds(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """STEP 114 — 같은 파일은 멱등, 다른 내용은 새 활성 버전."""
        policy_id = _policy_id(db, _CODE)
        sources = PolicyCompanySourceRepository(db)

        first = _company_file(
            tmp_path / "village.xlsx",
            [[_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, first, policy_code=_CODE).status_code == 200
        assert len(sources.find_versions(policy_id)) == 1

        assert _upload_companies(client, first, policy_code=_CODE).status_code == 200
        assert len(sources.find_versions(policy_id)) == 1  # 같은 파일 — 늘지 않는다

        second = _company_file(
            tmp_path / "village2.xlsx",
            [
                [_VILLAGE, "합성용사촌", "가나다", "2026-01-01", "2026-12-31"],
                [_OTHER, "합성상사", "라마바", "2026-01-01", "2026-12-31"],
            ],
        )
        assert _upload_companies(client, second, policy_code=_CODE).status_code == 200

        versions = sources.find_versions(policy_id)
        assert [row.version for row in versions] == [1, 2]
        assert [row.is_active for row in versions] == [False, True]

    def test_13_with_no_list_at_all_it_is_unavailable(self, client: TestClient) -> None:
        """명단조차 없으면 「조회불가」다 — ⛔ 0% 도 미해당도 아니다."""
        row = _policy_row(client)

        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None
        assert row["achievement_rate"] is None
