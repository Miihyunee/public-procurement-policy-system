"""
tests.test_offsetting

음수 거래 상계 판정 검증 — **2026-08-19 고객 확정(정정)**.

    동일 금액 · 동일 기업명 · 동일 사업자등록번호이면 동일 거래로 판단한다.
    **날짜는 상계 조건이 아니다.**

PM 지시 §2 의 검증 항목 6가지를 그대로 고정하고, 선택 기준이 확정되지 않은
다건 후보를 **임의로 상계하지 않는다**는 것을 함께 고정합니다.

.. warning::
    이 로직은 **아직 계산에 연결되어 있지 않습니다.** 현재 Repository 가
    ``amount <= 0`` 저장을 거부하므로 음수 거래가 DB 에 존재할 수 없습니다.
    연결 시점은 PM 결정 사항입니다.
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
    """PM 지시 §2 — 확정된 3조건으로 판정한다."""

    def test_1_same_date_is_offset(self) -> None:
        """① 같은 날짜 + 동일 금액/기업/사업자번호 → 상계."""
        positive = _p("100000", 5)
        negative = _p("-100000", 5)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1
        assert result.remaining == []

    def test_2_different_date_is_offset(self) -> None:
        """② 다른 날짜 + 동일 금액/기업/사업자번호 → 상계."""
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1
        assert result.pairs[0].positive is positive
        assert result.pairs[0].negative is negative
        assert result.remaining == []
        assert result.unmatched_negatives == []

    def test_3_different_amount_is_not_offset(self) -> None:
        """③ 금액이 다르면 상계하지 않는다."""
        positive = _p("100000", 1)
        negative = _p("-200000", 10)

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_4_different_company_name_is_not_offset(self) -> None:
        """④ 기업명이 다르면 상계하지 않는다."""
        positive = _p("100000", 1, company="A기업")
        negative = _p("-100000", 10, company="B기업")

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.remaining == [positive, negative]

    def test_5_different_business_no_is_not_offset(self) -> None:
        """⑤ 사업자등록번호가 다르면 상계하지 않는다."""
        positive = _p("100000", 1, business_no="1111111111")
        negative = _p("-100000", 10, business_no="2222222222")

        result = offset_negative_purchases([positive, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]

    def test_6_positive_below_the_negative_is_still_offset(self) -> None:
        """⑥ 양수가 음수보다 **파일 아래**에 있어도 상계한다.

        .. note::
            **기대값이 바뀐 이유** — 이전 구현에는 "양수가 이전" 조건이 있어
            이 경우를 상계하지 않았습니다. 2026-08-19 고객 확정으로 날짜·순서는
            상계 조건이 아님이 확인되어 업무규칙 자체가 바뀌었습니다.
            실데이터에서 담당자가 표시한 상계 126쌍 중 28쌍이 이 형태입니다.
        """
        negative = _p("-100000", 5, purchase_id=1)
        positive = _p("100000", 5, purchase_id=2)

        result = offset_negative_purchases([negative, positive])

        assert len(result.pairs) == 1
        assert result.remaining == []

    def test_later_positive_is_also_offset(self) -> None:
        """음수보다 **나중 날짜**인 양수도 상계 대상이다(날짜 무관).

        .. note::
            **기대값이 바뀐 이유** — 위와 같습니다. 실데이터 126쌍 중 2쌍이
            이 형태입니다.
        """
        negative = _p("-100000", 1)
        positive = _p("100000", 10)

        result = offset_negative_purchases([negative, positive])

        assert len(result.pairs) == 1
        assert result.remaining == []

    def test_confirmed_match_fields_are_three(self) -> None:
        """판별 요소는 고객 확정 3가지뿐이다 — 날짜가 들어 있지 않다."""
        assert confirmed_match_fields() == ("company_name", "business_no", "abs(amount)")


class TestDateIsNotUsedAtAll:
    """⛔ 날짜를 아예 읽지 않는다."""

    def test_missing_resolution_date_does_not_raise(self) -> None:
        """결의일자가 비어 있어도 판정할 수 있다.

        .. note::
            **기대값이 바뀐 이유** — 이전에는 ``date_of="resolution_date"`` 로
            순서를 가렸기 때문에 값이 없으면 :class:`ValueError` 였습니다.
            날짜가 상계 조건에서 빠지면서 이 제약의 근거가 사라졌습니다.
        """
        positive = _p("100000", 1)
        negative = _p("-100000", 10)
        assert positive.resolution_date is None

        result = offset_negative_purchases([positive, negative])

        assert len(result.pairs) == 1

    def test_function_takes_no_date_argument(self) -> None:
        """``date_of`` 인자가 사라졌다(날짜 의존성 제거)."""
        import inspect

        parameters = inspect.signature(offset_negative_purchases).parameters
        assert list(parameters) == ["purchases"]

    def test_source_does_not_read_date_fields(self) -> None:
        """⛔ 모듈 **코드**가 날짜 필드를 읽지 않는다.

        설명(docstring)에는 "날짜를 쓰지 않는다" 는 문장이 나올 수 있으므로,
        주석·문서를 제외한 **실행되는 이름**만 검사합니다.
        """
        import ast
        from pathlib import Path

        import procurement.core.offsetting as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
        }

        for field_name in ("resolution_date", "payment_date", "contract_date"):
            assert field_name not in names, field_name

    def test_source_does_not_use_getattr_on_purchases(self) -> None:
        """⛔ 날짜 필드를 문자열로 우회 접근하지 않는다."""
        import ast
        from pathlib import Path

        import procurement.core.offsetting as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "getattr" not in called


class TestAmbiguousCandidatesAreHeld:
    """⛔ 후보가 여러 건이면 **임의로 고르지 않고 보류**한다 (PM 지시 §3)."""

    def test_two_positives_one_negative_is_held(self) -> None:
        """양수 2 · 음수 1 → 어느 양수가 남는지 정할 수 없다.

        .. note::
            **기대값이 바뀐 이유** — 이전 구현은 "음수에 가장 가까운 이전 양수"
            라는 순서 규칙으로 자동 선택했습니다. 이 규칙은 고객 확정 사항이
            아니므로 제거하고, 선택 기준이 나올 때까지 보류합니다.
        """
        old = _p("100000", 1, purchase_id=1)
        recent = _p("100000", 8, purchase_id=2)
        negative = _p("-100000", 10, purchase_id=3)

        result = offset_negative_purchases([old, recent, negative])

        assert result.pairs == []
        assert result.unmatched_negatives == []
        assert len(result.ambiguous_groups) == 1
        assert result.remaining == [old, recent, negative]

    def test_held_group_reports_what_the_customer_must_decide(self) -> None:
        """보류 그룹은 판별 키와 후보를 그대로 담는다(보고용)."""
        old = _p("16900", 1, company="엘지전자(주)")
        recent = _p("16900", 8, company="엘지전자(주)")
        negative = _p("-16900", 10, company="엘지전자(주)")

        group = offset_negative_purchases([old, recent, negative]).ambiguous_groups[0]

        assert group.company_name == "엘지전자(주)"
        assert group.business_no == "1234567890"
        assert group.amount == Decimal("16900")
        assert group.positives == [old, recent]
        assert group.negatives == [negative]

    def test_one_positive_two_negatives_is_held(self) -> None:
        """양수 1 · 음수 2 → 어느 음수가 남는지 정할 수 없다.

        .. note::
            **기대값이 바뀐 이유** — 이전에는 1쌍을 맺고 나머지 음수 1건을
            "짝 없음" 으로 보고했습니다. 어느 음수를 짝지을지가 확정 규칙이
            아니므로 보류로 바꿉니다.
        """
        positive = _p("100000", 1)
        first = _p("-100000", 5)
        second = _p("-100000", 10)

        result = offset_negative_purchases([positive, first, second])

        assert result.pairs == []
        assert len(result.ambiguous_groups) == 1

    def test_equal_counts_are_not_held(self) -> None:
        """개수가 같으면 다건이어도 보류하지 않는다 — 남는 거래가 동일하다."""
        positives = [_p("100000", 1, purchase_id=1), _p("100000", 2, purchase_id=2)]
        negatives = [_p("-100000", 5, purchase_id=3), _p("-100000", 6, purchase_id=4)]

        result = offset_negative_purchases([*positives, *negatives])

        assert len(result.pairs) == 2
        assert result.remaining == []
        assert result.ambiguous_groups == []

    def test_no_positive_at_all_is_unmatched_not_held(self) -> None:
        """양수가 아예 없으면 보류가 아니라 '짝 없음' 이다."""
        negative = _p("-100000", 10)

        result = offset_negative_purchases([negative])

        assert result.pairs == []
        assert result.unmatched_negatives == [negative]
        assert result.ambiguous_groups == []
        assert result.remaining == [negative]

    def test_holding_does_not_leak_into_other_keys(self) -> None:
        """보류 그룹이 있어도 다른 키의 자명한 상계는 정상 처리된다."""
        held = [_p("100000", 1), _p("100000", 2), _p("-100000", 3)]
        clear = [
            _p("70000", 4, company="B기업", business_no="2222222222"),
            _p("-70000", 5, company="B기업", business_no="2222222222"),
        ]

        result = offset_negative_purchases([*held, *clear])

        assert len(result.pairs) == 1
        assert len(result.ambiguous_groups) == 1
        assert result.remaining == held


class TestEdgeCases:
    """경계 상황."""

    def test_empty_input(self) -> None:
        result = offset_negative_purchases([])
        assert result.remaining == []
        assert result.pairs == []

    def test_only_positives_are_untouched(self) -> None:
        items = [_p("100000", 1), _p("200000", 2)]
        result = offset_negative_purchases(items)
        assert result.remaining == items

    def test_unrelated_purchases_are_untouched(self) -> None:
        other = _p("500000", 5, company="C기업", business_no="9999999999")
        positive = _p("100000", 1)
        negative = _p("-100000", 10)

        result = offset_negative_purchases([positive, negative, other])

        assert result.remaining == [other]

    def test_zero_amount_is_not_offset(self) -> None:
        """금액 0 인 거래는 상계 대상이 아니라 그대로 남는다."""
        zero = _p("0", 3)

        result = offset_negative_purchases([zero])

        assert result.remaining == [zero]
        assert result.pairs == []

    def test_company_name_whitespace_is_trimmed(self) -> None:
        """앞뒤 공백만 정리한다. 그 밖의 표기 정규화는 하지 않는다."""
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
            "상계 1쌍 · 남은 거래 0건 · 짝 없는 음수 0건 · 기준 미확정 보류 0그룹(0건)"
        )

    def test_summary_reports_held_groups(self) -> None:
        held = [_p("100000", 1), _p("100000", 2), _p("-100000", 3)]
        assert "보류 1그룹(1건)" in summarize(offset_negative_purchases(held))


class TestNotWiredIntoCalculation:
    """이 로직은 아직 계산에 연결되지 않았다 (PM 지시 §5).

    현재 Repository 가 음수 저장을 막고 있어, 연결해도 대상 데이터가 없습니다.
    연결 승인 후 이 테스트를 삭제하고 통합 테스트로 대체합니다.
    """

    def test_repository_still_rejects_negative_amounts(self, tmp_path: object) -> None:
        """음수 구매는 아직 저장 자체가 불가능하다(D-003 · C-2 미해소)."""
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
        """계산기가 아직 상계 로직을 쓰지 않는다."""
        from pathlib import Path

        import procurement.calculators.procurement_achievement as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "offsetting" not in source

    def test_importer_and_upload_do_not_import_offsetting(self) -> None:
        """⛔ 업로드 → DB 경로에도 아직 연결하지 않는다."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for relative in ("uploads", "services", "database"):
            for path in (root / relative).rglob("*.py"):
                assert "offsetting" not in path.read_text(encoding="utf-8"), path
