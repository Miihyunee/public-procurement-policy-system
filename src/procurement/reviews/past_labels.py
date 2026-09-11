"""
procurement.reviews.past_labels

**같은 적요를 과거에 어떻게 확정했는지** 찾아 보여 줍니다.

담당자가 화면에서 판단할 때 가장 알고 싶은 것은 "예전에 나는 이걸 뭐라고
했었나" 입니다. 이 모듈은 그 질문에만 답합니다.

::

    DB-1 적요  ─┐
                ├→ 정규화 키로 묶기 →  {유형: 건수}
    DB-2 확정   ─┘

.. warning::
    ⛔ **분류하지 않습니다.**

    과거 기록을 **세어서 보여줄 뿐**, 어떤 유형이 맞다고 말하지 않습니다.
    "과거에 용역이 많았으니 용역" 같은 판단을 하지 않으며, 그런 값을 담는
    필드도 두지 않았습니다.

.. warning::
    ⛔ **자동 확정 기준이 아닙니다.**

    "과거 5건 이상 같은 유형이면 확정" 같은 임계값이 **없습니다.** 고객
    업무규칙이 확정되지 않았으므로 임의로 만들지 않습니다
    (``DESCRIPTION_CLASSIFICATION_DATA_ANALYSIS.md`` §12).

.. note::
    **확정된 건만 셉니다.** ``PENDING`` 이거나 판단 보류(``None``)인 건은
    사람이 결론을 내지 않은 것이므로 이력에 넣지 않습니다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from procurement.core.description_key import normalize_description
from procurement.core.purchase_type import PURCHASE_TYPE_LABELS
from procurement.models.purchase import Purchase
from procurement.models.review import CONFIRMED, PurchaseReview

#: 과거 확정 이력이 하나도 없음.
NO_HISTORY: Final = "NO_HISTORY"

#: 한 가지 유형으로만 확정된 적이 있음.
SINGLE_TYPE: Final = "SINGLE_TYPE"

#: 두 가지 이상으로 갈린 적이 있음.
MIXED_TYPES: Final = "MIXED_TYPES"

#: 일관성 수준 — **구조적 구분**이며 점수 임계값이 아닙니다.
CONSISTENCY_LEVELS: Final = (NO_HISTORY, SINGLE_TYPE, MIXED_TYPES)


@dataclass(frozen=True, kw_only=True)
class PastLabel:
    """과거 같은 적요가 확정된 유형 하나.

    Attributes:
        purchase_type: 확정 유형 코드.
        count: 그렇게 확정된 건수.
    """

    purchase_type: str
    count: int

    @property
    def label(self) -> str:
        """한글 라벨. 알 수 없는 코드면 코드를 그대로 돌려줍니다."""
        return PURCHASE_TYPE_LABELS.get(self.purchase_type, self.purchase_type)


@dataclass(frozen=True, kw_only=True)
class PastLabelSummary:
    """한 적요에 대한 과거 확정 이력 요약.

    Attributes:
        labels: 유형별 건수(건수 내림차순). 이력이 없으면 빈 목록.
    """

    labels: tuple[PastLabel, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        """과거 확정 건수 합계."""
        return sum(label.count for label in self.labels)

    @property
    def type_count(self) -> int:
        """과거에 붙은 **서로 다른** 유형의 수."""
        return len(self.labels)

    @property
    def has_conflict(self) -> bool:
        """같은 적요가 과거에 **여러 유형**으로 확정된 적이 있는가.

        담당자가 먼저 볼 만한 지점입니다 — 적요만으로는 결론이 안 났던
        자리라는 뜻이기 때문입니다.

        ⛔ 그렇다고 이 건을 자동으로 걸러내거나 막지 않습니다.
        """
        return self.type_count > 1

    @property
    def dominant(self) -> PastLabel | None:
        """가장 많이 확정된 유형. 이력이 없으면 ``None``.

        ⛔ **"정답" 이 아닙니다.** 가장 자주 골랐다는 사실일 뿐입니다.
        """
        return self.labels[0] if self.labels else None

    @property
    def dominant_ratio(self) -> Decimal:
        """최다 유형이 차지하는 비율(%). 이력이 없으면 0.

        100% 면 과거 판단이 한 번도 갈리지 않았다는 뜻이고, 50% 에 가까울수록
        갈렸다는 뜻입니다.

        ⛔ **여기에 기준선이 없습니다.** "80% 넘으면 확정" 같은 임계값을
        만들지 않았습니다 — 고객 업무규칙 미확정.
        """
        dominant = self.dominant
        if dominant is None or self.total == 0:
            return Decimal("0.00")
        return (Decimal(dominant.count) / Decimal(self.total) * 100).quantize(Decimal("0.01"))

    @property
    def consistency(self) -> str:
        """과거 판단이 얼마나 일관됐는지 — :data:`CONSISTENCY_LEVELS` 중 하나.

        **구조적 구분일 뿐 임계값이 아닙니다.** 숫자를 잘라 등급을 매기지
        않고, "이력이 있는가 / 갈렸는가" 라는 사실만 봅니다.

        - :data:`NO_HISTORY` — 과거 확정 이력이 없음
        - :data:`SINGLE_TYPE` — 한 가지 유형으로만 확정됨 (비율 100%)
        - :data:`MIXED_TYPES` — 두 가지 이상으로 갈림

        갈린 **정도**는 :attr:`dominant_ratio` 로 따로 봅니다. 그 값을 등급으로
        자르는 것은 고객 확인 사항입니다.
        """
        if not self.labels:
            return NO_HISTORY
        return SINGLE_TYPE if self.type_count == 1 else MIXED_TYPES

    def count_of(self, purchase_type: str) -> int:
        """특정 유형으로 확정된 건수. 없으면 0."""
        for label in self.labels:
            if label.purchase_type == purchase_type:
                return label.count
        return 0

    def differs_from(self, purchase_type: str | None) -> bool:
        """주어진 유형이 과거 **최빈** 확정 유형과 다른가.

        ⚠️ "틀렸다" 는 뜻이 **아닙니다.** 과거 판단과 갈린다는 사실만
        알립니다.

        Args:
            purchase_type: 비교할 유형. ``None`` 이면 항상 ``False``.
        """
        if purchase_type is None or not self.labels:
            return False
        return purchase_type != self.labels[0].purchase_type


#: 이력이 없는 경우의 빈 요약.
EMPTY_SUMMARY = PastLabelSummary()


class PastLabelIndex:
    """적요 → 과거 확정 유형 분포.

    DB-1(적요)과 DB-2(확정)를 **읽기만** 해서 메모리에 색인을 만듭니다.
    """

    def __init__(self, purchases: Iterable[Purchase], reviews: Iterable[PurchaseReview]) -> None:
        """색인을 만듭니다.

        Args:
            purchases: DB-1 구매 목록. 적요를 얻는 데만 씁니다.
            reviews: DB-2 검토 상태 목록. ``CONFIRMED`` 이고 판단 보류가
                아닌 것만 반영됩니다.
        """
        descriptions = {
            purchase.purchase_id: purchase.description
            for purchase in purchases
            if purchase.purchase_id is not None
        }

        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for review in reviews:
            if review.review_status != CONFIRMED:
                continue
            final_type = review.final_purchase_type
            if final_type is None:
                continue
            key = normalize_description(descriptions.get(review.purchase_id))
            if not key:
                continue
            counts[key][final_type] += 1

        self._counts = counts

    def summary_for(self, description: str | None) -> PastLabelSummary:
        """적요 하나에 대한 과거 확정 이력을 반환합니다.

        Args:
            description: 원본 적요. 띄어쓰기 차이는 무시됩니다.

        Returns:
            :class:`PastLabelSummary`. 이력이 없으면 :data:`EMPTY_SUMMARY`.
        """
        counts = self._counts.get(normalize_description(description))
        if not counts:
            return EMPTY_SUMMARY
        return PastLabelSummary(
            labels=tuple(
                PastLabel(purchase_type=purchase_type, count=count)
                for purchase_type, count in counts.most_common()
            )
        )

    def __len__(self) -> int:
        """이력이 있는 고유 적요 수."""
        return len(self._counts)
