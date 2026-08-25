"""
procurement.reviews.company_labels

**같은 거래처를 과거에 어떻게 확정했는지** 찾아 보여 줍니다.

고객이 *"실제 계약했던 업체명을 검색해서 공사 여부를 판단하기도 한다"* 고
답했습니다(2026-08-25). 담당자가 지금 머릿속이나 다른 파일에서 하는 그 일을
화면으로 옮깁니다.

::

    DB-1 거래처명 ─┐
                   ├→ 거래처명으로 묶기 → {유형: 건수}
    DB-2 확정      ─┘

.. warning::
    ⛔ **분류하지 않습니다.**

    과거 기록을 **세어서 보여줄 뿐**, 어떤 유형이 맞다고 말하지 않습니다.
    "이 업체는 공사업체다" · "과거에 공사가 많았으니 공사" 같은 판단을 하지
    않으며, 그런 값을 담는 필드도 두지 않았습니다.

.. warning::
    ⛔ **거래처명으로 유형을 판정하지 않습니다.**

    상호에 `건설` · `토건` · `조경건설` 이 들어가면 공사, 같은 규칙을 만들지
    **않았습니다.** 고객이 확인해 준 적이 없습니다.

.. note::
    **기준은 :mod:`~procurement.reviews.past_labels` 와 똑같습니다** — 묶는 키만
    적요에서 거래처명으로 바뀝니다. 두 블록이 다른 기준으로 세면 화면의 숫자가
    서로 어긋나 담당자가 어느 쪽을 믿어야 할지 알 수 없게 됩니다.

    ==================== ====================================================
    확정 기준             ``review_status == CONFIRMED`` **이고**
                          ``final_purchase_type`` 이 ``None`` 이 아닌 것
                          (판단 보류는 사람이 결론을 내지 않은 것이라 제외)
    모집단                호출자가 넘겨준 구매 목록. 운영에서는
                          ``find_for_calculation(None)`` = **현재 배치**이며
                          대체된(SUPERSEDED) 배치는 들어오지 않습니다
    현재 행               ⛔ **제외하지 않습니다** — 적요 이력과 같은 규칙입니다
    ==================== ====================================================

.. warning::
    ⚠️ **거래처명을 정규화하지 않습니다.** 저장된 문자열이 정확히 같은 건만
    셉니다.

    저장소에 거래처명용 정규화 규칙이 **없어서**, 새로 만들면 그것이 곧
    확인받지 않은 업무규칙이 됩니다. 그 대가는 실측으로 확인했습니다(현재 배치
    2,162건 기준).

    - 같은 사업자번호인데 표기가 갈린 거래처 **12종** — 한 사업자번호가
      `SK브로드밴드주식회사` · `SK브로드밴드(주)` · `에스케이브로드밴드(주)` ·
      `SK브로드밴드` **네 가지**로 적힌 사례가 있습니다. 이력이 **나뉘어**
      실제보다 적게 보입니다.
    - 거래처명이 같은데 사업자번호가 다른 경우 **1종**(`(주)케이티`). 서로 다른
      업체의 이력이 **합쳐져** 보입니다.

    어느 쪽으로 묶을지(거래처명 / 사업자번호 / 둘 다)는 **고객 확인 사항**이며
    여기서 정하지 않습니다. 대신 화면이 "무엇을 기준으로 셌는지" 를 밝힙니다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from procurement.models.purchase import Purchase
from procurement.models.review import CONFIRMED, PurchaseReview
from procurement.reviews.past_labels import EMPTY_SUMMARY, PastLabel, PastLabelSummary


class CompanyLabelIndex:
    """거래처명 → 과거 확정 유형 분포.

    DB-1(거래처명)과 DB-2(확정)를 **읽기만** 해서 메모리에 색인을 만듭니다.
    :class:`~procurement.reviews.past_labels.PastLabelIndex` 와 같은 구조이며,
    묶는 키만 다릅니다.
    """

    def __init__(self, purchases: Iterable[Purchase], reviews: Iterable[PurchaseReview]) -> None:
        """색인을 만듭니다.

        Args:
            purchases: DB-1 구매 목록. **거래처명을 얻는 데만** 씁니다.
            reviews: DB-2 검토 상태 목록. ``CONFIRMED`` 이고 판단 보류가
                아닌 것만 반영됩니다.
        """
        names = {
            purchase.purchase_id: purchase.company_name
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
            key = _key(names.get(review.purchase_id))
            if not key:
                continue
            counts[key][final_type] += 1

        self._counts = counts

    def summary_for(self, company_name: str | None) -> PastLabelSummary:
        """거래처 하나에 대한 과거 확정 이력을 반환합니다.

        ⚠️ **정확히 같은 문자열**만 셉니다. 표기가 다르면 다른 거래처로 봅니다.

        Args:
            company_name: 원본 거래처명.

        Returns:
            :class:`~procurement.reviews.past_labels.PastLabelSummary`.
            이력이 없으면 :data:`~procurement.reviews.past_labels.EMPTY_SUMMARY`.
        """
        counts = self._counts.get(_key(company_name))
        if not counts:
            return EMPTY_SUMMARY
        return PastLabelSummary(
            labels=tuple(
                PastLabel(purchase_type=purchase_type, count=count)
                for purchase_type, count in counts.most_common()
            )
        )

    def __len__(self) -> int:
        """이력이 있는 고유 거래처 수."""
        return len(self._counts)


def _key(company_name: str | None) -> str:
    """묶음 키.

    ⛔ **정규화하지 않습니다.** 앞뒤 공백만 떼어 냅니다 — 그것조차 규칙이라기
    보다 저장 과정에서 붙을 수 있는 군더더기이고, ``(주)`` · ``주식회사`` 를
    떼거나 전각을 반각으로 바꾸는 일은 **하지 않습니다.**
    """
    return (company_name or "").strip()
