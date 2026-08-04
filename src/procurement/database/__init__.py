"""
procurement.database

데이터베이스 접근 계층 패키지.

연결 관리와 Repository 기반/도메인 클래스를 제공합니다::

    from procurement.database import get_connection, BaseRepository, CompanyRepository

    with get_connection() as conn:
        conn.execute("SELECT 1")
"""

from procurement.database.base import BaseRepository
from procurement.database.certification_repository import (
    CertificationRepository,
    CertificationValidationError,
)
from procurement.database.company_repository import (
    CompanyRepository,
    CompanyValidationError,
    DuplicateBusinessNoError,
)
from procurement.database.connection import create_connection, get_connection
from procurement.database.policy_repository import (
    DuplicatePolicyCodeError,
    PolicyRepository,
    PolicyValidationError,
)
from procurement.database.purchase_repository import (
    PurchaseRepository,
    PurchaseValidationError,
)

__all__ = [
    "BaseRepository",
    "CertificationRepository",
    "CertificationValidationError",
    "CompanyRepository",
    "CompanyValidationError",
    "DuplicateBusinessNoError",
    "DuplicatePolicyCodeError",
    "PolicyRepository",
    "PolicyValidationError",
    "PurchaseRepository",
    "PurchaseValidationError",
    "create_connection",
    "get_connection",
]
