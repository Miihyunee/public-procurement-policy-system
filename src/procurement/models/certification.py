"""
procurement.models.certification

Certification 도메인 모델을 정의합니다.

Certification 은 기업이 보유한 정책 인증 정보를 담으며, Company 와 Policy 를
연결하는 핵심 엔티티입니다. 하나의 기업은 여러 개의 인증을 보유할 수 있습니다.
필드 구성은 ``docs/DATABASE_DESIGN.md`` 의 Certification 테이블을 그대로
따릅니다.

.. note::
    본 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 포함하지 않습니다.
    영속화(저장/조회)는
    :class:`procurement.database.certification_repository.CertificationRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(kw_only=True)
class Certification:
    """기업 정책 인증 정보 모델.

    Attributes:
        company_id: Company 테이블 참조 ID (필수).
        policy_id: Policy 테이블 참조 ID (필수).
        valid_from: 인증 시작일 (필수).
        valid_to: 인증 종료일 (필수).
        certificate_number: 인증서 번호 (선택).
        issuing_agency: 발급기관 (선택).
        certification_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    company_id: int
    policy_id: int
    valid_from: date
    valid_to: date
    certificate_number: str | None = None
    issuing_agency: str | None = None
    certification_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
