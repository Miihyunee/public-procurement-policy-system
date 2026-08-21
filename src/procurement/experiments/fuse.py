"""
procurement.experiments.fuse

**FUSE(결과 결합)** 방식 후보 생성기 (실험용).

여러 분석기의 결과를 하나로 합칩니다. 자기 자신은 적요를 해석하지 않고,
**다른 분석기들이 낸 순위를 결합**할 뿐입니다.

::

    BM25  ─┐
           ├→  FUSE  →  결합된 후보
    RAG   ─┘

.. note::
    **RRF(Reciprocal Rank Fusion)** 를 씁니다. 점수 척도가 다른 분석기들을
    합칠 때 쓰는 표준 방법으로, **점수 대신 순위**만 보므로 한쪽의 점수
    분포가 다른 쪽을 잡아먹지 않습니다.

.. warning::
    ⛔ **가중치를 업무적으로 정하지 않았습니다.**

    기본값은 모든 분석기에 동일 가중치입니다. "BM25 를 더 믿는다" 같은 판단은
    실측 근거가 없으므로 만들지 않았습니다. 실험에서 바꿔 볼 수 있도록 인자로만
    열어 두었습니다.

.. warning::
    🔴 **이 방법을 선택한 것이 아닙니다.** FUSE 는 하위 방법이 먼저 있어야
    의미가 있으므로, 개별 방법의 실측이 끝난 뒤에 판단합니다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from procurement.core.description_classifier import DescriptionClassifier
from procurement.models.classification import ANALYZED, ClassificationResult, TypeCandidate

#: RRF 상수. 검색 분야의 관례값(60)이며 **업무규칙이 아닙니다.**
#: 값이 클수록 순위 차이의 영향이 줄어듭니다.
DEFAULT_RRF_K = 60

_QUANT = Decimal("0.0001")


class FUSEClassifier:
    """여러 분석기의 순위를 RRF 로 결합합니다.

    Attributes:
        name: 분석기 이름. 결합한 하위 분석기 이름을 포함합니다.
        version: 버전.
    """

    version = "1"

    def __init__(
        self,
        classifiers: Sequence[DescriptionClassifier],
        *,
        rrf_k: int = DEFAULT_RRF_K,
        weights: Sequence[float] | None = None,
    ) -> None:
        """분석기를 초기화합니다.

        Args:
            classifiers: 결합할 분석기들. 비어 있으면 후보를 만들지 않습니다.
            rrf_k: RRF 상수(관례값 60).
            weights: 분석기별 가중치. ``None`` 이면 **모두 동일**합니다.

        Raises:
            ValueError: 가중치 개수가 분석기 수와 다른 경우.
        """
        if weights is not None and len(weights) != len(classifiers):
            raise ValueError("가중치 개수가 분석기 수와 다릅니다.")
        self._classifiers = list(classifiers)
        self._rrf_k = rrf_k
        self._weights = list(weights) if weights is not None else [1.0] * len(classifiers)
        joined = "+".join(classifier.name for classifier in self._classifiers) or "none"
        self.name = f"fuse:{joined}"

    def classify(self, description: str | None) -> ClassificationResult:
        """하위 분석기들의 결과를 결합해 후보를 만듭니다.

        Args:
            description: 원본 적요.

        Returns:
            점수 내림차순 :class:`ClassificationResult`. 하위 분석기가 모두
            후보를 내지 못하면 후보 0개.
        """
        if not self._classifiers:
            return self._empty("결합할 분석기가 없습니다.")

        fused: dict[str, float] = defaultdict(float)
        sources: dict[str, list[str]] = defaultdict(list)

        for classifier, weight in zip(self._classifiers, self._weights, strict=True):
            result = classifier.classify(description)
            for rank, candidate in enumerate(result.candidates, start=1):
                fused[candidate.purchase_type] += weight / (self._rrf_k + rank)
                sources[candidate.purchase_type].append(f"{classifier.name} {rank}순위")

        if not fused:
            return self._empty("하위 분석기가 후보를 내지 못했습니다.")

        total = sum(fused.values())
        candidates = [
            TypeCandidate(
                purchase_type=label,
                score=(Decimal(str(value / total))).quantize(_QUANT),
                evidence=" · ".join(sources[label]),
            )
            for label, value in fused.items()
        ]
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return ClassificationResult(
            candidates=candidates,
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
        )

    def _empty(self, note: str) -> ClassificationResult:
        """후보 없는 결과."""
        return ClassificationResult(
            candidates=[],
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
            note=note,
        )
