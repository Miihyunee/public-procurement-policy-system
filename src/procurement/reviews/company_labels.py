"""
procurement.reviews.company_labels

**같은 업체를 과거에 어떻게 확정했는지** 찾아 보여 줍니다.

고객이 *"실제 계약했던 업체명을 검색해서 공사 여부를 판단하기도 한다"* 고
답했습니다(2026-08-25). 담당자가 지금 머릿속이나 다른 파일에서 하는 그 일을
화면으로 옮깁니다.

::

    DB-1 사업자등록번호 ─┐
                         ├→ 사업자등록번호로 묶기 → {유형: 건수}
    DB-2 확정            ─┘

.. note::
    **묶는 키는 사업자등록번호입니다** (2026-08-30 고객 확정 ·
    ``DECISIONS.md`` §0.9.5 원칙 4).

    > 사업자등록번호가 동일하면 동일 업체로 판단한다.

    따라서 `SK브로드밴드주식회사` · `SK브로드밴드(주)` ·
    `에스케이브로드밴드(주)` · `SK브로드밴드` 처럼 **표기가 갈려도 사업자번호가
    같으면 한 업체의 이력**으로 셉니다. 반대로 이름이 같아도 사업자번호가
    다르면 **다른 업체**입니다.

    ⛔ ``(주)`` · ``㈜`` · ``주식회사`` 같은 표기 차이를 판단 기준으로 삼지
    않습니다. 거래처명은 **표시용**이며, 동일 업체 여부를 정하는 키가 아닙니다.

.. warning::
    ⛔ **분류하지 않습니다.**

    과거 기록을 **세어서 보여줄 뿐**, 어떤 유형이 맞다고 말하지 않습니다.
    "이 업체는 공사업체다" · "과거에 공사가 많았으니 공사" 같은 판단을 하지
    않으며, 그런 값을 담는 필드도 두지 않았습니다.

    **사업자번호로 이력을 모으는 것과 구매유형을 자동 판정하는 것은 별개의
    기능입니다.** 과거가 공사 5건 · 용역 8건 · 물품 2건이어도, 현재 건의
    유형은 담당자가 정합니다.

.. warning::
    ⛔ **업체명으로 유형을 판정하지 않습니다.**

    상호에 `건설` · `토건` · `조경건설` 이 들어가면 공사, 같은 규칙을 만들지
    **않았습니다**(§0.9.5 원칙 5 — 고객이 명시적으로 부정했습니다).

.. warning::
    ⛔ **상호변경을 자동으로 판정하지 않습니다.** 사업자번호가 같은데 상호가
    전혀 다르면 이력은 **연결됩니다**(고객 확정 기준 그대로). 그것이 상호변경
    때문인지는 사람이 확인할 일이며, 시스템이 결론짓지 않습니다.

.. note::
    **집계 기준은 :mod:`~procurement.reviews.past_labels` 와 똑같습니다** —
    묶는 키만 적요에서 사업자등록번호로 바뀝니다. 두 블록이 다른 기준으로
    세면 화면의 숫자가 서로 어긋나 담당자가 어느 쪽을 믿어야 할지 알 수
    없게 됩니다.

    ==================== ====================================================
    확정 기준             ``review_status == CONFIRMED`` **이고**
                          ``final_purchase_type`` 이 ``None`` 이 아닌 것
                          (판단 보류는 사람이 결론을 내지 않은 것이라 제외)
    모집단                호출자가 넘겨준 구매 목록. 운영에서는
                          ``find_for_calculation(None)`` = **현재 배치**이며
                          대체된(SUPERSEDED) 배치는 들어오지 않습니다
    현재 행               ⛔ **제외하지 않습니다** — 적요 이력과 같은 규칙입니다
    ==================== ====================================================

.. note::
    ⚠️ **사업자번호를 여기서 정규화하지 않습니다.** 저장된 값이 이미
    정규화(하이픈 제거 10자리)되어 있기 때문입니다 —
    :func:`~procurement.matchers.business_no.normalize_business_no` 가 적재
    시점에 처리하고, ``purchase.business_no`` 는 ``NOT NULL`` 입니다. 여기서
    한 번 더 규칙을 만들면 그것이 곧 두 번째 업무규칙이 됩니다.

    ⛔ 값이 없으면 **그 건은 세지 않습니다.** 거래처명으로 되돌아가 묶지
    않습니다 — 그렇게 하면 고객이 정한 기준이 조용히 무너집니다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from procurement.models.purchase import Purchase
from procurement.models.review import CONFIRMED, PurchaseReview
from procurement.reviews.past_labels import EMPTY_SUMMARY, PastLabel, PastLabelSummary


class CompanyLabelIndex:
    """사업자등록번호 → 과거 확정 유형 분포.

    DB-1(사업자등록번호)과 DB-2(확정)를 **읽기만** 해서 메모리에 색인을
    만듭니다. :class:`~procurement.reviews.past_labels.PastLabelIndex` 와 같은
    구조이며, 묶는 키만 다릅니다.
    """

    def __init__(self, purchases: Iterable[Purchase], reviews: Iterable[PurchaseReview]) -> None:
        """색인을 만듭니다.

        Args:
            purchases: DB-1 구매 목록. **사업자등록번호를 얻는 데만** 씁니다.
                거래처명은 읽지 않습니다.
            reviews: DB-2 검토 상태 목록. ``CONFIRMED`` 이고 판단 보류가
                아닌 것만 반영됩니다.
        """
        business_numbers = {
            purchase.purchase_id: purchase.business_no
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
            key = _key(business_numbers.get(review.purchase_id))
            if not key:
                continue
            counts[key][final_type] += 1

        self._counts = counts

    def summary_for(self, business_no: str | None) -> PastLabelSummary:
        """업체 하나에 대한 과거 확정 이력을 반환합니다.

        **사업자등록번호가 정확히 같은 건**만 셉니다. 거래처명 표기는 보지
        않으므로, 같은 번호에 이름이 여러 가지로 적혀 있어도 하나로 모입니다.

        Args:
            business_no: 저장된 사업자등록번호(정규화된 10자리).

        Returns:
            :class:`~procurement.reviews.past_labels.PastLabelSummary`.
            이력이 없으면 :data:`~procurement.reviews.past_labels.EMPTY_SUMMARY`.
        """
        counts = self._counts.get(_key(business_no))
        if not counts:
            return EMPTY_SUMMARY
        return PastLabelSummary(
            labels=tuple(
                PastLabel(purchase_type=purchase_type, count=count)
                for purchase_type, count in counts.most_common()
            )
        )

    def __len__(self) -> int:
        """이력이 있는 고유 **사업자등록번호** 수."""
        return len(self._counts)


def _key(business_no: str | None) -> str:
    """묶음 키 — **사업자등록번호**.

    ⛔ **정규화하지 않습니다.** 앞뒤 공백만 떼어 냅니다. 저장된 값은 적재
    시점에 이미 하이픈이 제거된 10자리이므로, 여기서 하이픈을 떼거나 자릿수를
    보정하면 그것이 곧 **두 번째 업무규칙**이 됩니다.

    ⛔ 값이 없으면 빈 문자열을 돌려주고, 호출부가 그 건을 **세지 않습니다.**
    거래처명으로 되돌아가지 않습니다.
    """
    return (business_no or "").strip()
