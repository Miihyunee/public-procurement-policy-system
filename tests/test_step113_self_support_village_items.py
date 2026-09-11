"""
자활용사촌 — **생산가능품목을 구매와 이을 근거가 없다**는 사실을 잠급니다.

자활용사촌만 계산식이 다릅니다.

    자활용사촌 **생산가능품목 총 구매액**의 7% 이상

즉 분모가 기관 전체 구매금액이 아니라 «자활용사촌이 만들 수 있는 품목의
구매액» 입니다. 그 분모를 구하려면 **거래마다 무슨 품목인지** 알아야 하는데,
지금 그 정보가 어디에도 없습니다.

무엇을 지키는가 (지시서 §5 · §6 · §7 · §12 · §16)
=================================================

1. 구매 데이터에 **품목 식별자가 없다** — 저장 자리부터 없다.
2. 품목을 잇는 **매핑 테이블도 없다** (CASE C).
3. 그래서 목표 7% 는 저장돼 있으나 달성률은 **«계산 보류»** 다.
4. ⛔ 자활용사촌 기업의 **전체 거래액을 실적으로 쓰지 않는다.**
5. ⛔ **적요 문자열로 품목을 판정하지 않는다** — 그런 코드가 없다.
6. ⛔ 분모가 없다고 **기관 전체 구매금액으로 대신하지 않는다.**

.. warning::
    ⛔ 어떤 기업이 A·B·C 를 생산할 수 있다는 것과, 그 기업에서 산 물건이
    A·B·C 였다는 것은 **다른 사실**입니다. 사업자번호만으로 품목을 단정하면
    아무도 확인하지 않은 금액이 분모와 분자에 동시에 들어갑니다.

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
from procurement.core.target_scope import PRODUCIBLE_ITEMS, is_calculable
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값입니다.
_VILLAGE = "1000000009"

#: 정책 코드 — ⛔ 추측하지 않고 현재 등록값을 씁니다.
_CODE = "SELF_SUPPORT_VILLAGE"

#: 소스 전체를 훑을 때 쓰는 뿌리.
_SRC = Path(__file__).resolve().parents[1] / "src" / "procurement"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "village.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _row(client: TestClient) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    return dict(next(r for r in payload["policies"] if r["policy_code"] == _CODE))


class TestThePolicyIsWiredButTheDenominatorIsNot:
    """§14 — 목표 7% 는 저장돼 있다. 그런데 분모를 구할 수 없다."""

    def test_the_policy_exists_with_the_resolution_date_rule(self, db: Path) -> None:
        policy = PolicyRepository(db).find_by_policy_code(_CODE)
        assert policy is not None
        assert policy.policy_name == "자활용사촌"
        assert policy.evaluation_basis == "RESOLUTION_DATE"

    def test_the_target_is_seven_percent_of_producible_items(self, client: TestClient) -> None:
        """⭐ 분모가 **기관 전체 구매금액이 아니다.**"""
        items = client.get("/policy-targets", params={"year": 2026}).json()["items"]
        row = next(i for i in items if i["policy_code"] == _CODE)
        scoped = row["scoped_targets"]
        assert len(scoped) == 1
        assert scoped[0]["scope"] == PRODUCIBLE_ITEMS
        assert Decimal(scoped[0]["target_rate"]) == Decimal("7")
        assert scoped[0]["calculable"] is False
        # ⛔ 총 구매금액 기준 목표가 아니다 — 그 칸은 비어 있어야 한다.
        assert row["target_rate"] is None

    def test_the_producible_items_scope_is_not_calculable(self) -> None:
        assert is_calculable(PRODUCIBLE_ITEMS) is False


class TestThePurchaseSideHasNoItemIdentifier:
    """§5 · §12 — CASE C. 이을 근거가 **구조적으로** 없다."""

    def test_the_purchase_table_has_no_item_columns(self, db: Path) -> None:
        """저장할 자리부터 없다 — ⛔ 만들지도 않았다."""
        rows = PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        columns = {row["name"] for row in rows}
        for absent in (
            "item_code",
            "item_number",
            "g2b_code",
            "g2b_item_number",
            "product_code",
            "item_category",
            "detail_item_name",
        ):
            assert absent not in columns, absent
        # 있는 것은 이것뿐이다 — 적요는 사람이 읽는 글이지 품목 코드가 아니다.
        assert "description" in columns

    def test_there_is_no_item_mapping_table(self, db: Path) -> None:
        """§12 CASE B 가 아니다 — 기존 품목 매핑 구조가 없다."""
        rows = PurchaseRepository(db).execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row["name"] for row in rows}
        for absent in ("item", "items", "product", "g2b_item", "item_category", "producible_item"):
            assert absent not in names, absent


class TestNothingGuessesTheItem:
    """§6 · §7 · §16 — ⛔ 없는 근거를 만들어 내지 않는다."""

    #: 계산이 실제로 지나가는 곳. 품목 추측이 들어온다면 여기로 들어온다.
    CALCULATION_PATH = ("calculators", "dashboard")

    def test_the_calculation_path_never_mentions_items(self) -> None:
        """⛔ 계산 경로에 품목·G2B·생산가능품목을 다루는 코드가 없다."""
        for package in self.CALCULATION_PATH:
            for path in (_SRC / package).rglob("*.py"):
                source = path.read_text(encoding="utf-8").lower()
                for term in ("g2b", "물품목록번호", "물품분류", "세부품명", "item_code"):
                    assert term not in source, (package, path.name, term)

    def test_the_calculation_path_never_reads_the_description(self) -> None:
        """⛔ 적요로 품목을 판정하지 않는다 — 계산기가 적요를 보지 않는다.

        적요는 담당자가 읽는 글입니다. 그 글자로 «생산가능품목이다» 를
        정하면 아무도 확인하지 않은 금액이 분모와 분자에 동시에 들어갑니다.
        """
        for path in (_SRC / "calculators").rglob("*.py"):
            assert "description" not in path.read_text(encoding="utf-8"), path.name

    def test_the_similarity_experiments_are_not_wired_into_the_calculation(self) -> None:
        """BM25·FUSE 는 **구매유형(공사·용역·물품) 후보 생성 실험**용이며,
        품목 매칭에 쓰이지 않는다.

        .. note::
            ``src/procurement/experiments/`` 에 BM25 · FUSE 가 **이미**
            있습니다(이전 STEP 의 구매유형 분류 실험). 지시서가 금지한 것은
            그것들을 **생산가능품목 매칭에 끌어다 쓰는 일**이므로, 없는지가
            아니라 **계산 경로가 그것들을 부르지 않는지**를 봅니다.
        """
        for package in self.CALCULATION_PATH:
            for path in (_SRC / package).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                assert "experiments" not in source, (package, path.name)


class TestTheScreenSaysItCannotBeCalculated:
    """§13 — ⛔ 0% 로 떨어뜨리지 않는다."""

    def test_with_no_company_list_it_is_unavailable(self, client: TestClient, db: Path) -> None:
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_VILLAGE,
                company_name="합성업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("1000"),
            )
        )
        row = _row(client)
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None
        assert row["achievement_rate"] is None  # ⛔ 0% 가 아니다

    def test_the_whole_institution_total_is_not_used_as_the_denominator(
        self, client: TestClient, db: Path
    ) -> None:
        """⭐ §16 의 핵심 — 분모를 기관 전체 구매금액으로 대신하지 않는다.

        대신했다면 달성률이 나왔을 것입니다. 나오지 않는 것이 맞습니다.
        """
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_VILLAGE,
                company_name="합성업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("1000"),
            )
        )
        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        assert payload["total_purchase_amount"] == "1000"  # 분모는 있다
        assert _row(client)["achievement_rate"] is None  # 그런데 쓰지 않는다
