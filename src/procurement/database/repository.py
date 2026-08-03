"""
procurement.database.repository

도메인 Repository 확장을 위한 진입점 모듈입니다.

향후 각 테이블에 대응하는 Repository 를 본 모듈에 구현할 예정입니다.
Repository 는 :class:`procurement.database.base.BaseRepository` 를 상속합니다.

계획된 Repository (DATABASE_DESIGN.md 기준):
    - CompanyRepository        기업 기본정보
    - CertificationRepository  기업 인증정보
    - PurchaseRepository       기관 구매내역
    - PolicyRepository         우선구매 정책 정보
    - DatasetRepository        수집 데이터셋 관리
    - AuditLogRepository       계산 및 변경 이력

.. note::
    테이블 스키마(컬럼/타입/인덱스/제약조건)는 DATABASE_DESIGN v1.1 에서
    확정 후 별도 Issue 에서 구현합니다.
"""

from __future__ import annotations

from procurement.database.base import BaseRepository

__all__ = ["BaseRepository"]
