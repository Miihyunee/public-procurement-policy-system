"""
procurement.experiments.corpus

세 분석 방법이 공통으로 쓰는 **학습 자료(코퍼스)** 와 토큰화.

.. warning::
    ⛔ **코퍼스는 담당자가 확정한 사례뿐입니다.**

    손으로 쓴 키워드 목록·예산과목 매핑을 새로 만들지 않습니다. 분류의 근거는
    전부 **사람의 판단**이며, 분석기는 그것을 검색할 뿐입니다.

    코퍼스가 비어 있으면 어떤 분석기도 후보를 만들지 않습니다.

.. warning::
    🔴 **어떤 사례를 코퍼스로 쓸 것인가는 결정 대기입니다.**

    후보는 두 가지입니다.

    1. **DB-2 확정 사례** — 담당자가 이 시스템에서 확정한 건. 초기에는 비어
       있습니다(cold start).
    2. **작업본 시트의 ``구분`` 1,744건** — 담당자의 과거 판단. 바로 쓸 수
       있으나 **역추론 위험**이 있습니다
       (``DESCRIPTION_SIMILARITY_DESIGN.md`` 결정 대기 ④).

    이 모듈은 **어느 쪽도 자동으로 선택하지 않습니다.** 호출자가 명시적으로
    넣어야 합니다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from procurement.core.purchase_type import PURCHASE_TYPES
from procurement.models.review import CONFIRMED, PurchaseReview

#: 공백·구두점으로 끊어 낼 낱말.
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")

#: 문자 n-gram 길이. 한국어는 띄어쓰기가 일정하지 않아 낱말만으로는 잘 걸리지
#: 않으므로 문자 조각도 함께 씁니다.
#:
#: ⚠️ 이것은 **토큰화 방식**이지 업무규칙이 아닙니다. 어떤 적요가 어떤 유형인지
#: 정하지 않으며, 그 판단은 코퍼스(=담당자 확정)에서만 나옵니다.
_NGRAM = 2


class CorpusError(ValueError):
    """코퍼스 값이 규약을 벗어났을 때 발생합니다."""


def tokenize(text: str | None) -> list[str]:
    """적요를 토큰 목록으로 만듭니다.

    낱말과 문자 2-gram 을 함께 냅니다. 한국어 적요는 띄어쓰기가 일정하지 않아
    (``"교육계획서  발송"`` 처럼 공백이 둘인 경우도 있습니다) 낱말만으로는
    비슷한 표현을 놓칩니다.

    Args:
        text: 원본 적요. ``None`` 이거나 공백일 수 있습니다.

    Returns:
        토큰 목록. 입력이 비면 빈 목록.

    Examples:
        >>> tokenize("LED 교체공사")[:3]
        ['led', '교체공사', 'le']
    """
    if not text:
        return []
    lowered = text.lower()
    words = _WORD.findall(lowered)
    tokens = list(words)
    for word in words:
        tokens.extend(word[i : i + _NGRAM] for i in range(len(word) - _NGRAM + 1))
    return tokens


@dataclass(frozen=True, kw_only=True)
class LabeledExample:
    """담당자가 확정한 사례 하나.

    Attributes:
        description: 원본 적요.
        purchase_type: 담당자가 고른 구매유형.
        key: 사례 식별자. 평가 시 자기 자신을 코퍼스에서 빼는 데 씁니다
            (leave-one-out). 보통 ``purchase_id`` 를 넣습니다.
    """

    description: str
    purchase_type: str
    key: str | None = None

    def __post_init__(self) -> None:
        """확정된 3값만 받습니다. ⛔ 새 분류 체계를 만들지 않습니다."""
        if self.purchase_type not in PURCHASE_TYPES:
            allowed = " · ".join(sorted(PURCHASE_TYPES))
            raise CorpusError(
                f"허용되지 않는 구매유형입니다: {self.purchase_type!r} (허용: {allowed})"
            )


@dataclass(frozen=True)
class ClassificationCorpus:
    """담당자 확정 사례 모음.

    Attributes:
        examples: 사례 목록. **비어 있을 수 있습니다** — 그러면 분석기는
            후보를 만들지 않습니다(cold start).
    """

    examples: tuple[LabeledExample, ...] = field(default_factory=tuple)

    @classmethod
    def from_examples(cls, examples: Iterable[LabeledExample]) -> ClassificationCorpus:
        """사례 목록으로 코퍼스를 만듭니다."""
        return cls(tuple(examples))

    @classmethod
    def from_reviews(
        cls, reviews: Iterable[PurchaseReview], descriptions: dict[int, str | None]
    ) -> ClassificationCorpus:
        """DB-2 **확정 사례**로 코퍼스를 만듭니다.

        확정되지 않았거나(``PENDING``) 판단 보류(``final_purchase_type is None``)
        인 건은 **제외**합니다. 사람이 판단하지 않은 것을 학습 자료로 쓰면
        근거가 사라집니다.

        Args:
            reviews: DB-2 검토 상태 목록.
            descriptions: ``{purchase_id: 적요}``. 원본은 DB-1 에 있으므로
                호출자가 함께 넘깁니다(이 모듈은 DB 를 읽지 않습니다).

        Returns:
            :class:`ClassificationCorpus`.
        """
        examples: list[LabeledExample] = []
        for review in reviews:
            if review.review_status != CONFIRMED:
                continue
            if review.final_purchase_type is None:
                continue
            description = descriptions.get(review.purchase_id)
            if not description:
                continue
            examples.append(
                LabeledExample(
                    description=description,
                    purchase_type=review.final_purchase_type,
                    key=str(review.purchase_id),
                )
            )
        return cls(tuple(examples))

    def without(self, key: str | None) -> ClassificationCorpus:
        """특정 사례를 뺀 코퍼스를 돌려줍니다 (leave-one-out).

        평가할 때 자기 자신이 코퍼스에 있으면 **외운 것을 맞히는** 셈이 되어
        성능이 과대평가됩니다. 이전 분석에서 "적요+거래처명 100% 결정력" 이
        실은 85% 가 단일 출현 키였던 전례가 있습니다.

        Args:
            key: 제외할 사례 식별자. ``None`` 이면 그대로 돌려줍니다.
        """
        if key is None:
            return self
        return ClassificationCorpus(
            tuple(example for example in self.examples if example.key != key)
        )

    def label_counts(self) -> dict[str, int]:
        """유형별 사례 수."""
        return dict(Counter(example.purchase_type for example in self.examples))

    def documents_by_label(self) -> dict[str, list[str]]:
        """유형별로 토큰을 합친 문서(BM25 용)."""
        merged: dict[str, list[str]] = {}
        for example in self.examples:
            merged.setdefault(example.purchase_type, []).extend(tokenize(example.description))
        return merged

    def tokenized(self) -> list[tuple[LabeledExample, list[str]]]:
        """사례별 토큰(k-NN 용)."""
        return [(example, tokenize(example.description)) for example in self.examples]

    def __len__(self) -> int:
        """사례 수."""
        return len(self.examples)

    def __bool__(self) -> bool:
        """사례가 하나라도 있는가."""
        return bool(self.examples)


def as_sequence(corpus: ClassificationCorpus) -> Sequence[LabeledExample]:
    """사례 목록을 반환합니다(문서·테스트용)."""
    return corpus.examples
