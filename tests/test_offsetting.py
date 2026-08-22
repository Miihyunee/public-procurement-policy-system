"""
tests.test_offsetting

음수 거래 상계 판정 검증 — **2026-08-20 고객 최종 확정 업무규칙**.

    ① 동일 기업 + ② 동일 사업자등록번호 + ③ 동일 금액 → 후보 검색
        후보 0건       → 짝 없음
        후보 1건 (1:1) → 자동 상계
        후보 2건 이상  → 담당자 확인 대상 (G20 지출결의서 확인은 담당자 몫)

여기서 잡으려는 것은 "짝을 잘 맺는가" 가 아니라 **확정되지 않은 우선순위를
만들지 않았는가** 입니다. 발행일자·적요·예산과목은 **담당자에게 보여줄 참고
정보**이며 판정에 쓰지 않습니다.

.. note::
    **2026-08-20 기대값 변경** — 이전 판에는 "발행일자 차이가 최소인 후보를
    자동 선택" 하는 테스트가 있었습니다. 고객 최종 답변("후보가 여러 건이면
    지출결의서를 확인하여 진행")과 맞지 않고, 실측에서 자동 상계 72쌍 중 5쌍이
    담당자 처리와 달랐으므로 **업무규칙 자체가 바뀌었습니다.** 해당 테스트는
    삭제하고, 같은 상황이 이제 **보류**된다는 것을 고정합니다.

.. warning::
    이 로직은 **아직 계산에 연결되어 있지 않습니다.** 현재 Repository 가
    ``amount <= 0`` 저장을 거부하므로 음수 거래가 DB 에 존재할 수 없습니다.
    연결은 별도 PM 승인 사항입니다(DECISIONS §0.6.3.3 D 단계).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from procurement.core.offsetting import (
    CONTESTED_CANDIDATE,
    MULTIPLE_CANDIDATES,
    confirmed_match_fields,
    offset_negative_purchases,
    summarize,
)
from procurement.models import Purchase

FIXED = date(2026, 1, 1)


def _p(
    amount: str,
    issue_day: int | None = None,
    *,
    company: str = "A기업",
    business_no: str = "1234567890",
    description: str | None = None,
    budget_account: str | None = None,
    purchase_id: int | None = None,
) -> Purchase:
    """구매 한 건을 만듭니다(발행일자는 3월 ``issue_day`` 일).

    결의일자·계약일자·지급일은 판정에 쓰이지 않으므로 모두 같은 날로 고정합니다.
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

    def test_confirmed_match_fields_are_exactly_three(self) -> None:
        """판별 요소는 3가지뿐이다 — 발행일자는 판별 요소가 아니다.

        .. note::
            **기대값이 바뀐 이유** — 발행일자 최근접 자동 선택이 제거되면서
            ``"nearest issue_date"`` 가 판별 요소에서 빠졌습니다. 발행일자는
            담당자가 확인할 때 보는 참고정보입니다.
        """
        assert confirmed_match_fields() == ("company_name", "business_no", "abs(amount)")

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

    def test_no_candidate_at_all(self) -> None:
        """후보 0건 → 짝 없음. 상계하지 않고 그대로 보고한다."""
        negative = _p("-100000", 10)

        result = offset_negative_purchases([negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.needs_manual_review == []
        assert result.remaining == [negative]


class TestSingleCandidateIsAutoOffset:
    """후보 1건 → ✅ 자동 상계."""

    def test_one_to_one_is_offset(self) -> None:
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1
        assert result.pairs[0].positive is positive
        assert result.pairs[0].negative is negative
        assert result.remaining == []
        assert result.needs_manual_review == []

    def test_issue_date_order_does_not_matter(self) -> None:
        """(+) 가 (−) 보다 나중 발행이어도 1:1 이면 상계한다."""
        negative = _p("-242000", 22)
        positive = _p("242000", 26)

        result = offset_negative_purchases([negative, positive])

        assert len(result.pairs) == 1

    def test_far_apart_dates_still_offset_when_one_to_one(self) -> None:
        """후보가 하나뿐이면 발행일자가 아무리 멀어도 상계한다.

        발행일자는 판정 조건이 아니므로 거리로 걸러내지 않습니다.
        """
        positive = _p("360000", 6)
        negative = _p("-360000", 26)

        assert len(offset_negative_purchases([positive, negative]).pairs) == 1

    def test_different_description_is_still_offset(self) -> None:
        """적요가 달라도 1:1 이면 상계한다 (고객 확정)."""
        positive = _p("360000", 6, description="에스프레소 원두(디카페인) -4월 분")
        negative = _p("-360000", 16, description="에스프레소 원두(디카페인)")

        assert len(offset_negative_purchases([positive, negative]).pairs) == 1

    def test_budget_account_state_does_not_matter(self) -> None:
        """예산과목이 공란이든 채워졌든 1:1 이면 상계한다."""
        positive = _p("100000", 10, budget_account="소모성물품구입비")
        negative = _p("-100000", 12, budget_account=None)

        assert len(offset_negative_purchases([positive, negative]).pairs) == 1

    def test_missing_issue_date_does_not_block_a_one_to_one_pair(self) -> None:
        """발행일자가 없어도 1:1 이면 상계한다.

        .. warning::
            🟡 **내부 판단 · 미확정 예외사항** — 고객이 확정한 규칙이 아닙니다
            (`DECISIONS.md` §0.6.3.5). 발행일자가 판정 조건이 아니므로 상계를
            막을 근거가 없다는 **우리 판단**입니다. 실데이터 결측은 0건이므로
            현재 영향이 없고, 결측 행은 ``missing_issue_date`` 로 항상 보고해
            나중에 고객 확인이 가능하도록 남깁니다.

        .. note::
            **기대값이 바뀐 이유** — 이전 판은 발행일자로 후보를 골랐으므로 값이
            없으면 판정할 수 없었습니다. 판정에서 발행일자가 빠지면서 그 제약의
            근거가 사라졌습니다.
        """
        positive = _p("100000", None)
        negative = _p("-100000", None)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1
        assert result.missing_issue_date == [positive, negative]


class TestMultipleCandidatesAreHeld:
    """후보 2건 이상 → 🟡 담당자 확인. **아무것도 고르지 않는다.**"""

    def test_two_candidates_are_held(self) -> None:
        first = _p("100000", 15, purchase_id=1)
        second = _p("100000", 25, purchase_id=2)
        negative = _p("-100000", 20, purchase_id=3)

        result = offset_negative_purchases([first, second, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == []
        assert len(result.needs_manual_review) == 1
        assert result.needs_manual_review[0].reason == MULTIPLE_CANDIDATES
        assert result.remaining == [first, second, negative]

    def test_nearest_candidate_is_not_chosen(self) -> None:
        """⛔ 거리가 0일 vs 6일이어도 자동으로 고르지 않는다.

        실측 5건의 오류가 정확히 이 형태였습니다 — 시스템은 0일 차이를 골랐고
        담당자는 6일 차이를 골랐습니다(`DECISIONS.md` §0.6.3.5).
        """
        same_day = _p("17000000", 26, purchase_id=1)
        six_days_earlier = _p("17000000", 20, purchase_id=2)
        negative = _p("-17000000", 26, purchase_id=3)

        result = offset_negative_purchases([same_day, six_days_earlier, negative])

        assert result.pairs == []
        assert len(result.needs_manual_review[0].candidates) == 2

    def test_one_day_difference_is_not_decided_either(self) -> None:
        """⛔ 4일 vs 5일 같은 미세한 차이로도 결정하지 않는다(실측 사례 3)."""
        four_days = _p("368500", 10, purchase_id=1)
        five_days = _p("368500", 1, purchase_id=2)
        negative = _p("-368500", 6, purchase_id=3)

        assert offset_negative_purchases([four_days, five_days, negative]).pairs == []

    def test_matching_description_does_not_decide(self) -> None:
        """⛔ 적요가 같은 후보를 자동으로 고르지 않는다."""
        same_note = _p("100000", 15, description="토너", purchase_id=1)
        other_note = _p("100000", 25, description="토너 (4월분)", purchase_id=2)
        negative = _p("-100000", 20, description="토너", purchase_id=3)

        result = offset_negative_purchases([same_note, other_note, negative])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 1

    def test_blank_budget_account_does_not_decide(self) -> None:
        """⛔ 예산과목이 공란인 후보를 자동으로 고르지 않는다."""
        blank = _p("100000", 15, budget_account=None, purchase_id=1)
        filled = _p("100000", 25, budget_account="외주용역비", purchase_id=2)
        negative = _p("-100000", 20, budget_account=None, purchase_id=3)

        result = offset_negative_purchases([blank, filled, negative])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 1

    def test_contested_candidate_when_negatives_outnumber(self) -> None:
        """양수 1 · 음수 2 → 1:1 이 아니므로 둘 다 담당자 확인 대상이다."""
        positive = _p("100000", 10, purchase_id=1)
        first = _p("-100000", 11, purchase_id=2)
        second = _p("-100000", 25, purchase_id=3)

        result = offset_negative_purchases([positive, first, second])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 2
        assert {group.reason for group in result.needs_manual_review} == {CONTESTED_CANDIDATE}

    def test_equal_counts_above_one_are_also_held(self) -> None:
        """양수 2 · 음수 2 도 자동 상계하지 않는다(후보가 1건이 아니다)."""
        positives = [_p("100000", 1, purchase_id=1), _p("100000", 2, purchase_id=2)]
        negatives = [_p("-100000", 5, purchase_id=3), _p("-100000", 6, purchase_id=4)]

        result = offset_negative_purchases([*positives, *negatives])

        assert result.pairs == []
        assert len(result.needs_manual_review) == 2
        assert result.remaining == [*positives, *negatives]

    def test_holding_does_not_block_a_clear_group(self) -> None:
        """보류 그룹이 있어도 다른 키의 1:1 은 정상 상계된다."""
        held = [_p("100000", 15), _p("100000", 25), _p("-100000", 20)]
        clear = [
            _p("70000", 3, company="B기업", business_no="2222222222"),
            _p("-70000", 4, company="B기업", business_no="2222222222"),
        ]

        result = offset_negative_purchases([*held, *clear])

        assert len(result.pairs) == 1
        assert len(result.needs_manual_review) == 1
        assert result.remaining == held


class TestReviewGroupCarriesWhatTheReviewerNeeds:
    """담당자가 G20 에서 대조할 정보를 그대로 전달한다."""

    def test_identity_fields(self) -> None:
        first = _p("16900", 5, company="엘지전자(주)", business_no="1078614075")
        second = _p("16900", 5, company="엘지전자(주)", business_no="1078614075")
        negative = _p("-16900", 5, company="엘지전자(주)", business_no="1078614075")

        group = offset_negative_purchases([first, second, negative]).needs_manual_review[0]

        assert group.company_name == "엘지전자(주)"
        assert group.business_no == "1078614075"
        assert group.amount == Decimal("16900")
        assert group.negative is negative

    def test_all_candidates_are_listed(self) -> None:
        """후보를 걸러내지 않고 **전부** 전달한다."""
        candidates = [_p("242000", day, purchase_id=day) for day in (1, 10, 20, 26)]
        negative = _p("-242000", 26, purchase_id=99)

        group = offset_negative_purchases([*candidates, negative]).needs_manual_review[0]

        assert group.candidates == candidates

    def test_candidate_lines_show_the_three_reference_fields(self) -> None:
        """발행일자 · 적요 · 예산과목을 나란히 보여 준다(우선순위가 아니다)."""
        first = _p("100000", 20, description="토너", budget_account=None)
        second = _p("100000", 26, description="토너 구입 지출", budget_account="외주용역비")
        negative = _p("-100000", 26, description="토너")

        group = offset_negative_purchases([first, second, negative]).needs_manual_review[0]
        lines = group.candidate_lines()

        assert len(lines) == 2
        assert "2026-03-20" in lines[0]
        assert "토너" in lines[0]
        assert "(공란)" in lines[0]
        assert "외주용역비" in lines[1]

    def test_sibling_negatives_are_reported(self) -> None:
        """같은 조건의 다른 음수도 함께 알려 준다(그룹 전체를 봐야 하는 경우)."""
        positive = _p("805000", 8, purchase_id=1)
        first = _p("-805000", 8, purchase_id=2)
        second = _p("-805000", 9, purchase_id=3)

        groups = offset_negative_purchases([positive, first, second]).needs_manual_review

        by_negative = {group.negative.purchase_id: group for group in groups}
        assert by_negative[2].sibling_negatives == [second]
        assert by_negative[3].sibling_negatives == [first]


class TestNoAutomaticPriorityExists:
    """⛔ 자동 우선순위를 만들 코드가 아예 없다."""

    def test_source_reads_no_reference_field(self) -> None:
        """판정 코드가 발행일자·적요·예산과목을 **비교하지 않는다.**

        표시용 헬퍼(``candidate_lines``)에서만 읽습니다. 판정 함수에서 읽으면
        우선순위가 생기므로, 판정 함수 안에서의 참조를 금지합니다.
        """
        import ast
        import inspect

        import procurement.core.offsetting as module

        for function in (module.offset_negative_purchases, module._match_key):
            tree = ast.parse(inspect.getsource(function))
            attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            for forbidden in (
                "issue_date",
                "description",
                "budget_account",
                "resolution_date",
                "payment_date",
                "contract_date",
            ):
                if forbidden == "issue_date" and function is module.offset_negative_purchases:
                    # missing_issue_date 보고용으로만 읽는다(선택에 쓰지 않는다).
                    continue
                assert forbidden not in attributes, (function.__name__, forbidden)

    def test_no_sorting_or_min_max_in_the_decision(self) -> None:
        """⛔ 판정 함수에 정렬·최소/최대 선택이 없다(순위를 만드는 도구)."""
        import ast
        import inspect

        import procurement.core.offsetting as module

        tree = ast.parse(inspect.getsource(module.offset_negative_purchases))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert not called & {"min", "max", "sorted"}

    def test_source_does_not_reach_for_g20(self) -> None:
        """⛔ G20 로그인·조회·수집 코드가 없다."""
        from pathlib import Path

        import procurement.core.offsetting as module

        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("http", "request", "selenium", "playwright", "login", "urllib"):
            assert forbidden not in source, forbidden


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
        assert result.missing_issue_date == []

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
            "자동 상계 1쌍 · 남은 거래 0건 · 짝 없는 음수 0건 · 담당자 확인 0건 · 발행일자 없음 0건"
        )

    def test_summary_reports_review_count(self) -> None:
        held = [_p("100000", 15), _p("100000", 25), _p("-100000", 20)]
        assert "담당자 확인 1건" in summarize(offset_negative_purchases(held))


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
