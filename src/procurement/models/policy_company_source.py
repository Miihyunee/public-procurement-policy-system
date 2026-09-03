"""
procurement.models.policy_company_source

**정책별 기업정보가 등록되었는가**를 기록하는 모델입니다.

.. warning::
    ⛔ **"등록되지 않았다" 와 "해당 기업이 없다" 는 다릅니다.**

    이 기록이 없으면 그 정책은 **조회불가**입니다 — 어떤 사업자가 그 정책의
    기업인지 판단할 근거 자체가 없기 때문입니다. ⛔ 미해당으로도, 0원으로도
    처리하지 않습니다(STEP 96 §8).

    ::

        기록 있음 + 사업자번호가 목록에 있음   → 해당
        기록 있음 + 사업자번호가 목록에 없음   → 미해당
        기록 없음                              → 조회불가

.. note::
    ``Certification`` 이 0건이어도 이 기록이 있으면 **등록완료**입니다. 목록을
    받았는데 그 안에 우리 거래처가 하나도 없을 수 있고, 그것은 "판단할 수 없다"
    가 아니라 "전부 미해당" 이기 때문입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class PolicyCompanySource:
    """한 정책의 기업정보 등록 기록.

    Attributes:
        policy_id: 대상 정책 ID.
        source: 어디서 받았는지 — ``FILE`` 또는 ``API``. **표시·이력용**이며
            ⛔ 판정에 쓰이지 않습니다. 두 방법의 결과는 같습니다.
        company_count: 이번 등록에서 확인한 기업 수(신규 + 기존).
        certification_count: 이번 등록에서 새로 저장한 인증 수.
        source_label: 사용자가 알아볼 출처 표시(파일명 등). 없으면 ``None``.
        policy_company_source_id: 내부 고유 ID. 저장 전에는 ``None``.
        registered_at: 등록 시각.
        updated_at: 최종 갱신 시각.
    """

    policy_id: int
    source: str
    company_count: int = 0
    certification_count: int = 0
    source_label: str | None = None
    policy_company_source_id: int | None = None
    registered_at: datetime | None = None
    updated_at: datetime | None = None
