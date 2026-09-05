"""
procurement.admin.policy_company_source_response

**정책별 기업정보 등록 현황**의 응답 스키마입니다(STEP 96 §2 · §17).

.. warning::
    ⛔ **미등록과 미해당을 섞지 않습니다.** ``registered`` 가 ``False`` 면
    조회불가이며, "해당 기업이 없다" 는 뜻이 아닙니다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

#: 기업정보를 받은 적이 있다.
REGISTERED = "REGISTERED"

#: 기업정보를 받은 적이 없다 → **조회불가**.
NOT_REGISTERED = "NOT_REGISTERED"


class PolicyCompanySourceItemModel(BaseModel):
    """정책 하나의 기업정보 등록 현황.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명. ⛔ 화면이 정책명을 들고 있지 않도록 서버가 줍니다.
        registered: 기업정보를 받은 적이 있는가.
        status: ``REGISTERED`` / ``NOT_REGISTERED``.
        status_label: 화면 표시용 — "등록완료" / "미등록".
        source: 어디서 받았는지(``FILE`` / ``API``). 미등록이면 ``None``.
        source_label: 사용자가 알아볼 출처 표시(파일명 등). 없으면 ``None``.
        company_count: 확인한 기업 수. 미등록이면 ``None``.
        certification_count: 저장한 인증 수. 미등록이면 ``None``.
        updated_at: 최종 등록 시각. 미등록이면 ``None``.
        available_methods: 이 정책에서 **고를 수 있는** 확보 방법.
            ⛔ 실제 조회가 구현되지 않은 정책에는 ``API`` 를 넣지 않습니다
            (STEP 96 §3).
    """

    model_config = ConfigDict(frozen=True)

    policy_id: int
    policy_code: str
    policy_name: str
    registered: bool
    status: str
    status_label: str
    source: str | None = None
    source_label: str | None = None
    company_count: int | None = None
    certification_count: int | None = None
    updated_at: datetime | None = None
    available_methods: list[str] = []


class PolicyCompanySourceListModel(BaseModel):
    """정책별 기업정보 등록 현황 목록.

    Attributes:
        items: 활성 정책 전체. 미등록 정책도 **빼지 않습니다** — 화면이 등록
            버튼을 그리려면 목록 자체가 필요하고, "정책이 없음" 과 "기업정보를
            아직 받지 못함" 은 다른 상태이기 때문입니다.
    """

    model_config = ConfigDict(frozen=True)

    items: list[PolicyCompanySourceItemModel]
