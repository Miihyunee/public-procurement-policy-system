"""
procurement.models.policy

Policy 도메인 모델을 정의합니다.

Policy 는 시스템에서 지원하는 우선구매 정책(중소기업, 여성기업, 장애인기업,
창업기업, 녹색제품 등) 정보를 담으며, Certification 및 향후 달성률 계산에서
참조됩니다. 필드 구성은 ``docs/DATABASE_DESIGN.md`` 의 Policy 테이블을 그대로
따릅니다.

.. note::
    본 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 포함하지 않습니다.
    영속화(저장/조회)는 :class:`procurement.database.policy_repository.PolicyRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class Policy:
    """우선구매 정책 정보 모델.

    Attributes:
        policy_code: 정책 코드 (Unique, 필수).
        policy_name: 정책명 (필수).
        description: 정책 설명 (선택).
        is_active: 사용 여부 (필수). 기본값은 ``True`` 입니다.
        policy_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    policy_code: str
    policy_name: str
    description: str | None = None
    is_active: bool = True
    policy_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
