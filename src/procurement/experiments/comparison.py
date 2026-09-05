"""
procurement.experiments.comparison

분석 방법들을 **같은 데이터로 나란히 돌려 비교**하는 실험 실행기.

.. warning::
    ⛔ **DB 에 아무것도 쓰지 않습니다.** 실험은 메모리 안에서만 돌며, DB-1 ·
    DB-2 를 건드리지 않습니다. 담당자 확정값도 그대로입니다.

.. warning::
    ⛔ **승자를 고르지 않습니다.** 이 모듈은 숫자를 내놓을 뿐이며, 어떤 방법을
    쓸지는 PM/고객이 실측을 보고 결정합니다.

.. note::
    **기본은 leave-one-out 평가입니다.**

    평가할 건이 코퍼스에 남아 있으면 **외운 것을 맞히는** 셈이 되어 성능이
    과대평가됩니다. 실제로 이전 분석에서 "적요+거래처명 100% 결정력" 이 실은
    85% 가 단일 출현 키였고, 시간 분할하면 95% 가 예측 불가였던 전례가
    있습니다(``PURCHASE_TYPE_CLASSIFICATION_ANALYSIS.md``).

    비교를 위해 in-sample 결과도 함께 낼 수 있게 해 두었습니다. 두 값의 차이가
    곧 **암기 정도**입니다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from procurement.core.description_classifier import DescriptionClassifier
from procurement.core.description_key import normalize_description
from procurement.experiments.corpus import ClassificationCorpus, LabeledExample

#: 코퍼스를 받아 분석기를 만드는 함수.
#:
#: leave-one-out 평가에서는 사례마다 코퍼스가 달라지므로, 분석기 **인스턴스**가
#: 아니라 **만드는 방법**을 받아야 합니다.
ClassifierFactory = Callable[[ClassificationCorpus], DescriptionClassifier]


@dataclass(frozen=True, kw_only=True)
class CandidateRow:
    """비교표의 후보 한 줄.

    Attributes:
        rank: 후보 순위(1부터).
        purchase_type: 후보 유형.
        score: 후보 점수.
        evidence: 근거.
    """

    rank: int
    purchase_type: str
    score: Decimal
    evidence: str


@dataclass(frozen=True, kw_only=True)
class ItemComparison:
    """건 하나에 대한 한 방법의 결과.

    Attributes:
        key: 사례 식별자.
        description: 원본 적요.
        method: 분석 방법 이름.
        candidates: 후보 목록(순위 포함).
        confirmed_type: **담당자 확정값**. 비교의 기준입니다.
        is_ambiguous: 후보가 갈리는가.
        seen_in_corpus: 이 적요와 **같은 적요**가 코퍼스에 있었는가.
            ``False`` 면 분석기가 **처음 보는 표현**이었다는 뜻입니다
            (지시 11 — 신규 데이터).
    """

    key: str | None
    description: str
    method: str
    candidates: tuple[CandidateRow, ...]
    confirmed_type: str
    is_ambiguous: bool
    seen_in_corpus: bool = False

    @property
    def score_gap(self) -> Decimal | None:
        """1순위와 2순위의 점수 차. 후보가 1개 이하면 ``None``.

        ⛔ 원시 정보입니다. "차이가 크면 확정" 같은 기준은 없습니다.
        """
        if len(self.candidates) < 2:
            return None
        return self.candidates[0].score - self.candidates[1].score

    @property
    def has_evidence(self) -> bool:
        """1순위 후보에 담당자가 읽을 근거가 붙어 있는가(지시 11 — 근거)."""
        return bool(self.candidates and self.candidates[0].evidence.strip())

    @property
    def has_candidate(self) -> bool:
        """후보를 하나라도 냈는가(= 처리 가능한 건)."""
        return bool(self.candidates)

    @property
    def top_type(self) -> str | None:
        """1순위 후보 유형."""
        return self.candidates[0].purchase_type if self.candidates else None

    @property
    def top1_hit(self) -> bool:
        """1순위가 담당자 확정값과 같은가."""
        return self.top_type == self.confirmed_type

    @property
    def any_hit(self) -> bool:
        """후보 **어딘가**에 담당자 확정값이 있는가."""
        return any(row.purchase_type == self.confirmed_type for row in self.candidates)

    @property
    def confirmed_rank(self) -> int | None:
        """담당자 확정값이 몇 순위였는가. 후보에 없으면 ``None``."""
        for row in self.candidates:
            if row.purchase_type == self.confirmed_type:
                return row.rank
        return None


@dataclass(frozen=True, kw_only=True)
class MethodReport:
    """한 방법의 집계 결과.

    Attributes:
        method: 분석 방법 이름.
        items: 건별 결과.
    """

    method: str
    items: tuple[ItemComparison, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        """평가한 건수."""
        return len(self.items)

    @property
    def with_candidate(self) -> int:
        """후보를 낸 건수 (**처리 가능한 건수**)."""
        return sum(1 for item in self.items if item.has_candidate)

    @property
    def without_candidate(self) -> int:
        """후보를 내지 못한 건수."""
        return self.total - self.with_candidate

    @property
    def ambiguous(self) -> int:
        """후보가 갈린 건수 (**애매한 건수**)."""
        return sum(1 for item in self.items if item.is_ambiguous)

    @property
    def top1_hits(self) -> int:
        """1순위가 담당자 확정값과 일치한 건수."""
        return sum(1 for item in self.items if item.top1_hit)

    @property
    def any_hits(self) -> int:
        """후보 안에 담당자 확정값이 들어 있던 건수."""
        return sum(1 for item in self.items if item.any_hit)

    @property
    def coverage(self) -> Decimal:
        """후보를 낸 비율(%)."""
        return self._ratio(self.with_candidate, self.total)

    @property
    def top1_accuracy(self) -> Decimal:
        """**후보를 낸 건 중** 1순위 적중률(%).

        분모를 전체가 아니라 "후보를 낸 건" 으로 두는 이유: 후보를 내지 않은
        것은 틀린 것이 아니라 **판단하지 않은 것**입니다. 둘을 섞으면 "아무
        후보도 안 내는 분석기" 가 불리하게 보입니다. 전체 대비 값은
        :attr:`top1_accuracy_overall` 로 따로 냅니다.
        """
        return self._ratio(self.top1_hits, self.with_candidate)

    @property
    def top1_accuracy_overall(self) -> Decimal:
        """**전체 대비** 1순위 적중률(%)."""
        return self._ratio(self.top1_hits, self.total)

    @property
    def ambiguous_ratio(self) -> Decimal:
        """애매한 건의 비율(%)."""
        return self._ratio(self.ambiguous, self.total)

    @staticmethod
    def _ratio(part: int, whole: int) -> Decimal:
        """백분율. 분모가 0 이면 0."""
        if whole == 0:
            return Decimal("0.00")
        return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.01"))

    # ------------------------------------------------------------------
    # 지시 11 — "정확도" 와 "업무적 유용성" 을 분리한다
    # ------------------------------------------------------------------
    @property
    def evidence_rate(self) -> Decimal:
        """**근거**: 1순위 후보에 설명이 붙은 비율(%).

        담당자가 "왜 이 후보인가" 를 읽을 수 없으면 정확도가 높아도 쓰기
        어렵습니다.
        """
        return self._ratio(sum(1 for item in self.items if item.has_evidence), self.with_candidate)

    @property
    def errors(self) -> int:
        """후보를 냈지만 1순위가 틀린 건수."""
        return sum(1 for item in self.items if item.has_candidate and not item.top1_hit)

    @property
    def flagged_errors(self) -> int:
        """틀렸는데 **애매하다고 표시된** 건수."""
        return sum(
            1
            for item in self.items
            if item.has_candidate and not item.top1_hit and item.is_ambiguous
        )

    @property
    def ambiguity_recall(self) -> Decimal:
        """**애매함(놓침 방지)**: 틀린 건 중 애매하다고 표시한 비율(%).

        높을수록 "담당자가 봐야 할 건" 을 잘 집어냅니다.
        """
        return self._ratio(self.flagged_errors, self.errors)

    @property
    def ambiguity_precision(self) -> Decimal:
        """**애매함(과잉 경보)**: 애매하다고 표시한 것 중 실제로 틀린 비율(%).

        낮으면 거의 모든 건에 경고가 붙어 **신호로서 쓸모가 없습니다.**
        STEP 4 에서 BM25 가 98~99% 를 애매로 표시한 문제가 여기 드러납니다.
        """
        return self._ratio(self.flagged_errors, self.ambiguous)

    @property
    def unseen(self) -> int:
        """**신규 데이터**: 코퍼스에 같은 적요가 없던 건수."""
        return sum(1 for item in self.items if not item.seen_in_corpus)

    @property
    def unseen_accuracy(self) -> Decimal:
        """처음 보는 적요에 대한 1순위 적중률(%).

        운영에서 실제로 만나는 상황에 가장 가까운 숫자입니다.
        """
        unseen = [item for item in self.items if not item.seen_in_corpus]
        return self._ratio(sum(1 for item in unseen if item.top1_hit), len(unseen))

    @property
    def seen_accuracy(self) -> Decimal:
        """과거에 같은 적요가 있던 건의 1순위 적중률(%).

        :attr:`unseen_accuracy` 와의 차이가 곧 **과거 사례 의존도**입니다.
        """
        seen = [item for item in self.items if item.seen_in_corpus]
        return self._ratio(sum(1 for item in seen if item.top1_hit), len(seen))

    @property
    def median_score_gap(self) -> Decimal | None:
        """1·2순위 점수차의 중앙값. 후보가 2개 이상인 건이 없으면 ``None``."""
        gaps = sorted(gap for gap in (item.score_gap for item in self.items) if gap is not None)
        if not gaps:
            return None
        return gaps[len(gaps) // 2]

    def utility_lines(self) -> tuple[str, ...]:
        """지시 11 의 6개 항목을 **정확도와 나란히, 그러나 따로** 보여 줍니다.

        ⛔ 항목을 하나의 점수로 합치지 않습니다. 합치는 방식 자체가 업무
        판단이기 때문입니다.
        """
        gap = self.median_score_gap
        return (
            f"{self.method}",
            f"    정확도      1순위 {self.top1_accuracy}% (후보 낸 건 기준) · "
            f"전체 대비 {self.top1_accuracy_overall}%",
            f"    후보 제시율 {self.coverage}%  ({self.with_candidate}/{self.total})",
            f"    애매함      표시 {self.ambiguous_ratio}% · "
            f"놓침 방지 {self.ambiguity_recall}% · 과잉 경보 아님 {self.ambiguity_precision}%",
            f"    근거        {self.evidence_rate}% 의 1순위에 설명이 붙음",
            f"    데이터 의존 과거 사례 있음 {self.seen_accuracy}% ↔ 없음 {self.unseen_accuracy}%",
            f"    신규 데이터 처음 보는 적요 {self.unseen}건 "
            f"({self._ratio(self.unseen, self.total)}%)",
            f"    점수차      중앙값 {'-' if gap is None else gap}",
        )

    def summary_line(self) -> str:
        """한 줄 요약."""
        return (
            f"{self.method:<28} 후보 있음 {self.with_candidate:>5}/{self.total:<5}"
            f"({self.coverage:>6}%)  1순위 적중 {self.top1_hits:>5}"
            f"({self.top1_accuracy:>6}%)  후보 내 포함 {self.any_hits:>5}"
            f"  애매 {self.ambiguous:>5}({self.ambiguous_ratio:>6}%)"
        )


@dataclass(frozen=True, kw_only=True)
class ComparisonReport:
    """전체 비교 결과.

    Attributes:
        methods: 방법별 결과.
        corpus_size: 사용한 코퍼스 크기.
        leave_one_out: leave-one-out 평가였는가.
    """

    methods: tuple[MethodReport, ...] = field(default_factory=tuple)
    corpus_size: int = 0
    leave_one_out: bool = True

    def table_lines(self) -> tuple[str, ...]:
        """콘솔 표 형태로 반환합니다.

        ⛔ 승자를 표시하지 않습니다. 숫자만 나란히 놓습니다.
        """
        mode = "leave-one-out" if self.leave_one_out else "in-sample(암기 포함)"
        lines = [
            f"코퍼스 {self.corpus_size:,}건 · 평가 방식 {mode}",
            "-" * 108,
        ]
        lines.extend(report.summary_line() for report in self.methods)
        lines.append("-" * 108)
        lines.append("⛔ 이 표는 비교 자료입니다. 방법 선택은 PM/고객 확인 사항입니다.")
        return tuple(lines)

    def rows(self) -> tuple[ItemComparison, ...]:
        """건별 결과를 모두 이어 붙여 반환합니다(상세 비교·CSV 용)."""
        return tuple(item for report in self.methods for item in report.items)


def run_comparison(
    corpus: ClassificationCorpus,
    factories: dict[str, ClassifierFactory],
    *,
    evaluation_set: Sequence[LabeledExample] | None = None,
    leave_one_out: bool = True,
) -> ComparisonReport:
    """여러 방법을 같은 데이터로 돌려 비교합니다.

    Args:
        corpus: 담당자 확정 사례.
        factories: ``{표시이름: 코퍼스를 받아 분석기를 만드는 함수}``.
        evaluation_set: 평가 대상. ``None`` 이면 코퍼스 전체를 씁니다.
        leave_one_out: ``True`` 면 평가할 건을 코퍼스에서 빼고 분석합니다
            (**권장**). ``False`` 면 암기 효과가 섞입니다.

    Returns:
        :class:`ComparisonReport`.

    Examples:
        >>> # report = run_comparison(corpus, {"BM25": BM25Classifier})
        >>> # print("\\n".join(report.table_lines()))
    """
    targets = list(evaluation_set) if evaluation_set is not None else list(corpus.examples)

    # "이 적요가 코퍼스에 있었는가" 를 O(1) 로 답하기 위한 사전 계산.
    # 건마다 집합을 새로 만들면 O(n²) 이 된다.
    corpus_keys = Counter(normalize_description(example.description) for example in corpus.examples)
    corpus_key_by_example = {
        example.key: normalize_description(example.description)
        for example in corpus.examples
        if example.key is not None
    }

    def was_seen(example: LabeledExample) -> bool:
        """평가에 쓰인 코퍼스에 **같은 적요**가 있었는가."""
        key = normalize_description(example.description)
        if not key:
            return False
        occurrences: int = corpus_keys.get(key, 0)
        # leave-one-out 이면 자기 자신은 코퍼스에서 빠져 있다.
        if (
            leave_one_out
            and example.key is not None
            and corpus_key_by_example.get(example.key) == key
        ):
            occurrences -= 1
        return occurrences > 0

    reports: list[MethodReport] = []
    for label, factory in factories.items():
        shared = None if leave_one_out else factory(corpus)
        items: list[ItemComparison] = []

        for example in targets:
            classifier = factory(corpus.without(example.key)) if leave_one_out else shared
            assert classifier is not None
            result = classifier.classify(example.description)
            items.append(
                ItemComparison(
                    key=example.key,
                    description=example.description,
                    method=label,
                    candidates=tuple(
                        CandidateRow(
                            rank=rank,
                            purchase_type=candidate.purchase_type,
                            score=candidate.score,
                            evidence=candidate.evidence,
                        )
                        for rank, candidate in enumerate(result.candidates, start=1)
                    ),
                    confirmed_type=example.purchase_type,
                    is_ambiguous=result.is_ambiguous,
                    seen_in_corpus=was_seen(example),
                )
            )

        reports.append(MethodReport(method=label, items=tuple(items)))

    return ComparisonReport(
        methods=tuple(reports),
        corpus_size=len(corpus),
        leave_one_out=leave_one_out,
    )


def run_segmented_comparison(
    corpus: ClassificationCorpus,
    factories: dict[str, ClassifierFactory],
    segments: dict[str, Sequence[LabeledExample]],
    *,
    leave_one_out: bool = True,
) -> dict[str, ComparisonReport]:
    """평가셋을 **구간별로 나눠** 같은 방법들을 각각 돌립니다.

    전체 정확도 하나로 방법을 고르면 정작 중요한 구간에서 잘못 고를 수
    있습니다. STEP 5 에서 실제로 그런 일이 확인되었습니다 — 전체에서는
    한 방법이 앞섰지만, 공사/용역 판별이 실제로 필요한 구간에서는 순위가
    뒤집혔습니다(``DESCRIPTION_CLASSIFICATION_DATA_ANALYSIS.md`` §9.3).

    Args:
        corpus: 담당자 확정 사례(코퍼스는 구간과 무관하게 공통).
        factories: ``{표시이름: 코퍼스를 받아 분석기를 만드는 함수}``.
        segments: ``{구간 이름: 평가 대상}``. 예) 전체 · 외주용역비 · 공사.
        leave_one_out: 평가할 건을 코퍼스에서 빼고 분석할지 여부.

    Returns:
        ``{구간 이름: ComparisonReport}``. ⛔ 구간별 **승자를 뽑지 않습니다.**
    """
    return {
        name: run_comparison(
            corpus, factories, evaluation_set=examples, leave_one_out=leave_one_out
        )
        for name, examples in segments.items()
    }


def segment_lines(reports: dict[str, ComparisonReport]) -> tuple[str, ...]:
    """구간별 결과를 한 표로 늘어놓습니다.

    ⛔ 승자 표시, 굵게, 별표 같은 강조를 넣지 않습니다. 어느 구간을 중요하게
    볼지는 업무 판단이며 PM/고객의 몫입니다.
    """
    lines: list[str] = []
    for name, report in reports.items():
        lines.append("")
        lines.append(f"[{name}] 평가 {report.methods[0].total if report.methods else 0}건")
        lines.extend(f"  {method.summary_line()}" for method in report.methods)
    lines.append("")
    lines.append("⛔ 구간별 숫자입니다. 어느 구간을 기준으로 고를지는 PM/고객 확인 사항입니다.")
    return tuple(lines)
