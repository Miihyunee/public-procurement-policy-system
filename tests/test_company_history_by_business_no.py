"""
STEP 64 — 거래처 과거 확정 이력을 **사업자등록번호 기준**으로 모은다.

고객이 확정한 기준입니다(2026-08-30 · `DECISIONS.md` §0.9.5 원칙 4).

    사업자등록번호가 동일하면 동일 업체로 판단한다.

이 파일이 지키는 것
===================

1. **같은 사업자번호 + 다른 표기 → 하나로 모인다.** 이전에는 표기마다 나뉘어
   실제보다 적게 보였습니다.
2. **같은 이름 + 다른 사업자번호 → 나뉜다.** 서로 다른 업체의 이력이 합쳐지면
   담당자가 잘못된 근거로 판단하게 됩니다.
3. **⭐ 이력을 모으는 것은 유형을 정하는 것이 아니다.** 과거가 공사 5건이어도
   현재 건은 미확정 그대로여야 합니다.
4. **⭐ 달성률이 달라지지 않는다.** 참고정보 조회 기준을 바꾼 것뿐입니다.

.. warning::
    ⛔ **구매유형을 자동 판정하지 않습니다.** 사업자번호로 이력을 연결하는
    것과 그 이력으로 유형을 정하는 것은 **별개의 기능**이며, 후자는 고객이
    확인해 준 적이 없습니다(§0.9.5 원칙 5 — 오히려 명시적으로 부정했습니다).

.. warning::
    ⛔ **거래처명으로 되돌아가는 fallback 을 만들지 않습니다.** 사업자번호가
    없으면 그 건은 세지 않습니다. 이름으로 대신 묶으면 고객이 정한 기준이
    조용히 무너집니다.

.. note::
    합성 데이터만 씁니다 — 실제 거래처명·사업자등록번호를 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.purchase import Purchase
from procurement.models.review import CONFIRMED, PurchaseReview
from procurement.reviews.company_labels import CompanyLabelIndex

# 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
_ONE = "1000000001"
_TWO = "2000000002"

#: 고객이 든 예와 **같은 모양**의 표기 흔들림(합성 상호로 바꿨습니다).
_SPELLINGS = (
    "합성브로드밴드주식회사",
    "합성브로드밴드(주)",
    "합성브로드밴드",
    "㈜합성브로드밴드",
)

_DAY = date(2026, 3, 2)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "company-history.db"
    init_db(path)
    return path


def _add(path: Path, name: str, business_no: str, *, amount: str = "1000") -> int:
    """구매 한 건을 넣고 ``purchase_id`` 를 돌려줍니다."""
    saved = PurchaseRepository(path).insert(
        Purchase(
            business_no=business_no,
            company_name=name,
            contract_date=_DAY,
            payment_date=_DAY,
            resolution_date=_DAY,
            amount=Decimal(amount),
            description=f"{name} 지출",
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _confirm(path: Path, purchase_id: int, purchase_type: str | None) -> None:
    ReviewRepository(path).confirm(
        purchase_id, final_purchase_type=purchase_type, reviewed_by="담당자", review_note=None
    )


def _block(path: Path, purchase_id: int) -> dict[str, Any]:
    body: Any = TestClient(create_app(path)).get(f"/reviews/{purchase_id}").json()
    block: dict[str, Any] = body["company_labels"]
    return block


def _counts(block: dict[str, Any]) -> dict[str, int]:
    return {row["label"]: row["count"] for row in block["labels"]}


# ----------------------------------------------------------------------
# 색인 계층 — 묶음 키
# ----------------------------------------------------------------------
class TestIndexKey:
    """:class:`CompanyLabelIndex` 는 사업자등록번호로 묶는다."""

    def _index(
        self, rows: list[tuple[int, str, str]], confirmed: list[tuple[int, str]]
    ) -> CompanyLabelIndex:
        purchases = [
            Purchase(
                purchase_id=pid,
                business_no=business_no,
                company_name=name,
                contract_date=_DAY,
                payment_date=_DAY,
                amount=Decimal("1000"),
            )
            for pid, name, business_no in rows
        ]
        reviews = [
            PurchaseReview(
                purchase_id=pid, review_status=CONFIRMED, final_purchase_type=purchase_type
            )
            for pid, purchase_type in confirmed
        ]
        return CompanyLabelIndex(purchases, reviews)

    def test_groups_by_business_no(self, db: Path) -> None:
        index = self._index(
            [(1, "가나상사", _ONE), (2, "가나상사(주)", _ONE)],
            [(1, CONSTRUCTION), (2, CONSTRUCTION)],
        )
        assert index.summary_for(_ONE).total == 2

    def test_length_counts_business_numbers(self, db: Path) -> None:
        """고유 개수는 **사업자번호** 수다 — 이름 수가 아니다."""
        index = self._index(
            [(1, "가나상사", _ONE), (2, "가나상사(주)", _ONE), (3, "다른회사", _TWO)],
            [(1, CONSTRUCTION), (2, CONSTRUCTION), (3, SERVICE)],
        )
        assert len(index) == 2

    def test_missing_business_no_is_not_counted(self, db: Path) -> None:
        """⛔ 사업자번호가 없으면 세지 않는다 — 이름으로 되돌아가지 않는다.

        저장된 구매는 ``business_no`` 가 ``NOT NULL`` 이라 운영에서는 나오지
        않는 상황이지만, 색인이 **이름으로 fallback 하지 않는다**는 사실을
        여기서 잠급니다.
        """
        index = self._index([(1, "가나상사", "")], [(1, CONSTRUCTION)])
        assert len(index) == 0
        assert index.summary_for("").total == 0
        assert index.summary_for("가나상사").total == 0

    def test_lookup_by_name_finds_nothing(self, db: Path) -> None:
        """⛔ 거래처명으로는 조회되지 않는다 — 키가 아니다."""
        index = self._index([(1, "가나상사", _ONE)], [(1, CONSTRUCTION)])
        assert index.summary_for(_ONE).total == 1
        assert index.summary_for("가나상사").total == 0

    def test_no_normalisation_of_the_number(self, db: Path) -> None:
        """⛔ 하이픈을 떼거나 자릿수를 보정하지 않는다 — 두 번째 규칙 금지.

        저장 시점(``normalize_business_no``)에 이미 정규화되므로, 여기서 또
        손대면 규칙이 두 곳에 생깁니다.
        """
        index = self._index([(1, "가나상사", _ONE)], [(1, CONSTRUCTION)])
        assert index.summary_for("100-00-00001").total == 0


# ----------------------------------------------------------------------
# 사례 A~D
# ----------------------------------------------------------------------
class TestCustomerCases:
    """지시 §5 의 사례 A · B · C · D."""

    def test_case_a_one_number_many_spellings(self, db: Path) -> None:
        """사례 A — 표기 네 가지가 **한 업체 이력**으로 모인다."""
        for name in _SPELLINGS:
            _confirm(db, _add(db, name, _ONE), CONSTRUCTION)
        current = _add(db, _SPELLINGS[0], _ONE)

        block = _block(db, current)
        assert block["total"] == 4
        assert _counts(block) == {"공사": 4}

    def test_case_b_same_name_different_numbers(self, db: Path) -> None:
        """사례 B — 이름이 같아도 번호가 다르면 **다른 업체**."""
        _confirm(db, _add(db, "합성통신", _ONE), CONSTRUCTION)
        _confirm(db, _add(db, "합성통신", _ONE), CONSTRUCTION)
        current = _add(db, "합성통신", _TWO)

        assert _block(db, current)["total"] == 0

    def test_case_c_renamed_company(self, db: Path) -> None:
        """사례 C — 이름이 전혀 달라져도 번호가 같으면 이력이 이어진다.

        ⛔ 상호변경을 시스템이 판정한 것이 아니다. 고객이 정한 기준을 그대로
        적용한 결과이며, 왜 이름이 달라졌는지는 사람이 확인할 일이다.
        """
        _confirm(db, _add(db, "A기업", _ONE), SERVICE)
        current = _add(db, "B기업", _ONE)

        assert _block(db, current)["total"] == 1

    def test_case_d_no_name_fallback_in_the_response(self, db: Path) -> None:
        """사례 D — 번호가 다르면 이름이 같아도 절대 합쳐지지 않는다.

        저장된 구매는 사업자번호가 필수이므로 "번호 없음" 은 운영에서 생기지
        않습니다(색인 계층은 :class:`TestIndexKey` 가 확인). 여기서는 **이름
        fallback 이 응답 경로에도 없다**는 것을 잠급니다.
        """
        for _ in range(3):
            _confirm(db, _add(db, "같은이름", _ONE), GOODS)
        current = _add(db, "같은이름", _TWO)

        block = _block(db, current)
        assert block["total"] == 0
        assert block["business_no"] == _TWO


# ----------------------------------------------------------------------
# 숫자가 실제로 합쳐지는가 (지시 §7)
# ----------------------------------------------------------------------
class TestCountsAreMerged:
    """변경 전 나뉘어 있던 N건 · M건이 **N+M** 으로 모인다."""

    def test_two_spellings_merge(self, db: Path) -> None:
        # 표기 A 로 3건, 표기 B 로 2건 확정 — 같은 사업자번호.
        for _ in range(3):
            _confirm(db, _add(db, "합성브로드밴드(주)", _ONE), SERVICE)
        for _ in range(2):
            _confirm(db, _add(db, "합성브로드밴드주식회사", _ONE), SERVICE)
        current = _add(db, "합성브로드밴드", _ONE)

        # 예전 기준이었다면 표기별로 3건 / 2건 / 0건으로 나뉘었을 자리다.
        assert _block(db, current)["total"] == 5

    def test_merged_types_are_kept_apart(self, db: Path) -> None:
        """합쳐도 **유형별 건수는 그대로** 유지된다 — 라벨을 바꾸지 않는다."""
        _confirm(db, _add(db, "합성브로드밴드(주)", _ONE), CONSTRUCTION)
        _confirm(db, _add(db, "합성브로드밴드주식회사", _ONE), SERVICE)
        _confirm(db, _add(db, "합성브로드밴드", _ONE), SERVICE)
        current = _add(db, "㈜합성브로드밴드", _ONE)

        block = _block(db, current)
        assert _counts(block) == {"용역": 2, "공사": 1}
        assert block["type_count"] == 2
        assert block["has_conflict"] is True

    def test_other_company_is_not_pulled_in(self, db: Path) -> None:
        """합칠 때 남의 이력이 딸려 오지 않는다."""
        for _ in range(4):
            _confirm(db, _add(db, "합성브로드밴드(주)", _ONE), SERVICE)
        for _ in range(9):
            _confirm(db, _add(db, "합성브로드밴드(주)", _TWO), CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)

        assert _counts(_block(db, current)) == {"용역": 4}


# ----------------------------------------------------------------------
# 확정 라벨 보존 · 응답 형식
# ----------------------------------------------------------------------
class TestLabelsPreserved:
    """⛔ 기존 확정 라벨을 바꾸지 않는다."""

    def test_confirmed_values_unchanged(self, db: Path) -> None:
        first = _add(db, "합성브로드밴드(주)", _ONE)
        _confirm(db, first, CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)

        client = TestClient(create_app(db))
        client.get(f"/reviews/{current}")  # 이력 조회

        body: Any = client.get(f"/reviews/{first}").json()
        assert body["review"]["final_purchase_type"] == CONSTRUCTION
        assert body["review"]["status"] == CONFIRMED

    def test_reading_writes_nothing(self, db: Path) -> None:
        """조회가 구매 데이터를 바꾸지 않는다."""
        _confirm(db, _add(db, "합성브로드밴드(주)", _ONE), CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)
        before = PurchaseRepository(db).find_all()

        TestClient(create_app(db)).get(f"/reviews/{current}")

        after = PurchaseRepository(db).find_all()
        assert [(row.purchase_id, row.business_no, row.company_name) for row in after] == [
            (row.purchase_id, row.business_no, row.company_name) for row in before
        ]


class TestResponseShape:
    """응답 형식 — 기존 필드는 그대로, 기준을 밝히는 필드만 늘었다."""

    def test_existing_fields_kept(self, db: Path) -> None:
        current = _add(db, "합성브로드밴드", _ONE)
        block = _block(db, current)
        for field in (
            "company_name",
            "labels",
            "total",
            "type_count",
            "has_conflict",
            "consistency",
        ):
            assert field in block, field

    def test_reports_what_it_counted_by(self, db: Path) -> None:
        current = _add(db, "합성브로드밴드", _ONE)
        assert _block(db, current)["business_no"] == _ONE

    def test_company_name_is_this_rows_name(self, db: Path) -> None:
        """거래처명은 **표시용** — 이 건의 이름을 그대로 싣는다."""
        _confirm(db, _add(db, "옛이름", _ONE), SERVICE)
        current = _add(db, "새이름", _ONE)

        block = _block(db, current)
        assert block["company_name"] == "새이름"
        assert block["total"] == 1  # 이름이 달라도 이력은 이어진다

    def test_list_endpoint_uses_the_same_key(self, db: Path) -> None:
        """목록에도 같은 기준이 적용된다."""
        _confirm(db, _add(db, "합성브로드밴드(주)", _ONE), SERVICE)
        _add(db, "합성브로드밴드", _ONE)

        body: Any = TestClient(create_app(db)).get("/reviews").json()
        for item in body["items"]:
            assert item["company_labels"]["business_no"] == _ONE
            assert item["company_labels"]["total"] == 1


# ----------------------------------------------------------------------
# ⭐ 자동분류와의 경계
# ----------------------------------------------------------------------
class TestHistoryIsNotAVerdict:
    """⭐ **사업자번호 기반 이력 연결 ≠ 구매유형 자동판정.**"""

    def test_construction_history_does_not_confirm_the_current_row(self, db: Path) -> None:
        """과거 공사 이력이 있어도 현재 건은 **미확정** 그대로다."""
        for name in _SPELLINGS:
            _confirm(db, _add(db, name, _ONE), CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()
        assert body["company_labels"]["total"] == 4
        assert body["company_labels"]["consistency"] == "SINGLE_TYPE"
        # ⛔ 그래도 확정되지 않았다.
        assert body["review"]["final_purchase_type"] is None
        assert body["review"]["status"] == "PENDING"

    def test_merging_does_not_add_verdict_fields(self, db: Path) -> None:
        """⛔ 이력이 커져도 판정 의미의 필드가 생기지 않는다."""
        for name in _SPELLINGS:
            _confirm(db, _add(db, name, _ONE), CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)

        block = _block(db, current)
        for banned in (
            "score",
            "confidence",
            "rank",
            "recommended_type",
            "predicted_type",
            "dominant_type",
            "dominant_ratio",
            "is_construction_company",
        ):
            assert banned not in block, banned

    def test_the_reviewer_choice_wins(self, db: Path) -> None:
        """담당자가 과거와 다른 유형을 골라도 그대로 확정된다."""
        for name in _SPELLINGS:
            _confirm(db, _add(db, name, _ONE), CONSTRUCTION)
        current = _add(db, "합성브로드밴드", _ONE)
        client = TestClient(create_app(db))

        client.put(
            f"/reviews/{current}", json={"final_purchase_type": GOODS, "reviewed_by": "담당자"}
        )

        body: Any = client.get(f"/reviews/{current}").json()
        assert body["review"]["final_purchase_type"] == GOODS

    def test_company_name_keyword_is_not_used(self, db: Path) -> None:
        """⛔ 상호에 `건설` 이 들어가도 아무 일도 일어나지 않는다(§0.9.5 원칙 5)."""
        current = _add(db, "합성건설안전기술(주)", _ONE)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()
        assert body["company_labels"]["total"] == 0
        assert body["review"]["final_purchase_type"] is None


# ----------------------------------------------------------------------
# ⭐ 달성률 불변
# ----------------------------------------------------------------------
class TestAchievementUnchanged:
    """⭐ 참고정보 조회 기준을 바꾼 것뿐 — 계산은 그대로다."""

    @pytest.fixture
    def seeded(self, tmp_path: Path) -> Path:
        path = tmp_path / "achievement.db"
        init_db(path)
        seed_policies(path)
        # 표기가 갈린 같은 업체 + 다른 업체.
        for name in _SPELLINGS:
            _confirm(path, _add(path, name, _ONE, amount="1000"), CONSTRUCTION)
        _add(path, "다른회사", _TWO, amount="500")
        return path

    def test_summary_identical_before_and_after_history_lookup(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded, period_date_field="resolution_date"))
        before = client.get("/dashboard/summary?year=2026").json()

        client.get("/reviews")  # 이력 색인을 만들게 한다

        after = client.get("/dashboard/summary?year=2026").json()
        assert after == before

    def test_denominator_is_not_affected_by_grouping(self, seeded: Path) -> None:
        """분모는 구매 행 합계 그대로 — 업체를 어떻게 묶든 달라지지 않는다."""
        client = TestClient(create_app(seeded, period_date_field="resolution_date"))
        body = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(body["total_purchase_amount"]) == Decimal("4500")

    def test_policy_numbers_unchanged(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded, period_date_field="resolution_date"))
        before = [
            (p["policy_code"], p["purchase_amount"], p["achievement_rate"])
            for p in client.get("/dashboard/summary?year=2026").json()["policies"]
        ]
        client.get("/reviews")
        after = [
            (p["policy_code"], p["purchase_amount"], p["achievement_rate"])
            for p in client.get("/dashboard/summary?year=2026").json()["policies"]
        ]
        assert after == before


# ----------------------------------------------------------------------
# 화면
# ----------------------------------------------------------------------
class TestScreen:
    """화면이 **무엇을 기준으로 셌는지** 밝힌다."""

    @pytest.fixture
    def page(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "procurement"
            / "web"
            / "static"
            / "index.html"
        )
        return path.read_text(encoding="utf-8")

    def _body(self, page: str) -> str:
        start = page.index("function companyHistory")
        return page[start : page.index("\n  }", start)]

    def test_says_business_no_is_the_basis(self, page: str) -> None:
        assert "사업자등록번호가 같은 건을 모아 셉니다" in self._body(page)

    def test_no_longer_claims_name_matching(self, page: str) -> None:
        """⛔ 낡은 설명("거래처명이 정확히 같은 건만")이 남아 있지 않다."""
        assert "거래처명이 정확히 같은 건만" not in page

    def test_still_says_it_is_not_a_verdict(self, page: str) -> None:
        body = self._body(page)
        assert "자동 판정 아님" in body
        assert "현재 구매유형을 정하지 않습니다" in body

    def test_no_company_verdict_wording(self, page: str) -> None:
        """⛔ "이 업체는 공사업체입니다" 같은 표현을 쓰지 않는다."""
        body = self._body(page)
        for banned in ("공사업체", "용역업체", "물품업체"):
            assert banned not in body, banned
