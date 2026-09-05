"""
procurement.reviews.rule_classification

**고객이 확정한 규칙만으로** 구매유형을 자동 확정합니다(STEP 122).

::

    지출데이터 적재
          ↓
    확정 규칙 적용  ──→ 규칙에 해당  →  자동 확정 (final_purchase_type)
          ↓
    규칙에 없음     ──→ 담당자 검토 (PENDING)

.. warning::
    ⛔ **추측하지 않습니다.** 쓰는 것은
    :data:`~procurement.core.purchase_type.RULE_CLASSIFIABLE_BUDGET_ACCOUNTS`
    둘뿐이고, **예산과목 완전 일치**로만 봅니다.

    적요·거래처명·금액을 보지 않습니다. 유사도·키워드·임계값·BM25·RAG·FUSE 를
    쓰지 않습니다. 「도서가 들어가면 물품」 같은 부분 문자열 규칙도 없습니다.

    판정 원칙 1·2(``DECISIONS.md`` §0.9.5) — 「적요 낱말 하나만으로 확정하지
    않는다」 · 「예산과목 단독으로도 확정하지 않는다」 — 는 그대로 살아 있습니다.
    이 모듈이 예산과목을 보는 것은 그 둘이 **고객이 따로 확정한 3건 중, 실측에서
    다른 유형이 한 건도 나오지 않은** 항목이기 때문입니다.

.. warning::
    ⛔ **담당자가 이미 고른 것을 덮어쓰지 않습니다.** ``final_purchase_type`` 이
    이미 있는 행은 건너뜁니다. 규칙이 사람의 판단을 지우면 안 됩니다.

.. note::
    자동 확정도 **이력에 남습니다** — 기존 :meth:`ReviewRepository.confirm` 을
    그대로 쓰므로 ``purchase_review_history`` 에 누가(``규칙 자동판정``) 무엇을
    골랐는지 기록되고, 담당자가 검토 화면에서 **다시 바꿀 수 있습니다.**
    ⛔ 새 컬럼을 만들지 않았습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from procurement.core.period import PeriodFilter
from procurement.core.purchase_type import classify_by_confirmed_rule
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository

#: 자동 확정의 확정자 이름. 화면·이력에서 사람이 고른 것과 **구분**됩니다.
#:
#: ⛔ 새 컬럼을 만들지 않고 기존 ``reviewed_by`` 를 씁니다(STEP 122 §7).
RULE_REVIEWER: Final = "규칙 자동판정"

#: 자동 확정에 남기는 메모 앞머리. 어느 규칙이 왜 적용됐는지 남깁니다.
RULE_NOTE_PREFIX: Final = "고객 확정 규칙(예산과목 완전 일치)"


@dataclass(frozen=True, kw_only=True)
class RuleClassificationResult:
    """규칙 적용 결과.

    Attributes:
        examined: 살펴본 구매 건수.
        classified: 규칙으로 **새로 확정한** 건수.
        already_decided: 이미 유형이 있어 건너뛴 건수. ⛔ 덮어쓰지 않았습니다.
        pending: 규칙에 해당하지 않아 **담당자 검토로 남은** 건수.
        by_type: 유형별 확정 건수.
    """

    examined: int = 0
    classified: int = 0
    already_decided: int = 0
    pending: int = 0
    by_type: dict[str, int] | None = None


class RuleClassifier:
    """확정 규칙에 해당하는 구매의 유형을 자동으로 확정합니다.

    Args:
        purchase_repository: 대상 구매를 읽습니다(⛔ 쓰지 않습니다).
        review_repository: 확정을 기록합니다.
    """

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        review_repository: ReviewRepository,
    ) -> None:
        """분류기를 초기화합니다."""
        self._purchases = purchase_repository
        self._reviews = review_repository

    def apply(self, period: PeriodFilter | None = None) -> RuleClassificationResult:
        """계산 대상 구매에 확정 규칙을 적용합니다.

        Args:
            period: 좁힐 기간. ``None`` 이면 전체입니다.

        Returns:
            :class:`RuleClassificationResult`.
        """
        examined = 0
        classified = 0
        already_decided = 0
        pending = 0
        by_type: dict[str, int] = {}

        for purchase in self._purchases.find_for_calculation(period):
            if purchase.purchase_id is None:
                continue
            examined += 1

            existing = self._reviews.find_by_purchase_id(purchase.purchase_id)
            if existing is not None and existing.final_purchase_type is not None:
                # ⛔ 사람이 고른 것도, 앞서 규칙이 정한 것도 덮어쓰지 않는다.
                already_decided += 1
                continue

            purchase_type = classify_by_confirmed_rule(purchase.budget_account)
            if purchase_type is None:
                # 규칙에 없다 → 담당자가 본다. ⛔ 추측해서 채우지 않는다.
                pending += 1
                continue

            self._reviews.confirm(
                purchase.purchase_id,
                final_purchase_type=purchase_type,
                reviewed_by=RULE_REVIEWER,
                review_note=f"{RULE_NOTE_PREFIX}: {purchase.budget_account}",
            )
            classified += 1
            by_type[purchase_type] = by_type.get(purchase_type, 0) + 1

        return RuleClassificationResult(
            examined=examined,
            classified=classified,
            already_decided=already_decided,
            pending=pending,
            by_type=by_type,
        )
