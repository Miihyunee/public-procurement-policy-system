"""STEP 40 — 적요 참고 근거.

고객이 *"이런 낱말을 보고 판단한다"* 고 알려준 낱말이 적요에 들어 있는지만
보여줍니다(2026-08-25 회신).

.. warning::
    🔴 **구매유형을 판정하지 않습니다.**

    이 기능이 만드는 것은 "낱말이 들어 있다" 는 **관찰 사실**뿐입니다.
    유형·점수·순위·추천을 만들지 않고, 담당자의 확정값을 건드리지 않습니다.

.. warning::
    ⛔ **낱말이 유형을 뜻하지 않습니다.** 확정 1,744건 실측:
    ``용역 준공금`` 4건 중 2건이 공사 · ``기념품`` 36건 중 9건이 용역 ·
    ``수수료`` 119건 중 7건이 물품(소프트웨어 라이선스). 경계 사례의 처리
    방식은 **아직 고객 확인 전**입니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.description_hints import HINT_KEYWORDS, DescriptionHint, find_hints
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.purchase import Purchase

_DAY = date(2026, 3, 2)


def _keywords(description: str | None) -> list[str]:
    return [hint.keyword for hint in find_hints(description)]


class TestSingleKeyword:
    """지시 §11 — 낱말 하나."""

    def test_construction_word(self) -> None:
        assert _keywords("○○시설 하도급 공사비") == ["하도급", "공사"]

    def test_service_word(self) -> None:
        assert _keywords("나라장터 이용 수수료") == ["수수료"]

    def test_goods_word(self) -> None:
        assert _keywords("행사 현수막 제작") == ["현수막"]

    def test_every_customer_word_is_findable(self) -> None:
        """고객이 말한 낱말은 하나도 빠짐없이 찾을 수 있어야 한다."""
        for keyword in HINT_KEYWORDS:
            assert keyword in _keywords(f"어떤 건 {keyword} 지출"), keyword


class TestMultipleKeywords:
    """지시 §4 · §8 — 여러 개면 **전부** 적는다. 우선순위를 정하지 않는다."""

    def test_all_hits_are_reported(self) -> None:
        found = _keywords("나무심기 조성공사 공사 및 용역 준공금")

        assert "조성공사" in found
        assert "공사" in found
        assert "용역 준공금" in found

    def test_no_hit_is_dropped_as_a_loser(self) -> None:
        """⛔ 공사 낱말이 있다고 용역 낱말을 지우지 않는다."""
        found = _keywords("매입축사 지장물 철거공사 선금")

        assert "철거공사" in found
        assert "공사" in found

    def test_words_across_groups_coexist(self) -> None:
        found = _keywords("경영실적보고서 인쇄계약 완수금")

        assert "인쇄" in found  # 고객이 물품 예로 든 낱말
        assert "용역" not in found  # ⚠️ '완수금' 단독은 목록에 없다


class TestNoKeyword:
    """지시 §11 — 미해당."""

    def test_plain_description_has_no_hint(self) -> None:
        assert find_hints("12월 전기요금(이천)") == ()

    def test_empty_and_none_are_safe(self) -> None:
        assert find_hints(None) == ()
        assert find_hints("") == ()
        assert find_hints("   ") == ()


class TestBoundaryCasesAreNotDecided:
    """지시 §8 — 고객이 아직 답하지 않은 경계 사례를 우리가 정하지 않는다."""

    def test_software_licence_is_not_called_goods(self) -> None:
        """실측: `수수료` 119건 중 7건이 물품(소프트웨어). ⛔ 유형을 말하지 않는다."""
        hints = find_hints("Adobe CAD 라이선스 수수료")

        assert [hint.keyword for hint in hints] == ["수수료"]
        for hint in hints:
            assert not hasattr(hint, "purchase_type")
            assert "용역" not in hint.text
            assert "물품" not in hint.text

    def test_promotional_gift_is_not_called_goods(self) -> None:
        """실측: `기념품` 36건 중 9건이 용역(`홍보 기념품 구입`)."""
        hints = find_hints("홍보 기념품 구입")

        assert [hint.keyword for hint in hints] == ["기념품"]
        assert hints[0].text == "적요에 '기념품' 포함"

    def test_service_completion_payment_is_not_called_service(self) -> None:
        """실측: `용역 준공금` 4건 중 2건이 공사."""
        hints = find_hints("나무심기 조성공사 공사 및 용역 준공금")

        for hint in hints:
            assert "공사입니다" not in hint.text
            assert "용역입니다" not in hint.text

    def test_the_hint_carries_no_verdict_field(self) -> None:
        """⛔ 타입 수준에서 판정을 담을 수 없다."""
        hint = DescriptionHint(keyword="공사", text="적요에 '공사' 포함")

        for banned in ("purchase_type", "score", "rank", "confidence", "label"):
            assert not hasattr(hint, banned), banned


class TestReusesExistingNormalisation:
    """지시 §6-C · §7 — 새 정규화 규칙을 만들지 않는다."""

    def test_spacing_variants_are_found(self) -> None:
        """실측: 원문 비교로는 이 3건을 놓쳤다."""
        assert "철거공사" in _keywords("26년 6월 철거 공사용역비(전신주철거)")
        assert "용역 선금" in _keywords("녹색생활실천학교 용역선금")
        assert "용역 선금" in _keywords("양성_필기평가운영용역선금_외주용역비")

    def test_no_morphological_analysis(self) -> None:
        """⛔ 형태소 분석·유사어를 만들지 않는다 — 포함 여부만 본다."""
        assert _keywords("공사하였음") == ["공사"]  # 단순 포함
        assert _keywords("시공 계약") == []  # '시공' 은 고객 목록에 없다


class TestKeywordListIsCustomerOnly:
    """지시 §1 · §2 — 고객이 말한 낱말만. 우리가 넓히지 않는다."""

    def test_exactly_nineteen_words(self) -> None:
        assert len(HINT_KEYWORDS) == 19
        assert len(set(HINT_KEYWORDS)) == 19  # 중복 없음

    def test_words_the_customer_did_not_say_are_absent(self) -> None:
        """⛔ 우리가 추론해 넣지 않았다."""
        for invented in ("시공", "관급", "설치", "제작", "구입", "구매", "임차", "위탁", "운영"):
            assert invented not in HINT_KEYWORDS, invented

    def test_the_customer_words_are_present(self) -> None:
        for said in ("하도급", "노무비", "조성공사", "철거공사", "민원공사", "공사"):
            assert said in HINT_KEYWORDS, said
        for said in ("용역 선금", "용역 완수금", "용역 준공금", "측량", "자문", "수수료", "용역"):
            assert said in HINT_KEYWORDS, said
        for said in ("현수막", "인쇄", "책", "소모성", "기념품", "피복"):
            assert said in HINT_KEYWORDS, said


# ----------------------------------------------------------------------
# HTTP — 기존 검토 API 에 하위 호환으로 붙었는가
# ----------------------------------------------------------------------
@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """합성 구매 3건. ⛔ 실제 고객 데이터를 쓰지 않습니다."""
    path = tmp_path / "hints.db"
    init_db(path)
    purchases = PurchaseRepository(path)
    for business_no, description in (
        ("1000000001", "○○시설 하도급 공사비"),
        ("2000000002", "Adobe CAD 라이선스 수수료"),
        ("3000000003", "12월 전기요금(이천)"),
    ):
        purchases.insert(
            Purchase(
                business_no=business_no,
                company_name="합성기업",
                contract_date=_DAY,
                payment_date=_DAY,
                amount=Decimal("1000"),
                description=description,
            )
        )
    return path


def _items(path: Path) -> list[Any]:
    """검토 목록 응답의 items. JSON 이라 구조가 느슨하다."""
    body: Any = TestClient(create_app(path)).get("/reviews").json()
    items: list[Any] = body["items"]
    return items


class TestHttp:
    def test_hints_ride_along_with_the_review_item(self, seeded: Path) -> None:
        item = next(i for i in _items(seeded) if "하도급" in str(i["source"]["description"]))

        assert [h["keyword"] for h in item["description_hints"]] == ["하도급", "공사"]
        assert item["description_hints"][0]["text"] == "적요에 '하도급' 포함"

    def test_no_hint_is_an_empty_list_not_an_error(self, seeded: Path) -> None:
        item = next(i for i in _items(seeded) if "전기요금" in str(i["source"]["description"]))

        assert item["description_hints"] == []

    def test_hints_carry_no_score_or_type(self, seeded: Path) -> None:
        """⛔ 후보와 섞이지 않는다 — 점수·유형·순위 필드가 없다."""
        for item in _items(seeded):
            for hint in item["description_hints"]:
                assert set(hint) == {"keyword", "text"}

    def test_the_analysis_block_is_untouched(self, seeded: Path) -> None:
        """⛔ 참고 근거가 후보를 만들지 않는다 — 분석 결과는 그대로 비어 있다."""
        for item in _items(seeded):
            assert item["analysis"]["candidates"] == []
            assert item["analysis"]["candidate_count"] == 0
            assert item["analysis"]["analyzer_name"] is None

    def test_existing_fields_are_all_still_there(self, seeded: Path) -> None:
        """지시 §12 — 기존 API 계약을 깨지 않는다."""
        for item in _items(seeded):
            assert {"source", "analysis", "review", "past_labels"} <= set(item)


class TestConfirmedValuesAreProtected:
    """지시 §11 · §13 — 참고 근거가 확정값을 바꾸지 않는다."""

    def test_confirming_then_reading_keeps_the_decision(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))
        target = next(i for i in _items(seeded) if "하도급" in str(i["source"]["description"]))
        purchase_id = target["source"]["purchase_id"]

        confirmed = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": "SERVICE", "reviewed_by": "담당자"},
        )
        assert confirmed.status_code == 200

        again = next(i for i in _items(seeded) if i["source"]["purchase_id"] == purchase_id)
        # ⛔ 적요에 '하도급'·'공사' 가 있어도 담당자가 고른 SERVICE 그대로다.
        assert again["review"]["final_purchase_type"] == "SERVICE"
        assert again["review"]["status"] == "CONFIRMED"
        assert [h["keyword"] for h in again["description_hints"]] == ["하도급", "공사"]

    def test_reading_writes_nothing(self, seeded: Path) -> None:
        """⛔ 조회가 DB 를 바꾸지 않는다."""
        client = TestClient(create_app(seeded))
        purchases = PurchaseRepository(seeded)
        reviews = ReviewRepository(seeded)
        before = (
            [(p.purchase_id, p.company_id, p.description) for p in purchases.find_all()],
            [reviews.find_by_purchase_id(p.purchase_id or 0) for p in purchases.find_all()],
        )

        client.get("/reviews")

        after = (
            [(p.purchase_id, p.company_id, p.description) for p in purchases.find_all()],
            [reviews.find_by_purchase_id(p.purchase_id or 0) for p in purchases.find_all()],
        )
        assert before == after
