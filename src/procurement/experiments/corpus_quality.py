"""
procurement.experiments.corpus_quality

코퍼스를 **정답으로 써도 되는가**를 재는 품질 지표.

STEP 4 는 "세 방법 중 무엇이 잘 맞히는가" 를 쟀습니다. 이 모듈은 그 앞 질문을
답니다 — **정답이라고 부르는 값이 정답인가.**

.. warning::
    ⛔ **판정하지 않습니다.** 지표만 냅니다. "이 코퍼스는 쓸 수 있다/없다" 는
    PM·고객이 숫자를 보고 결정합니다.

.. warning::
    ⛔ **자동 확정 임계값을 만들지 않습니다.** "충돌 N건 이하면 통과" 같은 기준을
    두지 않았습니다.

.. note::
    **왜 충돌을 세는가.** 같은 적요가 서로 다른 유형으로 분류되어 있다면, 그
    적요만으로는 유형을 정할 수 없다는 뜻입니다. 분석기가 아무리 좋아도 넘을 수
    없는 천장이며, 그 천장이 곧 :attr:`CorpusQualityReport.deterministic_ceiling`
    입니다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from procurement.experiments.corpus import ClassificationCorpus, LabeledExample, tokenize

#: 공백을 지우고 소문자로 맞춰 "사실상 같은 적요" 를 한 덩어리로 봅니다.
#:
#: ⚠️ 정규화 방식일 뿐 업무규칙이 아닙니다. 원본 적요는 건드리지 않습니다.
_WHITESPACE = re.compile(r"\s+")


def normalize(description: str | None) -> str:
    """비교용으로 적요를 정규화합니다(공백 제거 + 소문자).

    Args:
        description: 원본 적요.

    Returns:
        정규화된 문자열. 입력이 비면 빈 문자열.

    Examples:
        >>> normalize("  LED  교체 공사 ")
        'led교체공사'
    """
    if not description:
        return ""
    return _WHITESPACE.sub("", description).lower()


def jaccard(left: Iterable[str], right: Iterable[str]) -> Decimal:
    """두 토큰 집합의 자카드 유사도(0~1).

    코사인 대신 자카드를 쓰는 이유: "겹치는 표현의 비율" 이 담당자에게 설명하기
    쉽습니다.
    """
    first, second = set(left), set(right)
    if not first or not second:
        return Decimal("0.0000")
    union = len(first | second)
    if not union:
        return Decimal("0.0000")
    return (Decimal(len(first & second)) / Decimal(union)).quantize(Decimal("0.0001"))


@dataclass(frozen=True, kw_only=True)
class ConflictGroup:
    """같은 값인데 유형이 갈린 묶음.

    Attributes:
        key: 묶음을 만든 값(정규화된 적요, 거래처명 등).
        label_counts: 유형별 건수.
        examples: 실제 사례.
    """

    key: str
    label_counts: dict[str, int] = field(default_factory=dict)
    examples: tuple[LabeledExample, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        """묶음의 행 수."""
        return sum(self.label_counts.values())

    @property
    def labels(self) -> tuple[str, ...]:
        """등장한 유형(건수 내림차순)."""
        return tuple(label for label, _ in Counter(self.label_counts).most_common())

    def involves(self, *labels: str) -> bool:
        """지정한 유형들이 **모두** 이 묶음에 있는가.

        공사 ↔ 용역 충돌만 따로 뽑을 때 씁니다.
        """
        return all(label in self.label_counts for label in labels)


@dataclass(frozen=True, kw_only=True)
class SimilarPair:
    """적요는 비슷한데 유형이 다른 두 사례.

    Attributes:
        similarity: 자카드 유사도.
        left: 한쪽 사례.
        right: 다른 쪽 사례.
    """

    similarity: Decimal
    left: LabeledExample
    right: LabeledExample

    def involves(self, *labels: str) -> bool:
        """지정한 유형들이 모두 이 쌍에 있는가."""
        present = {self.left.purchase_type, self.right.purchase_type}
        return all(label in present for label in labels)


@dataclass(frozen=True, kw_only=True)
class LabelProfile:
    """한 유형의 생김새.

    Attributes:
        label: 유형.
        count: 건수.
        unique_descriptions: 고유 적요 수.
        top_tokens: 최빈 토큰(대표 표현).
        top_descriptions: 최빈 적요.
    """

    label: str
    count: int
    unique_descriptions: int
    top_tokens: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    top_descriptions: tuple[tuple[str, int], ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class CorpusQualityReport:
    """코퍼스 품질 지표 묶음.

    Attributes:
        total: 전체 건수.
        label_counts: 유형별 건수.
        unique_descriptions: 고유 적요 수.
        duplicated_descriptions: 2건 이상 등장한 적요 수.
        singleton_descriptions: 한 번만 등장한 적요 수.
        description_conflicts: 동일 적요가 여러 유형으로 분류된 묶음.
        profiles: 유형별 생김새.
        similar_cross_label: 적요가 유사한데 유형이 다른 쌍.
    """

    total: int
    label_counts: dict[str, int] = field(default_factory=dict)
    unique_descriptions: int = 0
    duplicated_descriptions: int = 0
    singleton_descriptions: int = 0
    description_conflicts: tuple[ConflictGroup, ...] = field(default_factory=tuple)
    profiles: tuple[LabelProfile, ...] = field(default_factory=tuple)
    similar_cross_label: tuple[SimilarPair, ...] = field(default_factory=tuple)

    @property
    def conflicting_rows(self) -> int:
        """충돌 묶음에 속한 행 수."""
        return sum(group.size for group in self.description_conflicts)

    @property
    def singleton_ratio(self) -> Decimal:
        """한 번만 등장한 적요의 비율(%).

        이 값이 높으면 **검색으로 맞힐 수 있는 여지가 작습니다.** 과거에 본 적
        없는 표현이 계속 들어온다는 뜻이기 때문입니다.
        """
        return _percent(self.singleton_descriptions, self.unique_descriptions)

    @property
    def deterministic_ceiling(self) -> Decimal:
        """적요만으로 도달 가능한 **정확도 상한**(%).

        각 적요 묶음에서 가장 많은 유형을 전부 맞힌다고 가정한 값입니다. 충돌이
        있으면 100% 가 될 수 없습니다.

        ⚠️ 이것은 목표치가 아니라 **넘을 수 없는 천장**입니다.
        """
        if not self.total:
            return Decimal("0.00")
        unreachable = sum(
            group.size - max(group.label_counts.values()) for group in self.description_conflicts
        )
        return _percent(self.total - unreachable, self.total)

    def conflicts_between(self, *labels: str) -> tuple[ConflictGroup, ...]:
        """지정한 유형들이 함께 나타난 충돌만 추립니다."""
        return tuple(group for group in self.description_conflicts if group.involves(*labels))

    def similar_between(self, *labels: str) -> tuple[SimilarPair, ...]:
        """지정한 유형들 사이의 유사 적요 쌍만 추립니다."""
        return tuple(pair for pair in self.similar_cross_label if pair.involves(*labels))

    def summary_lines(self) -> tuple[str, ...]:
        """콘솔 요약. ⛔ 합격/불합격을 적지 않습니다."""
        lines = [
            f"전체 {self.total:,}건 · 고유 적요 {self.unique_descriptions:,}종",
            "  유형별   " + " · ".join(f"{k} {v:,}" for k, v in self.label_counts.items()),
            f"  중복 적요 {self.duplicated_descriptions:,}종 / 단일 출현 "
            f"{self.singleton_descriptions:,}종 ({self.singleton_ratio}%)",
            f"  동일 적요 유형 충돌 {len(self.description_conflicts):,}종 "
            f"({self.conflicting_rows:,}행)",
            f"  적요만으로 가능한 정확도 상한 {self.deterministic_ceiling}%",
            f"  유형이 다른데 적요가 유사한 쌍 {len(self.similar_cross_label):,}",
        ]
        return tuple(lines)


def _percent(part: int, whole: int) -> Decimal:
    """백분율. 분모가 0 이면 0."""
    if whole == 0:
        return Decimal("0.00")
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.01"))


def group_conflicts(
    examples: Sequence[LabeledExample], key: Sequence[str]
) -> tuple[ConflictGroup, ...]:
    """주어진 키로 묶어, 유형이 갈린 묶음만 돌려줍니다.

    Args:
        examples: 사례 목록.
        key: 사례마다 계산한 묶음 키(``examples`` 와 길이가 같아야 합니다).

    Returns:
        충돌 묶음. 큰 것부터.

    Raises:
        ValueError: 길이가 다른 경우.
    """
    if len(examples) != len(key):
        raise ValueError("사례 수와 키 수가 다릅니다.")

    buckets: dict[str, list[LabeledExample]] = defaultdict(list)
    for example, bucket_key in zip(examples, key, strict=True):
        buckets[bucket_key].append(example)

    groups = [
        ConflictGroup(
            key=bucket_key,
            label_counts=dict(Counter(item.purchase_type for item in items)),
            examples=tuple(items),
        )
        for bucket_key, items in buckets.items()
        if len({item.purchase_type for item in items}) > 1
    ]
    groups.sort(key=lambda group: (-group.size, group.key))
    return tuple(groups)


def find_similar_cross_label(
    examples: Sequence[LabeledExample],
    *,
    minimum: Decimal = Decimal("0.6"),
    limit: int = 200,
) -> tuple[SimilarPair, ...]:
    """유형이 다른데 적요가 유사한 쌍을 찾습니다.

    전수 비교는 O(n²) 이라 큰 코퍼스에서 느립니다. 토큰을 공유하는 사례끼리만
    비교하는 역색인으로 후보를 좁힙니다(결과는 동일).

    Args:
        examples: 사례 목록.
        minimum: 이 값 이상만 반환합니다.
        limit: 최대 반환 개수(유사도 내림차순).

    Returns:
        :class:`SimilarPair` 목록.
    """
    entries = [
        (index, example, set(tokenize(example.description)))
        for index, example in enumerate(examples)
    ]
    index_by_token: dict[str, list[int]] = defaultdict(list)
    for index, _, tokens in entries:
        for token in tokens:
            if len(token) > 1:
                index_by_token[token].append(index)

    seen: set[tuple[int, int]] = set()
    pairs: list[SimilarPair] = []
    for candidates in index_by_token.values():
        for position, left_index in enumerate(candidates):
            for right_index in candidates[position + 1 :]:
                edge = (left_index, right_index)
                if edge in seen:
                    continue
                seen.add(edge)
                _, left, left_tokens = entries[left_index]
                _, right, right_tokens = entries[right_index]
                if left.purchase_type == right.purchase_type:
                    continue
                if normalize(left.description) == normalize(right.description):
                    continue
                score = jaccard(left_tokens, right_tokens)
                if score >= minimum:
                    pairs.append(SimilarPair(similarity=score, left=left, right=right))

    pairs.sort(key=lambda pair: (-pair.similarity, pair.left.description))
    return tuple(pairs[:limit])


def profile_labels(
    examples: Sequence[LabeledExample], *, top: int = 12
) -> tuple[LabelProfile, ...]:
    """유형별 대표 표현을 뽑습니다.

    Args:
        examples: 사례 목록.
        top: 유형마다 뽑을 상위 개수.

    Returns:
        건수 내림차순 :class:`LabelProfile` 목록.
    """
    by_label: dict[str, list[LabeledExample]] = defaultdict(list)
    for example in examples:
        by_label[example.purchase_type].append(example)

    profiles: list[LabelProfile] = []
    for label, items in by_label.items():
        tokens: Counter[str] = Counter()
        for item in items:
            # 한 적요 안에서 같은 낱말이 여러 번 나와도 1 로 센다 —
            # "몇 건에 나타났는가" 가 대표성에 가깝기 때문.
            tokens.update(
                {
                    token
                    for token in tokenize(item.description)
                    if len(token) > 1 and not token.isdigit()
                }
            )
        descriptions = Counter(item.description for item in items)
        profiles.append(
            LabelProfile(
                label=label,
                count=len(items),
                unique_descriptions=len(descriptions),
                top_tokens=tuple(tokens.most_common(top)),
                top_descriptions=tuple(descriptions.most_common(5)),
            )
        )
    profiles.sort(key=lambda profile: (-profile.count, profile.label))
    return tuple(profiles)


def analyze_corpus(
    corpus: ClassificationCorpus,
    *,
    similarity_minimum: Decimal = Decimal("0.6"),
) -> CorpusQualityReport:
    """코퍼스 품질 지표를 한 번에 산출합니다.

    Args:
        corpus: 검사할 코퍼스.
        similarity_minimum: 유사 쌍으로 볼 최소 자카드 유사도.

    Returns:
        :class:`CorpusQualityReport`. ⛔ 합격 여부는 담기지 않습니다.
    """
    examples = list(corpus.examples)
    normalized = [normalize(example.description) for example in examples]
    occurrences = Counter(normalized)

    return CorpusQualityReport(
        total=len(examples),
        label_counts=dict(Counter(example.purchase_type for example in examples).most_common()),
        unique_descriptions=len(occurrences),
        duplicated_descriptions=sum(1 for count in occurrences.values() if count > 1),
        singleton_descriptions=sum(1 for count in occurrences.values() if count == 1),
        description_conflicts=group_conflicts(examples, normalized),
        profiles=profile_labels(examples),
        similar_cross_label=find_similar_cross_label(examples, minimum=similarity_minimum),
    )
