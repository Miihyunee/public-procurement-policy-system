"""
procurement.experiments.bm25

**BM25** 방식 후보 생성기 (실험용).

유형별로 **담당자가 확정한 적요들을 하나의 문서**로 합치고, 새 적요를 질의로
삼아 Okapi BM25 점수를 냅니다. 점수가 높은 유형이 후보가 됩니다.

.. warning::
    ⛔ **키워드 목록을 만들지 않습니다.**

    "공사 = 시공·교체·설치" 같은 손으로 쓴 사전이 **없습니다.** 어떤 낱말이
    어떤 유형과 관련 있는지는 전적으로 **담당자 확정 사례**에서 나옵니다.
    코퍼스가 비면 후보도 없습니다.

.. note::
    **의존성 없음** — Okapi BM25 는 표준 공식이라 파이썬 표준 라이브러리만으로
    구현됩니다. 외부 패키지·인터넷 연결이 필요하지 않습니다.

.. warning::
    🔴 **이 방법을 선택한 것이 아닙니다.** 비교 대상 셋 중 하나이며, 선택은
    실측 후 PM/고객이 합니다.
"""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal

from procurement.experiments.corpus import ClassificationCorpus, tokenize
from procurement.models.classification import ANALYZED, ClassificationResult, TypeCandidate

#: Okapi BM25 의 관례적 기본값. 검색 분야의 표준값이며 **업무규칙이 아닙니다.**
#: 값을 바꿔 가며 실험할 수 있도록 생성자 인자로 열어 둡니다.
DEFAULT_K1 = 1.5

#: 같은 이유의 관례적 기본값(문서 길이 정규화 강도).
DEFAULT_B = 0.75

#: 점수를 0~1 로 옮길 때 쓰는 소수 자리.
_QUANT = Decimal("0.0001")


class BM25Classifier:
    """유형별 문서에 대한 BM25 검색으로 후보를 만듭니다.

    Attributes:
        name: 분석기 이름(DB-2 에 기록됩니다).
        version: 버전. 파라미터를 바꾸면 함께 바꿉니다.
    """

    name = "bm25"
    version = "1"

    def __init__(
        self,
        corpus: ClassificationCorpus,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        """분석기를 초기화합니다.

        Args:
            corpus: 담당자 확정 사례. 비어 있으면 후보를 만들지 않습니다.
            k1: BM25 포화 파라미터(관례값 1.5).
            b: BM25 길이 정규화 파라미터(관례값 0.75).
        """
        self._k1 = k1
        self._b = b
        self._documents = {
            label: Counter(tokens)
            for label, tokens in corpus.documents_by_label().items()
            if tokens
        }
        self._lengths = {label: sum(counts.values()) for label, counts in self._documents.items()}
        total = len(self._documents)
        self._average_length = sum(self._lengths.values()) / total if total else 0.0
        self._document_frequency = Counter(
            token for counts in self._documents.values() for token in counts
        )
        self._total_documents = total

    def classify(self, description: str | None) -> ClassificationResult:
        """적요 하나에 대한 후보를 만듭니다.

        Args:
            description: 원본 적요.

        Returns:
            점수 내림차순 :class:`ClassificationResult`. 코퍼스가 비었거나
            겹치는 낱말이 없으면 후보 0개.
        """
        query = tokenize(description)
        if not query or not self._documents:
            return self._empty("코퍼스가 비었거나 적요에서 토큰을 얻지 못했습니다.")

        raw = {label: self._score(query, label) for label in self._documents}
        best = max(raw.values())
        if best <= 0:
            return self._empty("확정 사례와 겹치는 표현이 없습니다.")

        candidates = [
            TypeCandidate(
                purchase_type=label,
                # 상대 점수로 0~1 에 맞춘다. ⚠️ 확률이 아니라 **상대 강도**다.
                score=(Decimal(str(score / best))).quantize(_QUANT),
                evidence=self._evidence(query, label),
            )
            for label, score in raw.items()
            if score > 0
        ]
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return ClassificationResult(
            candidates=candidates,
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _score(self, query: list[str], label: str) -> float:
        """질의에 대한 한 유형 문서의 BM25 점수."""
        counts = self._documents[label]
        length = self._lengths[label]
        norm = (
            self._k1 * (1 - self._b + self._b * length / self._average_length)
            if self._average_length
            else self._k1
        )
        score = 0.0
        for token in set(query):
            frequency = counts.get(token, 0)
            if not frequency:
                continue
            df = self._document_frequency[token]
            idf = math.log(1 + (self._total_documents - df + 0.5) / (df + 0.5))
            score += idf * frequency * (self._k1 + 1) / (frequency + norm)
        return score

    def _evidence(self, query: list[str], label: str) -> str:
        """어떤 낱말이 걸렸는지 보여 줍니다(담당자 판단 근거)."""
        counts = self._documents[label]
        matched = sorted(
            {token for token in query if len(token) > 1 and counts.get(token)},
            key=lambda token: counts[token],
            reverse=True,
        )
        if not matched:
            return "확정 사례와 겹치는 표현"
        return "확정 사례와 겹치는 표현: " + ", ".join(matched[:5])

    def _empty(self, note: str) -> ClassificationResult:
        """후보 없는 결과."""
        return ClassificationResult(
            candidates=[],
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
            note=note,
        )
