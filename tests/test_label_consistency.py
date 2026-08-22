"""STEP 7 — 과거 확정 이력의 일관성.

두 곳을 검증합니다.

1. **운영** :class:`~procurement.reviews.past_labels.PastLabelSummary` —
   검토 화면이 보여주는 한 적요의 이력
2. **실험** :mod:`procurement.experiments.label_consistency` —
   그룹 전체를 훑는 분석

그리고 무엇보다 **⛔ 이 지표들이 판정으로 넘어가지 않는지**를 고정합니다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal

import pytest

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.experiments import label_consistency
from procurement.experiments.corpus import LabeledExample
from procurement.experiments.label_consistency import (
    CONSISTENCY_COLUMNS,
    ConsistencyReport,
    GroupConsistency,
    analyze_consistency,
)
from procurement.reviews import past_labels as past_labels_module
from procurement.reviews.past_labels import (
    EMPTY_SUMMARY,
    MIXED_TYPES,
    NO_HISTORY,
    SINGLE_TYPE,
    PastLabel,
    PastLabelSummary,
)


def summary(**counts: int) -> PastLabelSummary:
    """건수 내림차순으로 요약을 만든다."""
    ordered = sorted(counts.items(), key=lambda pair: -pair[1])
    return PastLabelSummary(
        labels=tuple(
            PastLabel(purchase_type=label, count=count) for label, count in ordered if count
        )
    )


class TestSummaryWithoutHistory:
    """과거 이력이 없는 경우 — 처음 보는 적요."""

    def test_empty_summary_is_all_zero(self) -> None:
        assert EMPTY_SUMMARY.total == 0
        assert EMPTY_SUMMARY.type_count == 0
        assert EMPTY_SUMMARY.dominant is None
        assert EMPTY_SUMMARY.dominant_ratio == Decimal("0.00")

    def test_consistency_says_no_history(self) -> None:
        """⛔ '일관성 낮음' 이 아니라 **이력이 없다**고 말한다."""
        assert EMPTY_SUMMARY.consistency == NO_HISTORY

    def test_no_conflict_and_no_disagreement(self) -> None:
        """이력이 없으면 충돌도, 불일치도 없다 — 오류가 아니다."""
        assert not EMPTY_SUMMARY.has_conflict
        assert not EMPTY_SUMMARY.differs_from(SERVICE)

    def test_count_of_is_zero(self) -> None:
        assert EMPTY_SUMMARY.count_of(CONSTRUCTION) == 0


class TestSummaryWithOneEntry:
    """과거 이력이 1건인 경우."""

    def test_single_entry_is_single_type(self) -> None:
        one = summary(**{SERVICE: 1})

        assert one.total == 1
        assert one.type_count == 1
        assert one.consistency == SINGLE_TYPE
        assert one.dominant_ratio == Decimal("100.00")

    def test_one_entry_is_not_a_conflict(self) -> None:
        assert not summary(**{SERVICE: 1}).has_conflict

    def test_differs_when_candidate_is_another_type(self) -> None:
        one = summary(**{CONSTRUCTION: 1})

        assert one.differs_from(SERVICE)
        assert not one.differs_from(CONSTRUCTION)


class TestSummaryWithOneTypeOnly:
    """여러 건이지만 유형이 하나뿐인 경우 — 지시 4번의 '10:0:0'."""

    def test_ten_of_the_same_type(self) -> None:
        consistent = summary(**{CONSTRUCTION: 10})

        assert consistent.total == 10
        assert consistent.type_count == 1
        assert consistent.consistency == SINGLE_TYPE
        assert consistent.dominant_ratio == Decimal("100.00")
        assert not consistent.has_conflict

    def test_dominant_is_that_type(self) -> None:
        consistent = summary(**{CONSTRUCTION: 10})
        assert consistent.dominant is not None

        assert consistent.dominant.purchase_type == CONSTRUCTION
        assert consistent.dominant.label == "공사"


class TestSummaryWithMixedTypes:
    """유형이 갈린 경우 — 지시 4번의 '5:4:1'."""

    @staticmethod
    def split() -> PastLabelSummary:
        return summary(**{CONSTRUCTION: 5, SERVICE: 4, GOODS: 1})

    def test_counts_are_kept_per_type(self) -> None:
        split = self.split()

        assert split.total == 10
        assert split.count_of(CONSTRUCTION) == 5
        assert split.count_of(SERVICE) == 4
        assert split.count_of(GOODS) == 1

    def test_consistency_says_mixed(self) -> None:
        assert self.split().consistency == MIXED_TYPES
        assert self.split().has_conflict
        assert self.split().type_count == 3

    def test_dominant_ratio_is_raw(self) -> None:
        """⛔ 등급으로 자르지 않고 비율을 그대로 준다."""
        assert self.split().dominant_ratio == Decimal("50.00")

    def test_a_near_tie_is_still_just_mixed(self) -> None:
        """9:1 도 5:5 도 똑같이 MIXED — 경계선을 만들지 않는다."""
        assert summary(**{SERVICE: 9, GOODS: 1}).consistency == MIXED_TYPES
        assert summary(**{SERVICE: 5, GOODS: 5}).consistency == MIXED_TYPES

    def test_ratio_separates_what_the_level_does_not(self) -> None:
        """수준은 같아도 비율은 다르다 — 판단 재료는 남아 있다."""
        assert summary(**{SERVICE: 9, GOODS: 1}).dominant_ratio == Decimal("90.00")
        assert summary(**{SERVICE: 5, GOODS: 5}).dominant_ratio == Decimal("50.00")


class TestGroupConsistency:
    """실험용 그룹 분석."""

    def test_row_has_every_required_column(self) -> None:
        """지시 4번이 나열한 산출 항목이 모두 있어야 한다."""
        group = GroupConsistency(key="가", counts={CONSTRUCTION: 10})

        assert tuple(group.row()) == CONSISTENCY_COLUMNS

    def test_consistent_group(self) -> None:
        group = GroupConsistency(key="가", counts={CONSTRUCTION: 10})

        assert group.total == 10
        assert group.type_count == 1
        assert not group.is_mixed
        assert group.consistency == SINGLE_TYPE
        assert group.dominant_label == "공사"
        assert group.dominant_ratio == Decimal("100.00")

    def test_split_group(self) -> None:
        group = GroupConsistency(key="나", counts={CONSTRUCTION: 5, SERVICE: 4, GOODS: 1})

        assert group.is_mixed
        assert group.consistency == MIXED_TYPES
        assert group.dominant_ratio == Decimal("50.00")
        assert group.count_of(SERVICE) == 4

    def test_zero_counts_are_not_types(self) -> None:
        """0 건인 유형은 '있는 유형' 으로 세지 않는다."""
        group = GroupConsistency(key="다", counts={CONSTRUCTION: 3, SERVICE: 0})

        assert group.type_count == 1
        assert group.consistency == SINGLE_TYPE

    def test_empty_group(self) -> None:
        group = GroupConsistency(key="라", counts={})

        assert group.total == 0
        assert group.dominant_type is None
        assert group.consistency == NO_HISTORY
        assert group.dominant_ratio == Decimal("0.00")

    def test_tie_is_resolved_deterministically(self) -> None:
        """동수여도 실행할 때마다 결과가 흔들리면 보고서를 믿을 수 없다."""
        group = GroupConsistency(key="마", counts={SERVICE: 3, GOODS: 3})

        assert (
            group.dominant_type
            == GroupConsistency(key="마", counts={GOODS: 3, SERVICE: 3}).dominant_type
        )


class TestAnalyzeConsistency:
    """묶어서 한 번에 산출."""

    @staticmethod
    def examples() -> list[LabeledExample]:
        return [
            LabeledExample(description="통신 회선", purchase_type=SERVICE, key="1"),
            LabeledExample(description="통신 회선", purchase_type=SERVICE, key="2"),
            LabeledExample(description="통신 회선", purchase_type=SERVICE, key="3"),
            LabeledExample(description="시설물 유지관리", purchase_type=CONSTRUCTION, key="4"),
            LabeledExample(description="시설물 유지관리", purchase_type=SERVICE, key="5"),
            LabeledExample(description="혼자 나온 적요", purchase_type=GOODS, key="6"),
        ]

    def report(self) -> ConsistencyReport:
        examples = self.examples()
        return analyze_consistency(examples, [example.description for example in examples])

    def test_groups_are_built_from_the_given_keys(self) -> None:
        """⛔ 그룹핑 키를 모듈이 정하지 않는다 — 호출자가 넘긴 대로 묶는다."""
        report = self.report()

        assert report.total_groups == 3
        assert report.total_rows == 6

    def test_repeating_group_is_single_type(self) -> None:
        """지시 5의 '반복형' 구조."""
        report = self.report()

        assert len(report.single_type_groups) == 1
        assert report.single_type_groups[0].key == "통신 회선"
        assert report.rows_in(report.single_type_groups) == 3

    def test_split_group_is_mixed(self) -> None:
        """지시 5의 '혼합형' 구조."""
        report = self.report()

        assert len(report.mixed_groups) == 1
        assert report.mixed_groups[0].key == "시설물 유지관리"

    def test_single_row_groups_are_excluded_from_the_split(self) -> None:
        """1건짜리는 정의상 일관적이라 섞어 세면 수치가 부풀려진다."""
        report = self.report()

        assert report.total_groups == 3
        assert len(report.multi_row_groups) == 2

    def test_length_mismatch_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="사례 수와 키 수가 다릅니다"):
            analyze_consistency(self.examples(), ["하나뿐인 키"])

    def test_empty_input(self) -> None:
        report = analyze_consistency([], [])

        assert report.total_groups == 0
        assert report.summary_lines()

    def test_summary_does_not_judge(self) -> None:
        """⛔ 요약문이 합격/불합격을 말하지 않는다."""
        text = " ".join(self.report().summary_lines())

        for word in ("합격", "불합격", "충분", "권장", "자동 확정", "쓸 만"):
            assert word not in text

    def test_ratio_buckets_are_reporting_scale_only(self) -> None:
        """분포는 보여주되, 어느 구간이 '좋다' 고 하지 않는다."""
        buckets = self.report().ratio_buckets()

        assert sum(count for _, count in buckets) == len(self.report().mixed_groups)


class TestNoThresholdSnuckIn:
    """⛔ 일관성 지표가 판정으로 넘어가지 않는다."""

    def test_no_verdict_fields(self) -> None:
        """'자동 확정해도 되는가' 를 담는 필드가 없어야 한다."""
        names = {
            name.lower()
            for owner in (GroupConsistency, PastLabelSummary)
            for name in list(owner.__dataclass_fields__) + list(vars(owner))
        }

        for banned in (
            "auto_confirm",
            "is_reliable",
            "can_confirm",
            "needs_review",
            "verdict",
            "recommended",
            "final",
        ):
            assert banned not in names, banned

    def test_consistency_levels_are_structural(self) -> None:
        """수준이 세 가지뿐이고, 전부 '사실' 이지 '등급' 이 아니다."""
        from procurement.reviews.past_labels import CONSISTENCY_LEVELS

        assert set(CONSISTENCY_LEVELS) == {NO_HISTORY, SINGLE_TYPE, MIXED_TYPES}
        for level in CONSISTENCY_LEVELS:
            assert "HIGH" not in level and "LOW" not in level

    def test_no_magic_ratio_comparison_in_the_level(self) -> None:
        """⛔ ``dominant_ratio > 0.8`` 같은 비교로 수준을 정하지 않는다."""
        owners: tuple[type, ...] = (past_labels_module.PastLabelSummary, GroupConsistency)
        for owner in owners:
            # ``consistency`` 는 property 이므로 클래스에서 직접 꺼내 getter 를 본다.
            getter = vars(owner)["consistency"].fget
            source = textwrap.dedent(inspect.getsource(getter))
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Compare):
                    continue
                for operand in [node.left, *node.comparators]:
                    if isinstance(operand, ast.Constant) and isinstance(
                        operand.value, (int, float)
                    ):
                        # 유형 개수 비교(0, 1)만 허용된다.
                        assert operand.value in (0, 1), operand.value

    def test_modules_never_write_a_final_purchase_type(self) -> None:
        for module in (label_consistency, past_labels_module):
            source = inspect.getsource(module)
            assert "final_purchase_type" not in source.replace("review.final_purchase_type", ""), (
                module.__name__
            )
