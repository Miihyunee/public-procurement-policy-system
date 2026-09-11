"""
STEP 140 — 분모가 덜 찼으면 달성률을 내지 않는다.

무엇이 문제였나
===============
유형별 달성률의 분모는 「기관 전체의 그 유형 구매금액」이다. 그런데 분모에
들어가는 것은 담당자가 **확정한** 행뿐이다.

여성기업 124건만 확정된 상태에서 이렇게 나왔다.

```
공사  분모 154,791,000 · 분자 154,791,000 → 3333.33%  상태 「정상」
용역  분모 986,818,690 · 분자 986,818,690 → 2000.00%  상태 「정상」
물품  분모  83,567,754 · 분자  83,567,754 → 2000.00%  상태 「정상」
```

분모와 분자가 **같은 124건**이었다. 확정된 것이 여성기업 건뿐이라 분모에
그 124건 말고는 들어갈 것이 없었다. 담당자는 이 화면을 그대로 대외 보고에
쓸 수 있었다.

부분 분모는 크기와 무관하게 늘 달성률을 실제보다 **높게** 만든다.

무엇을 바꿨나
=============
달성률을 내기 전에 **분모가 다 채워졌는지** 먼저 묻는다. 한 건이라도 유형이
정해지지 않았으면 그만큼 분모가 비어 있다는 뜻이므로 «계산 보류» 로 둔다.

⛔ 「몇 % 이상이면 계산한다」는 기준을 만들지 않았다. 임계값을 고르는 것은
   고객 확인 사항이다(지시서 §6).
⛔ 목표율·계산 공식·분모 산정 범위·자동분류 규칙을 바꾸지 않았다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.purchase_repository import PurchaseRepository

#: ⛔ 실제 고객 자료가 아니다. 검증자릿수를 맞춘 합성 사업자번호.
WOMAN_NO = "1000000009"
OTHER_NO = "1000000014"

YEAR = 2026


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _purchases(tmp_path: Path) -> str:
    """여성기업 인증기업 1건 + 그 밖의 기업 1건."""
    book = Workbook()
    sheet = book.active
    sheet.append(
        [
            "결의일자",
            "계약일자",
            "지급일",
            "기업명",
            "사업자등록번호",
            "계",
            "신고기준일",
            "적요",
            "예산과목",
        ]
    )
    sheet.append(["2026-03-01", None, None, "합성1기업", WOMAN_NO, 1000000, "2026-03-01", "", ""])
    sheet.append(["2026-03-02", None, None, "합성2기업", OTHER_NO, 9000000, "2026-03-02", "", ""])
    path = tmp_path / "purchases.xlsx"
    book.save(path)
    return str(path)


def _woman_listing(tmp_path: Path) -> str:
    book = Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    sheet.append([WOMAN_NO, "합성1기업", "", "2026-01-01", "2026-12-31"])
    path = tmp_path / "woman.xlsx"
    book.save(path)
    return str(path)


@pytest.fixture
def loaded(client: TestClient, db: Path, tmp_path: Path) -> TestClient:
    """구매 2건 · 여성기업 인증 1건 · 목표비율 등록까지 마친 상태."""
    upload = client.post(
        "/uploads/purchases", json={"file_path": _purchases(tmp_path), "year": YEAR}
    )
    assert upload.status_code == 200, upload.text
    for policy_code in ("WOMAN", "SMALL_BUSINESS"):
        # 중소기업도 함께 등록한다 — 유형별 목표가 없는 정책이 예전처럼
        # 달성률을 내는지 비교하려면 비교 대상이 실제로 계산돼야 한다.
        listing = client.post(
            "/companies/upload",
            json={"file_path": _woman_listing(tmp_path), "policy_code": policy_code},
        )
        assert listing.status_code == 200, listing.text
    client.post("/purchases/rematch")
    main(["targets", "--year", str(YEAR), "--db", str(db)])
    return client


def _woman(client: TestClient) -> dict[str, object]:
    body = client.get("/dashboard/summary", params={"year": YEAR}).json()
    for item in body.get("items") or body.get("policies") or []:
        if item["policy_code"] == "WOMAN":
            return dict(item)
    raise AssertionError("여성기업 요약이 없다")


def _ids(client: TestClient) -> dict[str, int]:
    """사업자번호 → purchase_id."""
    body = client.get("/reviews", params={"page": 1, "page_size": 50}).json()
    return {
        item["source"]["business_no"]: item["source"]["purchase_id"] for item in body["items"]
    }


def _confirm(client: TestClient, purchase_id: int, purchase_type: str) -> None:
    response = client.put(
        f"/reviews/{purchase_id}",
        json={"final_purchase_type": purchase_type, "reviewed_by": "시험"},
    )
    assert response.status_code == 200, response.text


def _scoped(item: dict[str, object]) -> dict[str, dict[str, object]]:
    return {row["scope"]: row for row in item["scoped_achievements"]}  # type: ignore[index,union-attr]


# ======================================================================
# §12 CASE 1 ~ 5
# ======================================================================
class TestTheRateIsHeldUntilTheDenominatorIsFull:
    def test_case_1_nothing_confirmed_is_on_hold(self, loaded: TestClient) -> None:
        """CASE 1 — 구매유형이 하나도 확정되지 않았다."""
        item = _woman(loaded)

        assert item["status"] == "SCOPED_BY_PURCHASE_TYPE"
        for scope, row in _scoped(item).items():
            assert row["achievement_rate"] is None, scope
            assert row["status"] == "CALCULATION_ON_HOLD", scope

        coverage = item["purchase_type_coverage"]
        assert coverage == {
            "confirmed_count": 0,
            "total_count": 2,
            "confirmed_amount": "0",
            "total_amount": "10000000",
            "complete": False,
        }

    def test_case_2_partly_confirmed_is_still_on_hold(self, loaded: TestClient) -> None:
        """CASE 2 — ⭐ 여성기업 건만 확정된 상태. 여기서 3333% 가 나왔었다."""
        ids = _ids(loaded)
        _confirm(loaded, ids[WOMAN_NO], "GOODS")

        item = _woman(loaded)
        goods = _scoped(item)["GOODS"]

        # 분모와 분자가 같아졌다 — 확정된 것이 이 한 건뿐이기 때문이다.
        assert goods["purchase_amount"] == goods["total_purchase_amount"] == "1000000"
        # ⭐ 그래도 달성률을 만들지 않는다.
        assert goods["achievement_rate"] is None
        assert goods["status"] == "CALCULATION_ON_HOLD"

        coverage = item["purchase_type_coverage"]
        assert coverage["confirmed_count"] == 1  # type: ignore[index]
        assert coverage["total_count"] == 2  # type: ignore[index]
        assert coverage["complete"] is False  # type: ignore[index]

    def test_case_3_fully_confirmed_calculates_as_before(self, loaded: TestClient) -> None:
        """CASE 3 — 전부 확정되면 **기존 계산이 그대로** 돈다."""
        ids = _ids(loaded)
        _confirm(loaded, ids[WOMAN_NO], "GOODS")
        _confirm(loaded, ids[OTHER_NO], "GOODS")

        item = _woman(loaded)
        goods = _scoped(item)["GOODS"]

        # 분모는 이제 기관 전체의 물품 구매금액이다.
        assert goods["total_purchase_amount"] == "10000000"
        assert goods["purchase_amount"] == "1000000"
        # 구매비율 10% ÷ 목표 5% = 200%
        assert goods["achievement_rate"] == "200.00"
        assert goods["status"] == "NORMAL"
        assert item["purchase_type_coverage"]["complete"] is True  # type: ignore[index]

    def test_case_4_a_rate_that_was_shown_disappears_again(self, loaded: TestClient) -> None:
        """CASE 4 — ⭐ 한 번 나왔던 달성률이 **남아 있지 않다.**

        전부 확정해 달성률이 나온 뒤, 한 건을 판단 보류로 되돌리면 분모가
        다시 덜 찬 상태가 된다. 그때 이전 달성률이 그대로 보이면 담당자는
        옛 숫자를 지금 숫자로 읽는다.
        """
        ids = _ids(loaded)
        _confirm(loaded, ids[WOMAN_NO], "GOODS")
        _confirm(loaded, ids[OTHER_NO], "GOODS")
        before = _scoped(_woman(loaded))["GOODS"]
        assert before["achievement_rate"] == "200.00", "먼저 달성률이 나와야 시험이 성립한다"

        # 「판단 보류」로 되돌린다 — 확정을 지우는 것이 아니라 비우는 것이다.
        _confirm(loaded, ids[OTHER_NO], None)  # type: ignore[arg-type]

        after = _scoped(_woman(loaded))["GOODS"]
        assert after["achievement_rate"] is None
        assert after["status"] == "CALCULATION_ON_HOLD"

    def test_case_5_other_policies_are_untouched(self, loaded: TestClient) -> None:
        """CASE 5 — ⛔ 유형별 목표가 없는 정책은 **영향을 받지 않는다.**

        구매유형이 하나도 확정되지 않은 상태에서도, 전체 구매금액을 분모로
        쓰는 정책들은 예전처럼 달성률을 낸다.
        """
        body = loaded.get("/dashboard/summary", params={"year": YEAR}).json()
        others = [
            item
            for item in (body.get("items") or body.get("policies") or [])
            if item["policy_code"] != "WOMAN"
        ]
        assert others, "비교할 다른 정책이 없다"

        for item in others:
            assert item["purchase_type_coverage"] is None, item["policy_code"]
            assert item["scoped_achievements"] == [], item["policy_code"]

        # 적어도 하나는 실제로 달성률이 나와야 한다 — 아니면 아무것도 보지 못한다.
        assert any(item["achievement_rate"] is not None for item in others)


# ======================================================================
# §12 CASE 6 — 확정한 값 자체는 건드리지 않는다
# ======================================================================
class TestConfirmedDataIsNotTouched:
    def test_case_6_confirmations_survive_the_hold(self, loaded: TestClient) -> None:
        """CASE 6 — 계산이 보류돼도 담당자가 확정한 값은 그대로다."""
        ids = _ids(loaded)
        _confirm(loaded, ids[WOMAN_NO], "SERVICE")

        item = _woman(loaded)
        assert _scoped(item)["SERVICE"]["status"] == "CALCULATION_ON_HOLD"

        # 확정값은 살아 있다.
        review = loaded.get(f"/reviews/{ids[WOMAN_NO]}").json()["review"]
        assert review["status"] == "CONFIRMED"
        assert review["final_purchase_type"] == "SERVICE"


# ======================================================================
# 세는 방법 자체
# ======================================================================
class TestCountingTheCoverage:
    def test_it_counts_the_same_population_the_calculation_uses(
        self, loaded: TestClient, db: Path
    ) -> None:
        """⭐ 계산 모집단과 **같은 조건**으로 센다.

        다른 모집단을 세면 「분모는 다 찼는데 화면은 덜 찼다고 한다」는
        어긋남이 생긴다.
        """
        from procurement.core.period import PeriodFilter

        repository = PurchaseRepository(db)
        period = PeriodFilter.for_year(YEAR, "resolution_date")
        confirmed, total, confirmed_amount, total_amount = repository.count_purchase_type_coverage(
            period
        )

        assert (confirmed, total) == (0, 2)
        assert confirmed_amount == Decimal("0")
        assert total_amount == Decimal("10000000")
        # 계산 대상 건수와 정확히 같아야 한다.
        assert total == len(repository.find_for_calculation(period))

    def test_an_empty_period_is_complete(self, db: Path) -> None:
        """대상이 하나도 없으면 채울 것이 없다 — 그때는 분모 0 으로 보류된다."""
        from procurement.core.period import PeriodFilter

        repository = PurchaseRepository(db)
        confirmed, total, _c, _t = repository.count_purchase_type_coverage(
            PeriodFilter.for_year(1999, "resolution_date")
        )

        assert (confirmed, total) == (0, 0)


# ======================================================================
# ⛔ 임계값을 만들지 않았다
# ======================================================================
class TestNoThresholdWasInvented:
    def test_no_percentage_threshold_exists_in_the_code(self) -> None:
        """⛔ 「몇 % 이상이면 계산한다」는 숫자가 코드에 없다(지시서 §6).

        ⚠️ 글자로 찾지 않는다. 설명 문장의 «§0.20» 같은 것에 걸리기 때문이다.
        코드가 실제로 쓰는 **상수**만 본다.
        """
        import ast

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for relative in ("dashboard/data_service.py", "dashboard/models.py"):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    continue
                # 0 과 1 사이의 상수는 «비율 임계값» 이다. 반올림 자리수는 문자열로
                # 적혀 있어 여기 걸리지 않는다.
                assert not (0 < float(node.value) < 1), (
                    f"{relative} 에 비율 상수 {node.value} 가 있다"
                )

    def test_completeness_is_a_comparison_not_a_ratio(self) -> None:
        """다 찼는가는 **건수 비교**다 — 비율을 계산하지 않는다."""
        from procurement.dashboard.models import PurchaseTypeCoverage

        full = PurchaseTypeCoverage(
            confirmed_count=2,
            total_count=2,
            confirmed_amount=Decimal("1"),
            total_amount=Decimal("1"),
        )
        one_short = PurchaseTypeCoverage(
            confirmed_count=1999,
            total_count=2000,
            confirmed_amount=Decimal("1"),
            total_amount=Decimal("1"),
        )

        assert full.complete
        # ⭐ 99.95% 라도 «다 찼다» 가 아니다. 한 건이 비면 분모가 그만큼 빈다.
        assert not one_short.complete


# ======================================================================
# 운영 조립부가 실제로 셀 수 있게 되어 있는가
# ======================================================================
class TestTheProductionWiringCanActuallyCount:
    def test_the_dashboard_service_gets_a_purchase_repository(self, loaded: TestClient) -> None:
        """⭐ ``app.py`` 가 저장소를 넣어 주지 않으면 이 장치가 조용히 꺼진다.

        서비스는 저장소 없이도 만들 수 있어(하위호환) 셀 수 없으면 예전처럼
        동작한다. 운영에서 그 경로를 타지 않는다는 것을 여기서 붙잡아 둔다.
        """
        assert _woman(loaded)["purchase_type_coverage"] is not None
