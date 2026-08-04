"""
procurement.models.purchase

Purchase 도메인 모델을 정의합니다.

Purchase 는 기관의 구매실적을 담으며, 이후 기업 매칭(Matcher)과 정책 달성률
계산(Calculator)의 입력 데이터로 사용됩니다. 필드 구성은
``docs/DATABASE_DESIGN.md`` 의 Purchase 테이블을 그대로 따릅니다.

.. note::
    본 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 포함하지 않습니다.
    영속화(저장/조회)는
    :class:`procurement.database.purchase_repository.PurchaseRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(kw_only=True)
class Purchase:
    """기관 구매실적 모델.

    Attributes:
        business_no: 사업자등록번호 (필수). 중복될 수 있습니다.
        company_name: 공급업체명 (필수).
        purchase_date: 구매일 (필수).
        amount: 구매금액 (필수). 0 보다 커야 합니다.
        company_id: Company 테이블 참조 ID. 매칭 후 저장되므로 기본값은 ``None`` 입니다.
        purchase_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    business_no: str
    company_name: str
    purchase_date: date
    amount: Decimal
    company_id: int | None = None
    purchase_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
