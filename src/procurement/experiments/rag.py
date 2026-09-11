"""
procurement.experiments.rag

**RAG(유사 사례 검색)** 방식 후보 생성기 (실험용).

담당자가 확정한 **개별 사례**를 검색해, 가장 비슷한 사례들의 유형을 후보로
냅니다. 근거가 "과거 이 건을 공사로 확정하셨습니다" 형태라 담당자가 가장
납득하기 쉬운 방식입니다.

::

    새 적요  →  유사 사례 k개 검색  →  사례들의 유형을 모아 후보 생성

.. warning::
    ⛔ **규칙을 만들지 않습니다.** 후보의 근거는 전부 사람이 내린 판단입니다.

.. note::
    **검색 방식은 갈아 끼울 수 있습니다.**

    현재 기본값은 **의존성 없는 토큰 코사인 유사도**입니다. 임베딩 모델을 쓰는
    "진짜" RAG 로 바꾸려면 :class:`SimilarityBackend` 를 구현해 넣으면 되고,
    상위 구조는 바뀌지 않습니다.

.. warning::
    🔴 **임베딩 백엔드를 선택하지 않았습니다.**

    외부 임베딩 API 를 쓰면 **구매 데이터가 기관 밖으로 나갑니다.** 공공기관
    데이터 반출 가능 여부는 고객 확인 사항입니다
    (``DESCRIPTION_SIMILARITY_DESIGN.md`` 결정 대기 ⑤).

    로컬 모델을 쓰면 반출 문제는 없으나 새 의존성(수백 MB)과 비결정성이
    생깁니다. 어느 쪽도 임의로 정하지 않았습니다.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Protocol, runtime_checkable

from procurement.experiments.corpus import ClassificationCorpus, LabeledExample, tokenize
from procurement.models.classification import ANALYZED, ClassificationResult, TypeCandidate

#: 참고할 유사 사례 수(기본값). 실험에서 바꿔 가며 비교할 수 있습니다.
DEFAULT_TOP_K = 5

#: 이 값 이하로 비슷하면 "비슷하다" 고 보지 않습니다.
#:
#: ⚠️ 업무 임계값이 **아닙니다.** 0 에 가까운 잡음을 버리기 위한 값이며,
#: 실험 파라미터로 열어 두었습니다.
DEFAULT_MIN_SIMILARITY = 0.0

_QUANT = Decimal("0.0001")


@runtime_checkable
class SimilarityBackend(Protocol):
    """두 적요가 얼마나 비슷한지 재는 방법.

    구현체를 갈아 끼우면 검색 방식이 바뀝니다(토큰 · 임베딩 · 외부 서비스 등).

    Attributes:
        name: 백엔드 이름. 실험 결과에 함께 기록합니다.
    """

    name: str

    def similarity(self, query_tokens: list[str], document_tokens: list[str]) -> float:
        """0 이상 1 이하의 유사도를 반환합니다."""
        ...


class TokenCosineBackend:
    """의존성 없는 기본 백엔드 — 토큰 빈도 코사인 유사도.

    외부 패키지·인터넷 연결 없이 동작하며 **결정적**입니다. 임베딩 백엔드를
    고르기 전까지 비교의 기준선(baseline) 역할을 합니다.
    """

    name = "token-cosine"

    def similarity(self, query_tokens: list[str], document_tokens: list[str]) -> float:
        """두 토큰 목록의 코사인 유사도."""
        if not query_tokens or not document_tokens:
            return 0.0
        left = Counter(query_tokens)
        right = Counter(document_tokens)
        shared = set(left) & set(right)
        if not shared:
            return 0.0
        dot = sum(left[token] * right[token] for token in shared)
        norm = math.sqrt(sum(value * value for value in left.values())) * math.sqrt(
            sum(value * value for value in right.values())
        )
        return dot / norm if norm else 0.0


class RAGClassifier:
    """유사 사례 k개를 검색해 후보를 만듭니다.

    Attributes:
        name: 분석기 이름. 백엔드 이름을 포함해 실험 결과에서 구분됩니다.
        version: 버전.
    """

    version = "1"

    def __init__(
        self,
        corpus: ClassificationCorpus,
        *,
        backend: SimilarityBackend | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> None:
        """분석기를 초기화합니다.

        Args:
            corpus: 담당자 확정 사례.
            backend: 유사도 백엔드. ``None`` 이면 :class:`TokenCosineBackend`.
            top_k: 참고할 유사 사례 수.
            min_similarity: 이 값 이하는 무시합니다.
        """
        self._backend = backend or TokenCosineBackend()
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._entries = corpus.tokenized()
        self.name = f"rag:{self._backend.name}"

    def classify(self, description: str | None) -> ClassificationResult:
        """적요 하나에 대한 후보를 만듭니다.

        Args:
            description: 원본 적요.

        Returns:
            점수 내림차순 :class:`ClassificationResult`. 비슷한 사례가 없으면
            후보 0개.
        """
        query = tokenize(description)
        if not query or not self._entries:
            return self._empty("코퍼스가 비었거나 적요에서 토큰을 얻지 못했습니다.")

        scored: list[tuple[float, LabeledExample]] = []
        for example, tokens in self._entries:
            similarity = self._backend.similarity(query, tokens)
            if similarity > self._min_similarity:
                scored.append((similarity, example))

        if not scored:
            return self._empty("비슷한 확정 사례를 찾지 못했습니다.")

        scored.sort(key=lambda item: (-item[0], item[1].key or ""))
        neighbours = scored[: self._top_k]

        weights: dict[str, float] = defaultdict(float)
        evidence: dict[str, LabeledExample] = {}
        for similarity, example in neighbours:
            weights[example.purchase_type] += similarity
            evidence.setdefault(example.purchase_type, example)

        total = sum(weights.values())
        candidates = [
            TypeCandidate(
                purchase_type=label,
                score=(Decimal(str(weight / total))).quantize(_QUANT),
                evidence=self._evidence(evidence[label], len(neighbours)),
            )
            for label, weight in weights.items()
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
    @staticmethod
    def _evidence(example: LabeledExample, neighbours: int) -> str:
        """담당자가 납득할 수 있는 근거 문장."""
        return f"유사 확정 사례(총 {neighbours}건 참고): “{example.description}”"

    def _empty(self, note: str) -> ClassificationResult:
        """후보 없는 결과."""
        return ClassificationResult(
            candidates=[],
            analyzer_name=self.name,
            analyzer_version=self.version,
            status=ANALYZED,
            note=note,
        )
