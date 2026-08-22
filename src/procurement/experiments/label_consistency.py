"""
procurement.experiments.label_consistency

**과거 확정 이력이 얼마나 일관적인가**를 그룹 단위로 재는 분석기.

한 그룹(같은 적요 등) 안에서 담당자가 늘 같은 유형을 골랐는지, 아니면 판단이
갈렸는지를 셉니다.

::

    총 10건 · 공사 10 · 용역 0 · 물품 0   →  한 유형으로만 확정됨
    총 10건 · 공사  5 · 용역 4 · 물품 1   →  여러 유형으로 갈림

.. warning::
    ⛔ **업무규칙도, 자동 확정 기준도 아닙니다.**

    "일관성이 높으니 자동 확정" 같은 판단을 하지 않습니다. 그런 값을 담는
    필드도 두지 않았습니다.

.. warning::
    ⛔ **점수를 잘라 등급을 매기지 않습니다.**

    일관성 수준은 ``NO_HISTORY`` / ``SINGLE_TYPE`` / ``MIXED_TYPES`` 세 가지로,
    **"이력이 있는가 · 갈렸는가" 라는 사실만** 봅니다. "80% 넘으면 높음" 같은
    임계값이 없습니다 — 그런 경계는 고객 확인 사항입니다. 갈린 **정도**는
    :attr:`GroupConsistency.dominant_ratio` 로 따로 냅니다.

.. note::
    **그룹핑 키를 정하지 않습니다.** 무엇으로 묶을지는 호출자가 넘깁니다
    (``DESCRIPTION_GROUPING_ANALYSIS.md`` — 키는 🔴 결정 대기).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from procurement.core.purchase_type import CONSTRUCTION, GOODS, PURCHASE_TYPE_LABELS, SERVICE
from procurement.experiments.corpus import LabeledExample
from procurement.reviews.past_labels import (
    MIXED_TYPES,
    NO_HISTORY,
    SINGLE_TYPE,
)

#: 보고서에 늘 같은 순서로 싣기 위한 유형 순서.
REPORT_TYPES: tuple[str, ...] = (CONSTRUCTION, SERVICE, GOODS)


def _percent(part: int, whole: int) -> Decimal:
    """백분율. 분모가 0 이면 0."""
    if whole == 0:
        return Decimal("0.00")
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.01"))


@dataclass(frozen=True, kw_only=True)
class GroupConsistency:
    """그룹 하나의 과거 확정 이력 요약.

    Attributes:
        key: 그룹 키(호출자가 만든 값).
        counts: 유형별 확정 건수.
    """

    key: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """확정 건수 합계."""
        return sum(self.counts.values())

    @property
    def type_count(self) -> int:
        """서로 다른 유형의 수."""
        return len([count for count in self.counts.values() if count])

    @property
    def dominant_type(self) -> str | None:
        """최다 유형. 이력이 없으면 ``None``.

        동수일 때는 :data:`REPORT_TYPES` 순서로 정합니다 — 보고서를 재현
        가능하게 만들기 위한 것이며, ⛔ **업무적 우선순위가 아닙니다.**
        """
        present = [(label, count) for label, count in self.counts.items() if count]
        if not present:
            return None
        return max(present, key=lambda pair: (pair[1], -REPORT_TYPES.index(pair[0])))[0]

    @property
    def dominant_label(self) -> str | None:
        """최다 유형의 한글 라벨."""
        dominant = self.dominant_type
        return None if dominant is None else PURCHASE_TYPE_LABELS.get(dominant, dominant)

    @property
    def dominant_count(self) -> int:
        """최다 유형의 건수."""
        dominant = self.dominant_type
        return 0 if dominant is None else self.counts.get(dominant, 0)

    @property
    def dominant_ratio(self) -> Decimal:
        """최다 유형이 차지하는 비율(%).

        ⛔ 여기에 합격선이 없습니다.
        """
        return _percent(self.dominant_count, self.total)

    @property
    def is_mixed(self) -> bool:
        """두 가지 이상으로 갈렸는가."""
        return self.type_count > 1

    @property
    def consistency(self) -> str:
        """:data:`~procurement.reviews.past_labels.CONSISTENCY_LEVELS` 중 하나.

        운영 코드(:class:`~procurement.reviews.past_labels.PastLabelSummary`)와
        **같은 구분**을 씁니다 — 화면 숫자와 분석 숫자가 어긋나지 않도록.
        """
        if self.total == 0:
            return NO_HISTORY
        return SINGLE_TYPE if self.type_count == 1 else MIXED_TYPES

    def count_of(self, purchase_type: str) -> int:
        """특정 유형의 건수."""
        return self.counts.get(purchase_type, 0)

    def row(self) -> dict[str, object]:
        """표·CSV 용 평평한 형태 (지시 4번의 산출 항목)."""
        return {
            "그룹 키": self.key,
            "과거 확정 건수": self.total,
            "공사": self.count_of(CONSTRUCTION),
            "용역": self.count_of(SERVICE),
            "물품": self.count_of(GOODS),
            "최다 유형": self.dominant_label,
            "최다 유형 비율": self.dominant_ratio,
            "유형 수": self.type_count,
            "유형 혼재": self.is_mixed,
            "일관성": self.consistency,
        }


#: :meth:`GroupConsistency.row` 가 내보내는 열 순서.
CONSISTENCY_COLUMNS: tuple[str, ...] = (
    "그룹 키",
    "과거 확정 건수",
    "공사",
    "용역",
    "물품",
    "최다 유형",
    "최다 유형 비율",
    "유형 수",
    "유형 혼재",
    "일관성",
)


@dataclass(frozen=True, kw_only=True)
class ConsistencyReport:
    """그룹 전체에 대한 집계.

    Attributes:
        groups: 그룹별 결과(건수 내림차순).
    """

    groups: tuple[GroupConsistency, ...] = field(default_factory=tuple)

    @property
    def total_groups(self) -> int:
        """그룹 수."""
        return len(self.groups)

    @property
    def total_rows(self) -> int:
        """그룹에 속한 확정 건수 합계."""
        return sum(group.total for group in self.groups)

    @property
    def multi_row_groups(self) -> tuple[GroupConsistency, ...]:
        """2건 이상인 그룹만.

        1건짜리 그룹은 **정의상 항상 일관적**이라 섞어 세면 일관성이
        실제보다 높아 보입니다. 그래서 따로 봅니다.
        """
        return tuple(group for group in self.groups if group.total > 1)

    @property
    def single_type_groups(self) -> tuple[GroupConsistency, ...]:
        """2건 이상이면서 한 유형으로만 확정된 그룹.

        지시 5번의 **"반복형"** 에 해당하는 구조입니다.
        """
        return tuple(group for group in self.multi_row_groups if not group.is_mixed)

    @property
    def mixed_groups(self) -> tuple[GroupConsistency, ...]:
        """유형이 갈린 그룹. 지시 5번의 **"혼합형"** 에 해당합니다."""
        return tuple(group for group in self.multi_row_groups if group.is_mixed)

    def rows_in(self, groups: Sequence[GroupConsistency]) -> int:
        """주어진 그룹들이 담고 있는 행 수."""
        return sum(group.total for group in groups)

    def summary_lines(self) -> tuple[str, ...]:
        """콘솔 요약.

        ⛔ 합격/불합격을 적지 않고, "이 정도면 쓸 만하다" 같은 평가도 하지
        않습니다.
        """
        multi = self.multi_row_groups
        single = self.single_type_groups
        mixed = self.mixed_groups
        return (
            f"그룹 {self.total_groups:,}개 · 확정 {self.total_rows:,}건",
            f"  2건 이상 그룹        {len(multi):,}개 ({self.rows_in(multi):,}행)",
            f"  ├ 한 유형으로만 확정 {len(single):,}개 ({self.rows_in(single):,}행)"
            f" — 지시 5의 '반복형' 구조",
            f"  └ 여러 유형으로 갈림 {len(mixed):,}개 ({self.rows_in(mixed):,}행)"
            f" — 지시 5의 '혼합형' 구조",
            "⛔ 이 구분은 구조적 사실입니다. 어느 쪽을 어떻게 처리할지는 고객 확인 사항입니다.",
        )

    def ratio_buckets(self) -> tuple[tuple[str, int], ...]:
        """갈린 그룹의 **최다 유형 비율 분포**.

        ⚠️ 이 구간들은 **보고용 눈금**이지 판정 기준이 아닙니다. 어디서
        잘라야 하는지를 고객이 실물 분포를 보고 정할 수 있도록, 잘라 놓지
        않고 **분포를 보여줄 뿐**입니다.
        """
        edges = (
            ("50% 이하", Decimal("50")),
            ("50~60%", Decimal("60")),
            ("60~70%", Decimal("70")),
            ("70~80%", Decimal("80")),
            ("80~90%", Decimal("90")),
            ("90~100% 미만", Decimal("100")),
        )
        counter: Counter[str] = Counter()
        for group in self.mixed_groups:
            ratio = group.dominant_ratio
            for name, upper in edges:
                if ratio <= upper:
                    counter[name] += 1
                    break
        return tuple((name, counter.get(name, 0)) for name, _ in edges)


def analyze_consistency(
    examples: Sequence[LabeledExample], keys: Sequence[str]
) -> ConsistencyReport:
    """그룹별 과거 확정 이력 일관성을 산출합니다.

    Args:
        examples: 담당자 확정 사례.
        keys: 사례마다 계산한 그룹 키(``examples`` 와 길이가 같아야 합니다).
            ⛔ 무엇으로 묶을지는 **호출자가 정합니다.**

    Returns:
        :class:`ConsistencyReport`. 건수 내림차순.

    Raises:
        ValueError: 길이가 다른 경우.
    """
    if len(examples) != len(keys):
        raise ValueError("사례 수와 키 수가 다릅니다.")

    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for example, key in zip(examples, keys, strict=True):
        buckets[key][example.purchase_type] += 1

    groups = [GroupConsistency(key=key, counts=dict(counts)) for key, counts in buckets.items()]
    groups.sort(key=lambda group: (-group.total, group.key))
    return ConsistencyReport(groups=tuple(groups))
