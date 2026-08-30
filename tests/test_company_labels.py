"""STEP 41 · 64 — 같은 업체의 과거 확정 이력.

.. note::
    **묶는 키가 STEP 64 에서 거래처명 → 사업자등록번호로 바뀌었습니다**
    (2026-08-30 고객 확정 · `DECISIONS.md` §0.9.5 원칙 4). 집계 규칙·모집단·
    "판정하지 않는다" 는 원칙은 그대로입니다.


고객이 *"실제 계약했던 업체명을 검색해서 공사 여부를 판단하기도 한다"* 고
답했습니다(2026-08-25). 담당자가 지금 머릿속이나 다른 파일에서 하는 그 일을
화면으로 옮긴 것입니다.

.. warning::
    🔴 **구매유형을 판정하지 않습니다.**

    과거 기록을 세어 보여줄 뿐이며 "이 업체는 공사업체다" 같은 결론을 말하지
    않습니다. 상호에 `건설` · `토건` 이 들어가면 공사, 같은 규칙도 **없습니다**
    — 고객이 확인해 준 적이 없습니다.

.. note::
    집계 기준은 :mod:`~procurement.reviews.past_labels` 와 **똑같고** 묶는 키만
    사업자등록번호입니다. 두 블록이 다른 기준으로 세면 화면의 숫자가 서로
    어긋납니다.
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
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.purchase import Purchase
from procurement.reviews.past_labels import MIXED_TYPES, NO_HISTORY, SINGLE_TYPE

#: 합성 데이터 — 실제 거래처명·사업자번호를 쓰지 않습니다.
_ALPHA = "합성기업 가"
_BETA = "합성기업 나"

#: 합성 사업자등록번호 두 개. 묶음 키가 사업자번호이므로(STEP 64) 어느 번호로
#: 넣었는지가 곧 "같은 업체인가" 가 됩니다.
_ONE = "1000000001"
_TWO = "2000000002"

_DAY = date(2026, 3, 2)


def _add(path: Path, name: str, *, business_no: str = "1000000001") -> int:
    """구매 한 건을 넣고 purchase_id 를 돌려줍니다."""
    saved = PurchaseRepository(path).insert(
        Purchase(
            business_no=business_no,
            company_name=name,
            contract_date=_DAY,
            payment_date=_DAY,
            amount=Decimal("1000"),
            description=f"{name} 지출",
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _confirm(path: Path, purchase_id: int, purchase_type: str | None) -> None:
    """담당자 확정. ``None`` 이면 판단 보류입니다."""
    ReviewRepository(path).confirm(
        purchase_id, final_purchase_type=purchase_type, reviewed_by="담당자", review_note=None
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "company-labels.db"
    init_db(path)
    return path


def _labels(path: Path, purchase_id: int) -> dict[str, Any]:
    """단건 조회 응답의 거래처 이력 블록."""
    body: Any = TestClient(create_app(path)).get(f"/reviews/{purchase_id}").json()
    block: dict[str, Any] = body["company_labels"]
    return block


def _counts(block: dict[str, Any]) -> dict[str, int]:
    return {row["label"]: row["count"] for row in block["labels"]}


class TestNoHistory:
    """지시 §11-① — 과거 확정 이력이 없는 거래처."""

    def test_empty_history(self, db: Path) -> None:
        purchase_id = _add(db, _ALPHA)

        block = _labels(db, purchase_id)

        assert block["labels"] == []
        assert block["total"] == 0
        assert block["type_count"] == 0
        assert block["has_conflict"] is False
        assert block["consistency"] == NO_HISTORY

    def test_the_company_name_is_still_reported(self, db: Path) -> None:
        """무엇을 기준으로 셌는지는 이력이 없어도 알려준다."""
        purchase_id = _add(db, _ALPHA)

        assert _labels(db, purchase_id)["company_name"] == _ALPHA


class TestSingleType:
    """지시 §11-②③④ — 한 유형만 과거 확정."""

    @pytest.mark.parametrize(
        ("purchase_type", "label"),
        [(CONSTRUCTION, "공사"), (SERVICE, "용역"), (GOODS, "물품")],
    )
    def test_only_that_type_is_counted(self, db: Path, purchase_type: str, label: str) -> None:
        for _ in range(3):
            _confirm(db, _add(db, _ALPHA), purchase_type)
        current = _add(db, _ALPHA)

        block = _labels(db, current)

        assert _counts(block) == {label: 3}
        assert block["total"] == 3
        assert block["type_count"] == 1
        assert block["has_conflict"] is False
        assert block["consistency"] == SINGLE_TYPE

    def test_a_single_type_is_not_a_verdict(self, db: Path) -> None:
        """⛔ 과거가 한 유형뿐이어도 현재 건을 그 유형으로 정하지 않는다."""
        for _ in range(5):
            _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()

        assert body["company_labels"]["consistency"] == SINGLE_TYPE
        # 현재 건은 여전히 미확정이다
        assert body["review"]["final_purchase_type"] is None
        assert body["review"]["status"] == "PENDING"


class TestMultipleTypes:
    """지시 §11-⑤⑥ — 여러 유형으로 갈린 이력."""

    def test_two_types(self, db: Path) -> None:
        for _ in range(3):
            _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        for _ in range(2):
            _confirm(db, _add(db, _ALPHA), SERVICE)
        current = _add(db, _ALPHA)

        block = _labels(db, current)

        assert _counts(block) == {"공사": 3, "용역": 2}
        assert block["total"] == 5
        assert block["type_count"] == 2
        assert block["has_conflict"] is True
        assert block["consistency"] == MIXED_TYPES

    def test_three_types(self, db: Path) -> None:
        _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        for _ in range(2):
            _confirm(db, _add(db, _ALPHA), SERVICE)
        for _ in range(4):
            _confirm(db, _add(db, _ALPHA), GOODS)
        current = _add(db, _ALPHA)

        block = _labels(db, current)

        assert _counts(block) == {"물품": 4, "용역": 2, "공사": 1}
        assert block["total"] == 7
        assert block["type_count"] == 3


class TestOnlyConfirmedCounts:
    """지시 §11-⑦ · §10 — 확정 기준은 적요 이력과 같다."""

    def test_pending_is_not_counted(self, db: Path) -> None:
        _add(db, _ALPHA)  # ⛔ 확정하지 않음
        _add(db, _ALPHA)
        current = _add(db, _ALPHA)

        assert _labels(db, current)["total"] == 0

    def test_undecided_confirmation_is_not_counted(self, db: Path) -> None:
        """판단 보류(``None``)는 사람이 결론을 내지 않은 것이라 세지 않는다."""
        _confirm(db, _add(db, _ALPHA), None)
        _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)

        block = _labels(db, current)

        assert block["total"] == 1
        assert _counts(block) == {"공사": 1}

    def test_reopened_is_not_counted(self, db: Path) -> None:
        """확정을 되돌리면 이력에서 빠진다 — 적요 이력과 같은 규칙."""
        reopened = _add(db, _ALPHA)
        _confirm(db, reopened, CONSTRUCTION)
        _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)
        assert _labels(db, current)["total"] == 2

        ReviewRepository(db).reopen(reopened, reopened_by="담당자", note=None)

        assert _labels(db, current)["total"] == 1


class TestCurrentRowPolicy:
    """지시 §7 · §11-⑧⑨ — 현재 행을 어떻게 다루는가."""

    def test_an_unconfirmed_current_row_adds_nothing(self, db: Path) -> None:
        """미확정이면 애초에 셀 것이 없다 — 자기참조가 생길 수 없다."""
        current = _add(db, _ALPHA)

        assert _labels(db, current)["total"] == 0

    def test_a_confirmed_current_row_is_included_like_past_labels(self, db: Path) -> None:
        """⛔ 현재 행을 제외하지 **않는다** — 기존 적요 이력과 같은 규칙이다.

        ``ReviewService._past_labels_for`` 의 docstring 이 "자기 자신의 확정도
        이력에 포함된다" 고 못박아 두었다. 한쪽만 제외하면 두 블록의 숫자가
        서로 어긋나 담당자가 어느 쪽을 믿어야 할지 알 수 없게 된다.
        """
        current = _add(db, _ALPHA)
        _confirm(db, current, CONSTRUCTION)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()

        assert body["company_labels"]["total"] == 1
        # 적요 이력도 같은 규칙이다 — 둘의 정책이 일치하는지 함께 고정한다.
        assert body["past_labels"]["total"] == 1


class TestBusinessNoIsTheGroupingKey:
    """묶음 키는 **사업자등록번호**다.

    .. note::
        **변경 사유(STEP 64).** 이 클래스는 원래
        ``TestCompanyNameIsNotNormalised`` 였고, "거래처명을 임의로 정규화하지
        않는다" 를 지키고 있었습니다. 그 자리는 *"어느 쪽으로 묶을지는 **고객
        확인 사항**"* 이라고 적어 두고 기다리던 자리였고, **2026-08-30 고객이
        답했습니다**(`DECISIONS.md` §0.9.5 원칙 4).

        > 사업자등록번호가 동일하면 동일 업체로 판단한다.

        검사를 느슨하게 만든 것이 아니라 **바뀐 규칙에 맞게 사실을 다시
        적었습니다.** 정규화를 하지 않는다는 원래의 약속은 오히려 더
        강해졌습니다 — 이제 거래처명은 묶음에 **아예 쓰이지 않기** 때문입니다.
    """

    def test_different_spellings_are_one_company(self, db: Path) -> None:
        """⛔ 표기가 달라도 사업자번호가 같으면 **한 업체**다.

        원래 이 시험은 `(주)` · `주식회사` 를 떼지 않는다는 것을 지켰습니다.
        지금은 **떼고 말고 할 일이 없습니다** — 이름을 보지 않습니다.
        """
        _confirm(db, _add(db, "합성기업 가(주)"), CONSTRUCTION)
        current = _add(db, "(주)합성기업 가")

        assert _labels(db, current)["total"] == 1

    def test_four_spellings_of_one_business_no(self, db: Path) -> None:
        """사례 A — 한 사업자번호에 표기가 넷이어도 이력은 하나로 모인다.

        고객이 든 예(`SK브로드밴드주식회사` · `SK브로드밴드(주)` ·
        `에스케이브로드밴드(주)` · `SK브로드밴드`)와 같은 모양입니다. 예전에는
        **넷으로 나뉘어** 실제보다 적게 보였습니다.
        """
        for name in ("합성기업 가주식회사", "합성기업 가(주)", "㈜합성기업 가", "합성기업 가"):
            _confirm(db, _add(db, name, business_no=_ONE), CONSTRUCTION)
        current = _add(db, "합성기업 가", business_no=_ONE)

        assert _labels(db, current)["total"] == 4

    def test_spacing_differences_are_one_company(self, db: Path) -> None:
        """공백 차이도 마찬가지다 — 사업자번호가 같으면 함께 잡힌다."""
        _confirm(db, _add(db, "합성기업가"), CONSTRUCTION)
        current = _add(db, _ALPHA)  # '합성기업 가'

        assert _labels(db, current)["total"] == 1

    def test_a_different_business_no_has_its_own_history(self, db: Path) -> None:
        """다른 업체의 이력은 섞이지 않는다(원래 이 시험이 지키던 사실)."""
        _confirm(db, _add(db, _ALPHA, business_no=_ONE), CONSTRUCTION)
        current = _add(db, _BETA, business_no=_TWO)

        assert _labels(db, current)["total"] == 0

    def test_same_name_different_business_no_is_separated(self, db: Path) -> None:
        """사례 B — 이름이 같아도 사업자번호가 다르면 **다른 업체**다.

        .. note::
            **변경 사유(STEP 64).** 이 시험은 원래 반대(합쳐진다)를 고정하면서
            *"실측상 `(주)케이티` 1종이 여기 해당한다 — **고객 확인 사항**"*
            이라고 적어 두었습니다. 고객이 *"사업자등록번호가 다르면 거래처명이
            같더라도 동일 업체로 묶지 않는다"* 고 답해 방향이 정해졌습니다.
        """
        _confirm(db, _add(db, _ALPHA, business_no=_ONE), CONSTRUCTION)
        current = _add(db, _ALPHA, business_no=_TWO)

        assert _labels(db, current)["total"] == 0

    def test_a_renamed_company_keeps_its_history(self, db: Path) -> None:
        """사례 C — 이름이 전혀 달라져도 사업자번호가 같으면 이력이 이어진다.

        ⛔ **상호변경을 시스템이 판정한 것이 아닙니다.** 고객이 정한 기준
        (사업자번호)을 그대로 적용한 결과일 뿐이며, 왜 이름이 달라졌는지는
        사람이 확인할 일입니다.
        """
        _confirm(db, _add(db, "A기업", business_no=_ONE), CONSTRUCTION)
        current = _add(db, "B기업", business_no=_ONE)

        assert _labels(db, current)["total"] == 1

    def test_the_name_is_still_shown(self, db: Path) -> None:
        """거래처명은 **표시용**으로 남는다 — 이 건의 이름을 그대로 싣는다."""
        _confirm(db, _add(db, "A기업", business_no=_ONE), CONSTRUCTION)
        current = _add(db, "B기업", business_no=_ONE)

        block = _labels(db, current)
        assert block["company_name"] == "B기업"  # 표시용 — 이 건의 이름
        assert block["business_no"] == _ONE  # 기준 — 무엇으로 셌는가


class TestNoVerdictFields:
    """지시 §3 · §9 — 자동판정 의미의 필드를 만들지 않는다."""

    def test_the_block_has_no_score_or_recommendation(self, db: Path) -> None:
        for _ in range(5):
            _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)

        block = _labels(db, current)

        for banned in (
            "score",
            "confidence",
            "rank",
            "recommended_type",
            "predicted_type",
            "candidate",
            "candidates",
            "dominant_type",
            "dominant_ratio",
        ):
            assert banned not in block, banned

    def test_the_block_holds_only_facts(self, db: Path) -> None:
        current = _add(db, _ALPHA)

        # 변경 사유(STEP 64): 묶음 기준이 사업자번호가 되면서 "무엇으로 셌는가"
        # 를 밝히는 business_no 가 늘었다. 비교를 느슨하게 하지 않고 **새 필드를
        # 기대 집합에 함께 적는다** — 이 시험은 "사실만 담는다" 를 지킨다.
        assert set(_labels(db, current)) == {
            "business_no",
            "company_name",
            "labels",
            "total",
            "type_count",
            "has_conflict",
            "consistency",
        }

    def test_no_type_button_is_pre_selected(self, db: Path) -> None:
        """⛔ 이력이 아무리 한쪽으로 몰려도 확정값을 채우지 않는다."""
        for _ in range(10):
            _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()

        assert body["review"]["final_purchase_type"] is None
        assert body["analysis"]["candidates"] == []


class TestExistingBlocksAreUntouched:
    """지시 §11-⑫⑬⑭⑮ · §16 — 기존 기능이 그대로인가."""

    def test_description_hints_still_work(self, db: Path) -> None:
        saved = PurchaseRepository(db).insert(
            Purchase(
                business_no="1000000001",
                company_name=_ALPHA,
                contract_date=_DAY,
                payment_date=_DAY,
                amount=Decimal("1000"),
                description="○○시설 하도급 공사비",
            )
        )
        assert saved.purchase_id is not None

        body: Any = TestClient(create_app(db)).get(f"/reviews/{saved.purchase_id}").json()

        assert [h["keyword"] for h in body["description_hints"]] == ["하도급", "공사"]

    def test_past_labels_use_the_description_axis(self, db: Path) -> None:
        """⛔ 두 이력이 섞이지 않는다 — 적요는 적요끼리, 거래처는 거래처끼리."""
        # 같은 거래처 · 다른 적요로 확정한다.
        other = PurchaseRepository(db).insert(
            Purchase(
                business_no="1000000001",
                company_name=_ALPHA,
                contract_date=_DAY,
                payment_date=_DAY,
                amount=Decimal("1000"),
                description="전혀 다른 적요",
            )
        )
        assert other.purchase_id is not None
        _confirm(db, other.purchase_id, CONSTRUCTION)
        current = _add(db, _ALPHA)  # 적요는 '합성기업 가 지출'

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()

        assert body["company_labels"]["total"] == 1  # 거래처가 같으므로 잡힌다
        assert body["past_labels"]["total"] == 0  # ⛔ 적요가 다르므로 안 잡힌다

    def test_confirming_is_not_changed_by_the_block(self, db: Path) -> None:
        """지시 §11-⑭ — 확정값이 자동으로 바뀌지 않는다."""
        for _ in range(5):
            _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)
        client = TestClient(create_app(db))

        client.put(
            f"/reviews/{current}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
        )

        body: Any = client.get(f"/reviews/{current}").json()
        # ⛔ 과거가 공사 5건이어도 담당자가 고른 용역 그대로다.
        assert body["review"]["final_purchase_type"] == SERVICE

    def test_the_list_endpoint_carries_the_block_too(self, db: Path) -> None:
        """지시 §11-⑯ — 목록에도 같은 블록이 실린다."""
        _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        _add(db, _ALPHA)

        body: Any = TestClient(create_app(db)).get("/reviews").json()

        for item in body["items"]:
            assert item["company_labels"]["company_name"] == _ALPHA
            assert item["company_labels"]["total"] == 1

    def test_all_blocks_stay_independent(self, db: Path) -> None:
        """지시 §15 — 기존 필드를 삭제하거나 의미를 바꾸지 않았다."""
        current = _add(db, _ALPHA)

        body: Any = TestClient(create_app(db)).get(f"/reviews/{current}").json()

        assert set(body) == {
            "source",
            "analysis",
            "review",
            "past_labels",
            "company_labels",
            "description_hints",
        }
        assert "company_labels" not in body["review"]
        assert "company_labels" not in body["analysis"]
        assert "company_labels" not in body["past_labels"]

    def test_reading_writes_nothing(self, db: Path) -> None:
        """지시 §13 · §15 — 조회가 데이터를 바꾸지 않는다."""
        _confirm(db, _add(db, _ALPHA), CONSTRUCTION)
        current = _add(db, _ALPHA)
        purchases = PurchaseRepository(db)
        reviews = ReviewRepository(db)
        before = (
            [(p.purchase_id, p.company_id, p.company_name) for p in purchases.find_all()],
            [reviews.find_by_purchase_id(p.purchase_id or 0) for p in purchases.find_all()],
        )

        TestClient(create_app(db)).get(f"/reviews/{current}")

        after = (
            [(p.purchase_id, p.company_id, p.company_name) for p in purchases.find_all()],
            [reviews.find_by_purchase_id(p.purchase_id or 0) for p in purchases.find_all()],
        )
        assert before == after
