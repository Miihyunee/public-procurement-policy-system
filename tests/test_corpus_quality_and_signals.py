"""STEP 5 — 코퍼스 품질 지표와 검토 원시 신호.

이 테스트가 지키는 것은 두 가지입니다.

1. 지표가 **맞게 계산되는가**
2. ⛔ 지표가 **판정으로 넘어가지 않는가** — 임계값·자동 확정이 생기지 않았는가
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.experiments import corpus_quality, review_signals
from procurement.experiments.bm25 import BM25Classifier
from procurement.experiments.corpus import ClassificationCorpus, LabeledExample
from procurement.experiments.corpus_quality import (
    analyze_corpus,
    find_similar_cross_label,
    group_conflicts,
    jaccard,
    normalize,
    profile_labels,
)
from procurement.experiments.review_signals import SignalCollector


def example(description: str, purchase_type: str, key: str | None = None) -> LabeledExample:
    """짧게 사례를 만든다."""
    return LabeledExample(description=description, purchase_type=purchase_type, key=key)


class TestNormalizeAndJaccard:
    """비교용 정규화와 유사도."""

    def test_normalize_removes_all_whitespace(self) -> None:
        """적요는 띄어쓰기가 일정하지 않다 — '교체 공사' 와 '교체공사' 는 같은 말."""
        assert normalize("  LED  교체 공사 ") == "led교체공사"

    def test_normalize_handles_empty(self) -> None:
        assert normalize(None) == ""
        assert normalize("") == ""

    def test_jaccard_identical_is_one(self) -> None:
        assert jaccard(["가", "나"], ["나", "가"]) == Decimal("1.0000")

    def test_jaccard_disjoint_is_zero(self) -> None:
        assert jaccard(["가"], ["나"]) == Decimal("0.0000")

    def test_jaccard_empty_is_zero(self) -> None:
        """빈 쪽이 있으면 0 — 억지로 비슷하다고 하지 않는다."""
        assert jaccard([], ["가"]) == Decimal("0.0000")

    def test_jaccard_half(self) -> None:
        # 교집합 1, 합집합 3
        assert jaccard(["가", "나"], ["나", "다"]) == Decimal("0.3333")


class TestConflictGrouping:
    """같은 값인데 유형이 갈린 묶음."""

    def test_finds_a_conflict(self) -> None:
        examples = [example("청소", SERVICE), example("청소", GOODS)]
        groups = group_conflicts(examples, ["청소", "청소"])

        assert len(groups) == 1
        assert groups[0].size == 2
        assert set(groups[0].labels) == {SERVICE, GOODS}

    def test_agreeing_group_is_not_a_conflict(self) -> None:
        """같은 유형끼리 여러 건인 것은 충돌이 아니다."""
        examples = [example("청소", SERVICE), example("청소", SERVICE)]

        assert group_conflicts(examples, ["청소", "청소"]) == ()

    def test_involves_selects_specific_labels(self) -> None:
        examples = [example("보수", CONSTRUCTION), example("보수", SERVICE)]
        group = group_conflicts(examples, ["보수", "보수"])[0]

        assert group.involves(CONSTRUCTION, SERVICE)
        assert not group.involves(CONSTRUCTION, GOODS)

    def test_length_mismatch_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="사례 수와 키 수가 다릅니다"):
            group_conflicts([example("가", SERVICE)], ["가", "나"])

    def test_groups_are_ordered_by_size(self) -> None:
        examples = [
            example("가", SERVICE),
            example("가", GOODS),
            example("나", SERVICE),
            example("나", GOODS),
            example("나", CONSTRUCTION),
        ]
        groups = group_conflicts(examples, ["가", "가", "나", "나", "나"])

        assert [group.key for group in groups] == ["나", "가"]


class TestSimilarCrossLabel:
    """유형이 다른데 적요가 비슷한 쌍."""

    def test_finds_a_near_duplicate_across_labels(self) -> None:
        examples = [
            example("기관 홍보 기념품 구매", GOODS),
            example("기관 홍보·기념품 구매", SERVICE),
        ]
        pairs = find_similar_cross_label(examples, minimum=Decimal("0.5"))

        assert len(pairs) == 1
        assert pairs[0].involves(GOODS, SERVICE)

    def test_same_label_is_not_reported(self) -> None:
        """같은 유형끼리 비슷한 것은 문제가 아니다."""
        examples = [example("토너 구매", GOODS), example("토너 구입", GOODS)]

        assert find_similar_cross_label(examples, minimum=Decimal("0.1")) == ()

    def test_identical_descriptions_are_left_to_conflict_grouping(self) -> None:
        """완전히 같은 적요는 충돌 묶음이 다룬다 — 여기서 중복 보고하지 않는다."""
        examples = [example("청소 용역", SERVICE), example("청소  용역", GOODS)]

        assert find_similar_cross_label(examples, minimum=Decimal("0.1")) == ()

    def test_respects_the_limit(self) -> None:
        examples = [
            example(f"사무용품 구매 {index}", GOODS if index % 2 else SERVICE)
            for index in range(10)
        ]
        pairs = find_similar_cross_label(examples, minimum=Decimal("0.1"), limit=3)

        assert len(pairs) == 3


class TestLabelProfiles:
    """유형별 대표 표현."""

    def test_counts_and_orders_by_size(self) -> None:
        examples = [example("청소", SERVICE), example("청소", SERVICE), example("토너", GOODS)]
        profiles = profile_labels(examples)

        assert [profile.label for profile in profiles] == [SERVICE, GOODS]
        assert profiles[0].count == 2
        assert profiles[0].unique_descriptions == 1

    def test_token_is_counted_once_per_description(self) -> None:
        """한 적요 안에서 같은 낱말이 반복돼도 1 로 센다 — 대표성은 '몇 건'이다."""
        profiles = profile_labels([example("청소 청소 청소", SERVICE)])
        tokens = dict(profiles[0].top_tokens)

        assert tokens["청소"] == 1


class TestCorpusQualityReport:
    """전체 지표."""

    def test_counts_duplicates_and_singletons(self) -> None:
        corpus = ClassificationCorpus.from_examples(
            [example("청소", SERVICE), example("청소", SERVICE), example("토너", GOODS)]
        )
        report = analyze_corpus(corpus)

        assert report.total == 3
        assert report.unique_descriptions == 2
        assert report.duplicated_descriptions == 1
        assert report.singleton_descriptions == 1
        assert report.singleton_ratio == Decimal("50.00")

    def test_clean_corpus_has_a_ceiling_of_one_hundred(self) -> None:
        """충돌이 없으면 적요만으로 100% 가 가능하다(원리상)."""
        corpus = ClassificationCorpus.from_examples(
            [example("청소", SERVICE), example("토너", GOODS)]
        )

        assert analyze_corpus(corpus).deterministic_ceiling == Decimal("100.00")

    def test_conflict_lowers_the_ceiling(self) -> None:
        """같은 적요가 갈리면 어떤 분석기도 전부 맞힐 수 없다."""
        corpus = ClassificationCorpus.from_examples(
            [
                example("청소", SERVICE),
                example("청소", SERVICE),
                example("청소", GOODS),
                example("토너", GOODS),
            ]
        )
        report = analyze_corpus(corpus)

        # '청소' 3건 중 최빈 SERVICE 2건만 맞힐 수 있다 → 3/4
        assert report.deterministic_ceiling == Decimal("75.00")
        assert report.conflicting_rows == 3

    def test_empty_corpus_does_not_divide_by_zero(self) -> None:
        report = analyze_corpus(ClassificationCorpus.from_examples([]))

        assert report.total == 0
        assert report.deterministic_ceiling == Decimal("0.00")
        assert report.singleton_ratio == Decimal("0.00")

    def test_can_isolate_construction_service_conflicts(self) -> None:
        """공사 ↔ 용역 충돌만 따로 볼 수 있어야 한다(지시 6번)."""
        corpus = ClassificationCorpus.from_examples(
            [
                example("보수", CONSTRUCTION),
                example("보수", SERVICE),
                example("토너", GOODS),
                example("토너", SERVICE),
            ]
        )
        report = analyze_corpus(corpus)

        assert len(report.description_conflicts) == 2
        assert len(report.conflicts_between(CONSTRUCTION, SERVICE)) == 1

    def test_summary_does_not_judge(self) -> None:
        """⛔ 요약문에 합격/불합격·권고가 없다."""
        corpus = ClassificationCorpus.from_examples([example("청소", SERVICE)])
        text = " ".join(analyze_corpus(corpus).summary_lines())

        for word in ("합격", "불합격", "권장", "사용 가능", "선택"):
            assert word not in text


class TestReviewSignals:
    """검토 원시 신호."""

    @staticmethod
    def corpus() -> ClassificationCorpus:
        return ClassificationCorpus.from_examples(
            [
                example("LED 등기구 교체공사", CONSTRUCTION, "1"),
                example("청소 용역 대금", SERVICE, "2"),
                example("사무용품 구매", GOODS, "3"),
                example("복합기 임차", SERVICE, "4"),
                example("복합기 임차", GOODS, "5"),
            ]
        )

    def test_reports_top_and_runner_up(self) -> None:
        corpus = self.corpus()
        signals = SignalCollector(corpus).collect(BM25Classifier(corpus), "LED 교체공사")

        assert signals.top_type is not None
        assert signals.candidate_count >= 1
        assert signals.method == "bm25"

    def test_score_gap_is_none_with_a_single_candidate(self) -> None:
        """후보가 하나뿐이면 '점수 차' 라는 것이 없다 — 0 이 아니라 없음."""
        corpus = ClassificationCorpus.from_examples([example("청소 용역", SERVICE)])
        signals = SignalCollector(corpus).collect(BM25Classifier(corpus), "청소 용역")

        assert signals.candidate_count == 1
        assert signals.score_gap is None

    def test_score_gap_is_the_difference(self) -> None:
        corpus = self.corpus()
        signals = SignalCollector(corpus).collect(BM25Classifier(corpus), "복합기 임차")

        if signals.score_gap is not None:
            assert signals.top_score is not None
            assert signals.runner_up_score is not None
            assert signals.score_gap == signals.top_score - signals.runner_up_score

    def test_past_conflict_is_surfaced(self) -> None:
        """'복합기 임차' 는 과거에 용역과 물품 양쪽으로 확정된 적이 있다."""
        corpus = self.corpus()
        signals = SignalCollector(corpus).collect(BM25Classifier(corpus), "복합기 임차")

        assert signals.past_label_count == 2
        assert signals.past_conflict

    def test_unseen_description_has_no_history(self) -> None:
        corpus = self.corpus()
        signals = SignalCollector(corpus).collect(BM25Classifier(corpus), "처음 보는 적요")

        assert signals.past_labels == ()
        assert not signals.past_conflict
        assert not signals.disagrees_with_past

    def test_history_ignores_spacing(self) -> None:
        """띄어쓰기가 달라도 같은 적요로 본다."""
        corpus = self.corpus()

        assert SignalCollector(corpus).past_labels("복합기  임차") != ()

    def test_empty_description_is_safe(self) -> None:
        corpus = self.corpus()
        collector = SignalCollector(corpus)

        for value in (None, "", "   "):
            signals = collector.collect(BM25Classifier(corpus), value)
            assert signals.candidate_count == 0
            assert signals.top_type is None
            assert signals.score_gap is None

    def test_collect_many_preserves_order(self) -> None:
        """⛔ 정렬하지 않는다 — 정렬 기준을 정하는 것도 업무 판단이다."""
        corpus = self.corpus()
        descriptions = ["사무용품 구매", "LED 등기구 교체공사", "청소 용역 대금"]
        collected = SignalCollector(corpus).collect_many(BM25Classifier(corpus), descriptions)

        assert [signal.description for signal in collected] == descriptions

    def test_row_has_every_declared_column(self) -> None:
        corpus = self.corpus()
        row = SignalCollector(corpus).collect(BM25Classifier(corpus), "청소 용역 대금").as_row()

        assert tuple(row) == review_signals.SIGNAL_COLUMNS


class TestNoThresholdWasInvented:
    """⛔ STEP 5 에서 자동 확정 기준을 만들지 않았다."""

    def test_signals_have_no_verdict_field(self) -> None:
        """'검토 대상인가' 를 담는 필드가 없어야 한다 — 있으면 곧 기준이 생긴다."""
        fields = set(review_signals.ReviewSignals.__dataclass_fields__)
        properties = {
            name
            for name, value in vars(review_signals.ReviewSignals).items()
            if isinstance(value, property)
        }
        names = {name.lower() for name in fields | properties}

        for banned in ("needs_review", "is_confident", "auto_confirm", "final", "verdict"):
            assert banned not in names, banned

    def test_no_threshold_constants_in_the_modules(self) -> None:
        """모듈 상수에 0.9 · 0.95 같은 판정 임계값이 없어야 한다."""
        for module in (review_signals, corpus_quality):
            for name, value in vars(module).items():
                if name.startswith("_") or not isinstance(value, (int, float, Decimal)):
                    continue
                assert not (Decimal("0.5") < Decimal(str(value)) < Decimal("1")), (
                    f"{module.__name__}.{name} = {value} 가 판정 임계값처럼 보입니다."
                )

    def test_collector_does_not_filter_anything(self) -> None:
        """넣은 건수 = 나온 건수. 하나도 걸러내지 않는다."""
        corpus = TestReviewSignals.corpus()
        descriptions = ["가", "청소 용역 대금", "", "처음 보는 적요"]
        collected = SignalCollector(corpus).collect_many(BM25Classifier(corpus), descriptions)

        assert len(collected) == len(descriptions)

    def test_source_has_no_comparison_against_a_magic_score(self) -> None:
        """⛔ 소스에 ``score > 0.9`` 같은 비교가 없어야 한다 (AST 검사)."""
        for module in (review_signals, corpus_quality):
            tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                for operand in [node.left, *node.comparators]:
                    if isinstance(operand, ast.Constant) and isinstance(
                        operand.value, (int, float)
                    ):
                        assert not (0.5 < float(operand.value) < 1.0), (
                            f"{module.__name__} 에 판정 임계값 비교가 있습니다: {operand.value}"
                        )

    def test_modules_never_write_a_final_purchase_type(self) -> None:
        """⛔ 담당자 확정값을 건드리지 않는다."""
        for module in (review_signals, corpus_quality):
            source = inspect.getsource(module)
            assert "final_purchase_type" not in source


class TestStillIsolated:
    """⛔ STEP 5 모듈도 운영 경로에서 떨어져 있다."""

    def test_new_modules_do_not_import_the_database(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "procurement" / "experiments"

        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert "database" not in name, f"{path.name}: {name}"
                    assert "repository" not in name, f"{path.name}: {name}"

    def test_production_code_does_not_import_the_new_modules(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "procurement"

        for path in root.rglob("*.py"):
            if "experiments" in path.relative_to(root).parts:
                continue
            source = path.read_text(encoding="utf-8")
            assert "corpus_quality" not in source, path.name
            assert "review_signals" not in source, path.name
