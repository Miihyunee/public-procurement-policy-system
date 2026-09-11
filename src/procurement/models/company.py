"""
procurement.models.company

Company 도메인 모델을 정의합니다.

Company 는 모든 정책 인증(Certification)과 구매실적(Purchase)의 기준이 되는
핵심 엔티티이며, 필드 구성은 ``docs/DATABASE_DESIGN.md`` 의 Company 테이블을
그대로 따릅니다.

.. note::
    본 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 포함하지 않습니다.
    영속화(저장/조회)는 :class:`procurement.database.company_repository.CompanyRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class Company:
    """기업 기본정보 모델.

    Attributes:
        business_no: 사업자등록번호 (Unique, 필수).
        company_name: 기업명 (필수).
        representative_name: 대표자명. **선택값입니다** — 없으면 ``None``.

            🟢 2026-09-05 PM 확정: 기업을 식별하는 데 필요한 값은 **기업명과
            사업자등록번호 둘**이며, 대표자명은 선택값이다. 실제 사회적기업
            자료 6,128행 중 1,491행에 대표자명이 없었고, 그 때문에 등록되지
            못한 거래처가 달성/미달 판정을 뒤집었다.

            ⛔ 없는 대표자명을 지어내지 않습니다 — "미상" · "없음" · "-" ·
            "N/A" 같은 값은 전부 시스템이 만들어낸 정보입니다. 빈 값은
            ``None`` 으로 둡니다.
        company_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    business_no: str
    company_name: str
    representative_name: str | None = None
    company_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
