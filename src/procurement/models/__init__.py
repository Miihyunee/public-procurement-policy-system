"""
procurement.models

도메인 모델 패키지.

각 모델은 ``docs/DATABASE_DESIGN.md`` 의 테이블 정의를 기준으로 구현합니다::

    from procurement.models import Company
"""

from procurement.models.company import Company

__all__ = ["Company"]
