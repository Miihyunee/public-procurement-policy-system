"""
tests.test_offsetting

음수 거래 상계 판정 검증 — **2026-08-20 고객 확정 최종 업무규칙**.

    ① 동일 기업 + ② 동일 사업자등록번호 + ③ 동일 금액 → 후보
    ⑦ 세금계산서 발행일자 차이가 가장 가까운 (+)/(−) 를 1:1 매칭
    ⑧ 동률이면 담당자가 G20 지출결의서 조회 여부로 판단 → **시스템은 보류**

여기서 잡으려는 것은 "짝을 잘 맺는가" 만이 아니라 **확정되지 않은 규칙을
만들지 않았는가** 입니다. 적요·예산과목·발행 선후는 판정에 쓰지 않습니다.

.. warning::
    이 로직은 **아직 계산에 연결되어 있지 않습니다.** 현재 Repository 가
    ``amount <= 0`` 저장을 거부하므로 음수 거래가 DB 에 존재할 수 없습니다.
    연결은 별도 PM 승인 사항입니다(DECISIONS §0.6.3.3 D 단계).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from procurement.core.offsetting import (
    confirmed_match_fields,
    offset_negative_purchases,
    summarize,
)
from procurement.models import Purchase

FIXED = date(2026, 1, 1)


def _p(
    amount: str,
    issue_day: int | None,
    *,
    company: str = "A기업",
    business_no: str = "1234567890",
    description: str | None = None,
    budget_account: str | None = None,
    purchase_id: int | None = None,
) -> Purchase:
    """구매 한 건을 만듭니다(발행일자는 3월 ``issue_day`` 일).

    결의일자·계약일자·지급일은 판정에 쓰이지 않으므로 모두 같은 날로 고정해,
    **발행일자만이 판정에 영향을 준다**는 사실이 드러나게 합니다.
    """
    return Purchase(
        business_no=business_no,
        company_name=company,
        contract_date=FIXED,
        payment_date=FIXED,
        resolution_date=FIXED,
        issue_date=date(2026, 3, issue_day) if issue_day is not None else None,
        description=description,
        budget_account=budget_account,
        amount=Decimal(amount),
        purchase_id=purchase_id,
    )


class TestCandidateConditions:
    """① ② ③ — 후보를 찾는 3조건."""

    def test_single_candidate_is_offset(self) -> None:
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1
        assert result.pairs[0].positive is positive
        assert result.pairs[0].negative is negative
        assert result.remaining == []

    def test_different_amount_is_not_a_candidate(self) -> None:
        positive = _p("100000", 1)
        negative = _p("-200000", 10)

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_different_company_name_is_not_a_candidate(self) -> None:
        positive = _p("100000", 1, company="A기업")
        negative = _p("-100000", 10, company="B기업")

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_different_business_no_is_not_a_candidate(self) -> None:
        positive = _p("100000", 1, business_no="1111111111")
        negative = _p("-100000", 10, business_no="2222222222")

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_no_positive_at_all(self) -> None:
        negative = _p("-100000", 10)

        result = offset_negative_purchases([negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.needs_manual_review == []

    def test_confirmed_match_fields_are_documented(self) -> None:
        assert confirmed_match_fields() == (
            "company_name",
            "business_no",
            "abs(amount)",
            "nearest issue_date",
        )


class TestNearestIssueDate:
    """⑦ — 발행일자 차이가 가장 가까운 건과 맺는다."""

    def test_closer_candidate_wins(self) -> None:
        """+3/10(10일) · +3/15(5일) · −3/20 → 3/15 쪽."""
        far = _p("100000", 10)
        near = _p("100000", 15)
        negative = _p("-100000", 20)

        result = offset_negative_purchases([far, near, negative])

        assert result.pairs[0].positive is near
        assert result.pairs[0].distance_days == 5
        assert result.remaining == [far]

    def test_same_day_is_distance_zero(self) -> None:
        positive = _p("100000", 5)
        negative = _p("-100000", 5)

        result = offset_negative_purchases([positive, negative])

        assert result.pairs[0].distance_days == 0

    def test_positive_issued_later_is_still_matched(self) -> None:
        """⛔ "양수가 먼저" 를 조건으로 쓰지 않는다.

        고객은 업무 흐름상 (+) → (−) 가 일반적이라고 설명했지만 불변식이
        아니다. 담당자가 상계 표시한 126쌍 중 2쌍이 (+) 나중 발행이었고,
        조건으로 넣으면 이를 놓친다(`DECISIONS.md` §0.6.3.4).
        """
        negative = _p("-242000", 22)
        positive = _p("242000", 26)

        result = offset_negative_purchases([negative, positive])

        assert len(result.pairs) == 1
        assert result.pairs[0].distance_days == 4
        assert result.remaining == []

    def test_later_but_closer_beats_earlier_but_farther(self) -> None:
        """이전 양수가 있어도 **더 가까운** 나중 양수를 쓴다."""
        earlier_far = _p("100000", 1)
        later_near = _p("100000", 12)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([earlier_far, later_near, negative])

        assert result.pairs[0].positive is later_near
        assert result.pairs[0].distance_days == 2

    def test_each_negative_takes_its_own_nearest(self) -> None:
        """양수 2 · 음수 2 → 각자 가까운 쪽과 맺는다(중복 매칭 없음)."""
        p_early = _p("100000", 1, purchase_id=1)
        p_late = _p("100000", 20, purchase_id=2)
        n_early = _p("-100000", 2, purchase_id=3)
        n_late = _p("-100000", 21, purchase_id=4)

        result = offset_negative_purchases([p_early, p_late, n_early, n_late])

        assert len(result.pairs) == 2
        matched = {id(pair.negative): pair.positive for pair in result.pairs}
        assert matched[id(n_early)] is p_early
        assert matched[id(n_late)] is p_late
        assert result.remaining == []

    def test_one_positive_cannot_serve_two_negatives(self) -> None:
        """양수가 모자라면 남은 음수는 짝 없음으로 보고한다."""
        positive = _p("100000", 10)
        near = _p("-100000", 11)
        far = _p("-100000", 25)

        result = offset_negative_purchases([positive, near, far])

        assert len(result.pairs) == 1
        assert result.pairs[0].negative is near
        assert result.unmatched_negatives == [far]


class TestTiesAreHeldForManualReview:
    """⑧ — 동률이면 **임의로 고르지 않는다** (G20 확인은 담당자 몫)."""

    def test_symmetric_tie_is_held(self) -> None:
        """+3/15(5일) · −3/20 · +3/25(5일) → 고를 수 없다 (PM 예시)."""
        before = _p("100000", 15, purchase_id=1)
        negative = _p("-100000", 20, purchase_id=2)
        after = _p("100000", 25, purchase_id=3)

        result = offset_negative_purchases([before, negative, after])

        assert result.pairs == []
        assert result.unmatched_negatives == []
        assert len(result.needs_manual_review) == 1
        assert result.remaining == [before, negative, after]

    def test_same_issue_date_candidates_are_held(self) -> None:
        """같은 날 발행된 후보 4건 — 실데이터에서 가장 흔한 동률 형태."""
        candidates = [_p("16900", 5, company="엘지전자(주)", purchase_id=i) for i in range(4)]
        negative = _p("-16900", 5, company="엘지전자(주)", purchase_id=9)

        result = offset_negative_purchases([*candidates, negative])

        assert result.pairs == []
        group = result.needs_manual_review[0]
        assert len(group.candidates) == 4
        assert group.distance_days == 0

    def test_held_group_reports_what_the_reviewer_needs(self) -> None:
        first = _p("16900", 5, company="엘지전자(주)")
        second = _p("16900", 5, company="엘지전자(주)")
        negative = _p("-16900", 5, company="엘지전자(주)")

        group = offset_negative_purchases([first, second, negative]).needs_manual_review[0]

        assert group.company_name == "엘지전자(주)"
        assert group.business_no == "1234567890"
        assert group.amount == Decimal("16900")
        assert group.negative is negative
        assert group.candidates == [first, second]

    def test_a_tie_does_not_block_a_clear_negative(self) -> None:
        """한 음수가 보류돼도 **명확한 다른 음수**는 정상 상계된다."""
        tied_a = _p("100000", 15, purchase_id=1)
        tied_b = _p("100000", 25, purchase_id=2)
        ambiguous = _p("-100000", 20, purchase_id=3)
        clear_positive = _p("70000", 3, company="B기업", business_no="2222222222")
        clear_negative = _p("-70000", 4, company="B기업", business_no="2222222222")

        result = offset_negative_purchases(
            [tied_a, tied_b, ambiguous, clear_positive, clear_negative]
        )

        assert len(result.pairs) == 1
        assert result.pairs[0].negative is clear_negative
        assert len(result.needs_manual_review) == 1

    def test_certain_negative_is_matched_before_the_tied_one(self) -> None:
        """같은 그룹 안에서도 **확정 가능한 쪽을 먼저** 처리한다.

        −3/16 은 +3/15 하고만 1일 차이라 확정된다. 남은 −3/20 은 +3/25 하고만
        후보가 남으므로 역시 확정된다. 처리 순서 때문에 놓치는 일이 없어야 한다.
        """
        near = _p("100000", 15, purchase_id=1)
        far = _p("100000", 25, purchase_id=2)
        certain = _p("-100000", 16, purchase_id=3)
        other = _p("-100000", 20, purchase_id=4)

        result = offset_negative_purchases([near, far, certain, other])

        assert len(result.pairs) == 2
        assert result.needs_manual_review == []
        assert result.remaining == []


class TestNoUnconfirmedRules:
    """⛔ 확정되지 않은 규칙을 만들지 않았다."""

    def test_different_description_is_still_offset(self) -> None:
        """적요가 달라도 상계한다 (2026-08-20 고객 확정)."""
        positive = _p("360000", 6, description="에스프레소 원두(디카페인) -4월 분")
        negative = _p("-360000", 16, description="에스프레소 원두(디카페인)")

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1

    def test_description_does_not_break_a_tie(self) -> None:
        """⛔ 동률을 적요로 가르지 않는다 — 확정되지 않은 규칙이다."""
        same_note = _p("100000", 15, description="토너")
        other_note = _p("100000", 25, description="토너 (4월분)")
        negative = _p("-100000", 20, description="토너")

        result = offset_negative_purchases([same_note, other_note, negative])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 1

    def test_blank_budget_account_is_not_a_trigger(self) -> None:
        """⛔ 예산과목 공란을 상계 조건으로 쓰지 않는다."""
        positive = _p("100000", 10, budget_account="소모성물품구입비")
        negative = _p("-100000", 12, budget_account=None)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1

    def test_budget_account_does_not_break_a_tie(self) -> None:
        """⛔ 동률을 예산과목으로 가르지 않는다."""
        blank = _p("100000", 15, budget_account=None)
        filled = _p("100000", 25, budget_account="소모성물품구입비")
        negative = _p("-100000", 20, budget_account=None)

        result = offset_negative_purchases([blank, filled, negative])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 1

    def test_other_dates_do_not_affect_the_decision(self) -> None:
        """⛔ 결의일자·계약일자·지급일은 판정에 쓰지 않는다."""
        far = _p("100000", 10)
        near = _p("100000", 15)
        negative = _p("-100000", 20)
        # 다른 날짜를 흔들어도 결과가 같아야 한다.
        far.resolution_date = date(2026, 3, 19)
        far.payment_date = date(2026, 3, 19)
        far.contract_date = date(2026, 3, 19)

        result = offset_negative_purchases([far, near, negative])

        assert result.pairs[0].positive is near

    def test_source_reads_no_other_date_field(self) -> None:
        """⛔ 모듈 **코드**가 다른 날짜 필드를 읽지 않는다."""
        import ast
        from pathlib import Path

        import procurement.core.offsetting as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "issue_date" in attributes
        for other in ("resolution_date", "payment_date", "contract_date"):
            assert other not in attributes, other

    def test_source_does_not_compare_description_or_budget_account(self) -> None:
        """⛔ 적요·예산과목을 코드에서 읽지 않는다."""
        import ast
        from pathlib import Path

        import procurement.core.offsetting as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "description" not in attributes
        assert "budget_account" not in attributes

    def test_source_does_not_reach_for_g20(self) -> None:
        """⛔ G20 접근 코드가 없다 (로그인·조회·자동화 범위 밖)."""
        from pathlib import Path

        import procurement.core.offsetting as module

        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("http", "request", "selenium", "playwright", "login", "urllib"):
            assert forbidden not in source, forbidden


class TestMissingIssueDate:
    """발행일자가 없으면 **보고**한다 — 다른 날짜로 대체하지 않는다."""

    def test_missing_issue_date_is_reported(self) -> None:
        positive = _p("100000", None)
        negative = _p("-100000", None)

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.missing_issue_date == [positive, negative]
        assert result.remaining == [positive, negative]

    def test_missing_row_does_not_block_the_others(self) -> None:
        legacy = _p("100000", None, purchase_id=1)
        positive = _p("100000", 10, purchase_id=2)
        negative = _p("-100000", 12, purchase_id=3)

        result = offset_negative_purchases([legacy, positive, negative])

        assert len(result.pairs) == 1
        assert result.missing_issue_date == [legacy]
        assert result.remaining == [legacy]


class TestEdgeCases:
    """경계 상황."""

    def test_empty_input(self) -> None:
        result = offset_negative_purchases([])
        assert result.remaining == []
        assert result.pairs == []

    def test_only_positives_are_untouched(self) -> None:
        items = [_p("100000", 1), _p("200000", 2)]
        assert offset_negative_purchases(items).remaining == items

    def test_unrelated_purchases_are_untouched(self) -> None:
        other = _p("500000", 5, company="C기업", business_no="9999999999")
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        assert offset_negative_purchases([positive, negative, other]).remaining == [other]

    def test_zero_amount_is_not_offset(self) -> None:
        zero = _p("0", 3)
        result = offset_negative_purchases([zero])
        assert result.remaining == [zero]
        assert result.pairs == []

    def test_company_name_whitespace_is_trimmed(self) -> None:
        positive = _p("100000", 1, company="  A기업  ")
        negative = _p("-100000", 10, company="A기업")

        assert len(offset_negative_purchases([positive, negative]).pairs) == 1

    def test_inner_whitespace_is_not_normalized(self) -> None:
        """'A 기업' 과 'A기업' 을 같다고 보지 않는다(확정되지 않은 규칙)."""
        positive = _p("100000", 1, company="A 기업")
        negative = _p("-100000", 10, company="A기업")

        assert offset_negative_purchases([positive, negative]).pairs == []

    def test_summary_is_readable(self) -> None:
        result = offset_negative_purchases([_p("100000", 1), _p("-100000", 5)])
        assert summarize(result) == (
            "상계 1쌍 · 남은 거래 0건 · 짝 없는 음수 0건 · 담당자 확인 0건 · 발행일자 없음 0건"
        )


class TestNotWiredIntoCalculation:
    """이 로직은 아직 계산에 연결되지 않았다 (D 단계는 별도 승인)."""

    def test_repository_still_rejects_negative_amounts(self, tmp_path: object) -> None:
        from pathlib import Path

        import pytest

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
        from pathlib import Path

        import procurement.calculators.procurement_achievement as module

        assert "offsetting" not in Path(module.__file__).read_text(encoding="utf-8")

    def test_importer_and_upload_do_not_import_offsetting(self) -> None:
        """⛔ 업로드 → DB 경로에도 아직 연결하지 않는다."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for relative in ("uploads", "services", "database", "importers"):
            for path in (root / relative).rglob("*.py"):
                assert "offsetting" not in path.read_text(encoding="utf-8"), path
