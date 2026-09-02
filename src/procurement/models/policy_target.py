"""
procurement.models.policy_target

**연도별 · 정책별 목표비율** 모델입니다.

.. warning::
    ⛔ **목표비율의 대상은 구매처가 아닙니다.** 이 모델에 ``company_id`` 나
    사업자등록번호가 없는 것은 빠뜨린 것이 아니라 **의도**입니다
    (``DECISIONS.md`` §0.20).

    목표비율은 *"기관 전체 지출 중 이 정책의 인증기업에 지출한 금액이 차지해야
    하는 비율"* 이며, 축은 **연도 × 정책** 둘뿐입니다.

.. note::
    한 거래처가 여러 정책의 인증을 가지면 그 지출금액은 **각 정책 실적에 모두**
    들어갑니다. 목표비율도 정책마다 따로 두므로 서로 간섭하지 않습니다.

.. note::
    이 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 담지 않습니다. 저장·조회는
    :class:`procurement.database.policy_target_repository.PolicyTargetRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(kw_only=True)
class PolicyTarget:
    """한 연도 · 한 정책의 목표비율.

    Attributes:
        year: 대상 회계연도. 구매의 **결의일자 연도**(``resolution_date.year``)와
            맞춥니다. ⛔ 신고기준일·계약일자·지급일자의 연도가 아닙니다.
        policy_id: 대상 정책 ID (:class:`~procurement.models.policy.Policy` 참조).
        target_rate: 목표 구매비율(%). ``0`` 초과 ``100`` 이하이며, 임의의 값을
            쓸 수 있습니다(예: ``Decimal("37.5")``). ⛔ 화면의 달성률 표시 구간
            (20/40/60/80/100)과는 **다른 값**이며 그 값들로 제한되지 않습니다.
        policy_target_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    year: int
    policy_id: int
    target_rate: Decimal
    policy_target_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
