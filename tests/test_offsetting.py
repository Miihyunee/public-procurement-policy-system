"""
tests.test_offsetting

음수 거래 상계 판정 검증 — **2026-08-14 고객 확정**.

    음수 거래 이전에 기업명, 사업자번호, 금액이 똑같은 양수 금액이 있으면
    동일 거래로 판단한다.

PM 지시서 §5 · §6 의 예시를 그대로 고정합니다. 특히 §6 의 "동일 거래로
판단하지 않는 경우" 4가지를 모두 검증합니다.

.. warning::
    이 로직은 **아직 계산에 연결되어 있지 않습니다.** 현재 Repository 가
    ``amount <= 0`` 저장을 거부하므로 음수 거래가 DB 에 존재할 수 없습니다.
    연결 시점은 PM 결정 사항입니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from procurement.core.offsetting import (
    confirmed_match_fields,
    offset_negative_purchases,
    summarize,
)
from procurement.models import Purchase

FIELD = "payment_date"


def _p(
    amount: str,
    day: int,
    *,
    company: str = "A기업",
    business_no: str = "1234567890",
    purchase_id: int | None = None,
) -> Purchase:
    """구매 한 건을 만듭니다(3월 ``day`` 일)."""
    return Purchase(
        business_no=business_no,
        company_name=company,
        contract_date=date(2026, 3, day),
        payment_date=date(2026, 3, day),
        amount=Decimal(amount),
        purchase_id=purchase_id,
    )


class TestConfirmedRule:
    """고객이 확정한 기준으로 짝을 맺는다."""

    def test_pm_example_is_offset(self) -> None:
        """A기업 +100,000(3/1) 과 A기업 −100,000(3/10) → 동일 거래."""
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert len(result.pairs) == 1
        assert result.pairs[0].positive is positive
        assert result.pairs[0].negative is negative
        assert result.remaining == []
        assert result.unmatched_negatives == []

    def test_unrelated_purchases_are_untouched(self) -> None:
        """상계와 무관한 거래는 그대로 남는다."""
        other = _p("500000", 5, company="C기업", business_no="9999999999")
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative, other], date_of=FIELD)

        assert result.remaining == [other]

    def test_confirmed_match_fields_are_documented(self) -> None:
        """판별 요소가 고객 확정 4가지와 일치한다."""
        assert confirmed_match_fields() == (
            "company_name",
            "business_no",
            "abs(amount)",
            "이전 거래",
        )


class TestNotTheSameTransaction:
    """⛔ PM 지시서 §6 — 동일 거래로 판단하지 않는 경우."""

    def test_different_company_name(self) -> None:
        """A기업 +100,000 / B기업 −100,000 → 동일 거래 아님."""
        positive = _p("100000", 1, company="A기업")
        negative = _p("-100000", 10, company="B기업")

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.remaining == [positive, negative]

    def test_different_business_no(self) -> None:
        """사업자번호가 다르면 동일 거래 아님."""
        positive = _p("100000", 1, business_no="1111111111")
        negative = _p("-100000", 10, business_no="2222222222")

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_no_preceding_positive(self) -> None:
        """이전 양수 거래가 없으면 짝지을 대상이 없다."""
        negative = _p("-100000", 10)

        result = offset_negative_purchases([negative], date_of=FIELD)

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.remaining == [negative]

    def test_different_amount(self) -> None:
        """금액이 다르면 동일 거래 아님."""
        positive = _p("100000", 1)
        negative = _p("-200000", 10)

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]


class TestOrdering:
    """"이전 거래" 조건을 지킨다."""

    def test_later_positive_is_not_used(self) -> None:
        """음수보다 **나중**인 양수는 짝이 될 수 없다."""
        negative = _p("-100000", 1)
        positive = _p("100000", 10)

        result = offset_negative_purchases([negative, positive], date_of=FIELD)

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.remaining == [negative, positive]

    def test_same_day_is_not_preceding(self) -> None:
        """같은 날짜·같은 순번이면 '이전' 이 아니다."""
        positive = _p("100000", 5, purchase_id=1)
        negative = _p("-100000", 5, purchase_id=1)

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert result.pairs == []

    def test_same_day_earlier_row_counts_as_preceding(self) -> None:
        """같은 날짜면 저장 순서(purchase_id)로 앞뒤를 가린다."""
        positive = _p("100000", 5, purchase_id=1)
        negative = _p("-100000", 5, purchase_id=2)

        result = offset_negative_purchases([positive, negative], date_of=FIELD)

        assert len(result.pairs) == 1


class TestOneToOneMatching:
    """하나의 양수가 여러 음수에 중복 매칭되지 않는다."""

    def test_one_positive_cannot_offset_two_negatives(self) -> None:
        positive = _p("100000", 1)
        first = _p("-100000", 5)
        second = _p("-100000", 10)

        result = offset_negative_purchases([positive, first, second], date_of=FIELD)

        assert len(result.pairs) == 1
        assert len(result.unmatched_negatives) == 1

    def test_two_positives_offset_two_negatives(self) -> None:
        positives = [_p("100000", 1, purchase_id=1), _p("100000", 2, purchase_id=2)]
        negatives = [_p("-100000", 5, purchase_id=3), _p("-100000", 6, purchase_id=4)]

        result = offset_negative_purchases([*positives, *negatives], date_of=FIELD)

        assert len(result.pairs) == 2
        assert result.remaining == []
        assert result.unmatched_negatives == []

    def test_closest_preceding_positive_is_used(self) -> None:
        """같은 조건의 양수가 여럿이면 음수에 가장 가까운 것을 쓴다."""
        old = _p("100000", 1, purchase_id=1)
        recent = _p("100000", 8, purchase_id=2)
        negative = _p("-100000", 10, purchase_id=3)

        result = offset_negative_purchases([old, recent, negative], date_of=FIELD)

        assert result.pairs[0].positive is recent
        assert result.remaining == [old]


class TestEdgeCases:
    """경계 상황."""

    def test_empty_input(self) -> None:
        result = offset_negative_purchases([], date_of=FIELD)
        assert result.remaining == []
        assert result.pairs == []

    def test_only_positives_are_untouched(self) -> None:
        items = [_p("100000", 1), _p("200000", 2)]
        result = offset_negative_purchases(items, date_of=FIELD)
        assert result.remaining == items

    def test_company_name_whitespace_is_trimmed(self) -> None:
        """앞뒤 공백만 정리한다. 그 밖의 표기 정규화는 하지 않는다."""
        positive = _p("100000", 1, company="  A기업  ")
        negative = _p("-100000", 10, company="A기업")

        assert len(offset_negative_purchases([positive, negative], date_of=FIELD).pairs) == 1

    def test_inner_whitespace_is_not_normalized(self) -> None:
        """'A 기업' 과 'A기업' 을 같다고 보지 않는다(확정되지 않은 규칙)."""
        positive = _p("100000", 1, company="A 기업")
        negative = _p("-100000", 10, company="A기업")

        assert offset_negative_purchases([positive, negative], date_of=FIELD).pairs == []

    def test_date_field_must_be_given_explicitly(self) -> None:
        """``date_of`` 는 기본값이 없다 — 상계 기준 날짜가 확정되지 않았다."""
        with pytest.raises(TypeError):
            offset_negative_purchases([])  # type: ignore[call-arg]

    def test_unknown_date_field_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="date_of"):
            offset_negative_purchases([], date_of="created_at")

    def test_resolution_date_is_now_an_allowed_field(self) -> None:
        """결의일자 기준 상계도 가능하다(2026-08-15 필드 신설).

        .. note::
            **기대값이 바뀐 이유** — 이전에는 ``resolution_date`` 라는 필드
            자체가 없어 거부 대상이었습니다. 필드가 생겼으므로 호출자가
            선택할 수 있는 값이 되었습니다.
        """
        positive = _p("100000", 1)
        negative = _p("-100000", 10)
        positive.resolution_date = positive.contract_date
        negative.resolution_date = negative.contract_date

        result = offset_negative_purchases(
            [positive, negative], date_of="resolution_date"
        )

        assert len(result.pairs) == 1
        assert result.remaining == []

    def test_missing_resolution_date_is_reported_not_guessed(self) -> None:
        """⛔ 결의일자가 없는 행을 다른 날짜로 대체하지 않는다."""
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        with pytest.raises(ValueError, match="resolution_date"):
            offset_negative_purchases([positive, negative], date_of="resolution_date")

    def test_contract_date_can_be_used_as_ordering_field(self) -> None:
        """계약일 기준으로도 판정할 수 있다(호출자가 선택)."""
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative], date_of="contract_date")

        assert len(result.pairs) == 1

    def test_summary_is_readable(self) -> None:
        result = offset_negative_purchases([_p("100000", 1), _p("-100000", 5)], date_of=FIELD)
        assert summarize(result) == "상계 1쌍 · 남은 거래 0건 · 짝 없는 음수 0건"


class TestNotWiredIntoCalculation:
    """이 로직은 아직 계산에 연결되지 않았다.

    현재 Repository 가 음수 저장을 막고 있어, 연결해도 대상 데이터가 없습니다.
    연결 승인 후 이 테스트를 삭제하고 통합 테스트로 대체합니다.
    """

    def test_repository_still_rejects_negative_amounts(self, tmp_path: object) -> None:
        """음수 구매는 아직 저장 자체가 불가능하다(D-003 · C-2 미해소)."""
        from pathlib import Path

        from procurement.database.purchase_repository import (
            PurchaseRepository,
            PurchaseValidationError,
        )

        assert isinstance(tmp_path, Path)
        repo = PurchaseRepository(tmp_path / "offset.db")
        repo.create_table()

        with pytest.raises(PurchaseValidationError):
            repo.insert(_p("-100000", 1))

    def test_calculator_does_not_import_offsetting(self) -> None:
        """계산기가 아직 상계 로직을 쓰지 않는다."""
        from pathlib import Path

        import procurement.calculators.procurement_achievement as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "offsetting" not in source
