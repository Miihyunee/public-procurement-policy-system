"""
procurement.experiments.review_signals

담당자가 **무엇을 먼저 볼지** 판단할 수 있는 **원시 정보**.

STEP 4 에서 BM25 의 ``is_ambiguous`` 가 98~99% 로 나와, 그 값 하나로는 검토
대상을 고를 수 없다는 것이 드러났습니다. 그래서 판정을 내리는 대신 **판단
재료를 그대로 펼쳐 놓습니다.**

.. warning::
    ⛔ **임계값을 만들지 않습니다.**

    "점수 차 0.2 이상이면 확정", "1순위 0.9 이상이면 자동 확정" 같은 기준이
    **없습니다.** 고객 업무규칙이 확정되지 않았으므로 임의로 정하지 않습니다.
    이 모듈은 숫자를 **보여줄 뿐** 어떤 건도 통과시키거나 거르지 않습니다.

.. warning::
    ⛔ **자동 확정 경로가 없습니다.** 결과에 "확정" 을 뜻하는 필드가 없으며,
    담당자 확정값을 읽지도 쓰지도 않습니다.

.. note::
    **왜 신호를 여러 개 내는가.** 하나의 숫자로 압축하면 그 압축 방식 자체가
    업무규칙이 됩니다. 압축하지 않고 나열해, 어떤 신호가 쓸모 있는지를 **고객이
    실물을 보고** 고르게 합니다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from procurement.core.description_classifier import DescriptionClassifier
from procurement.experiments.corpus import ClassificationCorpus
from procurement.experiments.corpus_quality import normalize


@dataclass(frozen=True, kw_only=True)
class PastLabel:
    """과거 같은/비슷한 적요가 어떤 유형으로 확정되었는지.

    Attributes:
        purchase_type: 과거 확정 유형.
        count: 그렇게 확정된 건수.
    """

    purchase_type: str
    count: int


@dataclass(frozen=True, kw_only=True)
class ReviewSignals:
    """건 하나에 대한 **원시 판단 재료**.

    ⛔ "검토 대상인가" 를 담지 않습니다. 담을 수 있는 필드도 두지 않았습니다.

    Attributes:
        description: 원본 적요.
        method: 분석 방법 이름.
        top_type: 1순위 후보 유형.
        top_score: 1순위 점수.
        runner_up_type: 2순위 후보 유형.
        runner_up_score: 2순위 점수.
        candidate_count: 후보 수.
        analyzer_ambiguous: 분석기가 스스로 매긴 애매 여부(참고용).
        past_labels: 동일 적요의 과거 확정 유형 분포.
    """

    description: str
    method: str
    top_type: str | None = None
    top_score: Decimal | None = None
    runner_up_type: str | None = None
    runner_up_score: Decimal | None = None
    candidate_count: int = 0
    analyzer_ambiguous: bool = False
    past_labels: tuple[PastLabel, ...] = field(default_factory=tuple)

    @property
    def score_gap(self) -> Decimal | None:
        """1위와 2위의 점수 차. 후보가 1개 이하면 ``None``.

        ⚠️ 이 값이 작다고 해서 검토 대상이라는 뜻이 **아닙니다.** 기준은 아직
        없습니다.
        """
        if self.top_score is None or self.runner_up_score is None:
            return None
        return self.top_score - self.runner_up_score

    @property
    def past_label_count(self) -> int:
        """과거 같은 적요에 붙은 **서로 다른** 유형의 수."""
        return len(self.past_labels)

    @property
    def past_conflict(self) -> bool:
        """과거 같은 적요가 **여러 유형**으로 확정된 적이 있는가.

        코퍼스 자체가 흔들리는 지점이라, 분석기 점수와 무관하게 사람이 봐야 할
        가능성이 높습니다. 다만 ⛔ 자동으로 걸러내지 않습니다.
        """
        return self.past_label_count > 1

    @property
    def disagrees_with_past(self) -> bool:
        """1순위 후보가 과거 최빈 확정 유형과 다른가.

        ⚠️ "틀렸다" 는 뜻이 아닙니다. 과거 판단과 갈린다는 사실만 알립니다.
        """
        if self.top_type is None or not self.past_labels:
            return False
        return self.top_type != self.past_labels[0].purchase_type

    def as_row(self) -> dict[str, object]:
        """표·CSV 로 내보내기 위한 평평한 형태."""
        return {
            "적요": self.description,
            "분석방법": self.method,
            "1순위": self.top_type,
            "1순위점수": self.top_score,
            "2순위": self.runner_up_type,
            "2순위점수": self.runner_up_score,
            "점수차": self.score_gap,
            "후보수": self.candidate_count,
            "분석기애매": self.analyzer_ambiguous,
            "과거유형수": self.past_label_count,
            "과거충돌": self.past_conflict,
            "과거와불일치": self.disagrees_with_past,
        }


#: :meth:`ReviewSignals.as_row` 가 내보내는 열 순서.
SIGNAL_COLUMNS: tuple[str, ...] = (
    "적요",
    "분석방법",
    "1순위",
    "1순위점수",
    "2순위",
    "2순위점수",
    "점수차",
    "후보수",
    "분석기애매",
    "과거유형수",
    "과거충돌",
    "과거와불일치",
)


class SignalCollector:
    """분석기 결과 + 코퍼스 이력을 합쳐 원시 신호를 만듭니다.

    ⛔ 어떤 건도 통과시키거나 거르지 않습니다. 정렬조차 하지 않습니다 — 정렬
    기준을 정하는 것도 업무 판단이기 때문입니다.
    """

    def __init__(self, corpus: ClassificationCorpus) -> None:
        """수집기를 초기화합니다.

        Args:
            corpus: 과거 확정 사례. 동일 적요 이력을 찾는 데만 씁니다.
        """
        history: dict[str, Counter[str]] = defaultdict(Counter)
        for example in corpus.examples:
            history[normalize(example.description)][example.purchase_type] += 1
        self._history = history

    def past_labels(self, description: str | None) -> tuple[PastLabel, ...]:
        """동일 적요의 과거 확정 유형 분포(건수 내림차순)."""
        counts = self._history.get(normalize(description))
        if not counts:
            return ()
        return tuple(
            PastLabel(purchase_type=label, count=count) for label, count in counts.most_common()
        )

    def collect(self, classifier: DescriptionClassifier, description: str | None) -> ReviewSignals:
        """건 하나의 신호를 모읍니다.

        Args:
            classifier: 사용할 분석기.
            description: 원본 적요.

        Returns:
            :class:`ReviewSignals`.
        """
        result = classifier.classify(description)
        candidates = list(result.candidates)
        top = candidates[0] if candidates else None
        runner_up = candidates[1] if len(candidates) > 1 else None
        return ReviewSignals(
            description=description or "",
            method=classifier.name,
            top_type=top.purchase_type if top else None,
            top_score=top.score if top else None,
            runner_up_type=runner_up.purchase_type if runner_up else None,
            runner_up_score=runner_up.score if runner_up else None,
            candidate_count=len(candidates),
            analyzer_ambiguous=result.is_ambiguous,
            past_labels=self.past_labels(description),
        )

    def collect_many(
        self, classifier: DescriptionClassifier, descriptions: Sequence[str | None]
    ) -> tuple[ReviewSignals, ...]:
        """여러 건의 신호를 모읍니다. 입력 순서를 그대로 유지합니다."""
        return tuple(self.collect(classifier, description) for description in descriptions)
