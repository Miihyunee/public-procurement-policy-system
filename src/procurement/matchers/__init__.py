"""
procurement.matchers

데이터 매칭 서비스 패키지.

Repository 를 사용하여 데이터 간 연결을 수행하는 서비스 계층입니다::

    from procurement.matchers import CompanyMatcher
"""

from procurement.matchers.company_matcher import CompanyMatcher

__all__ = ["CompanyMatcher"]
