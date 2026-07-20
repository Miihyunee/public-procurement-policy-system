"""
procurement.core.config

애플리케이션 설정 패키지.

외부 모듈에서는 아래와 같이 임포트합니다::

    from procurement.core.config import settings

    print(settings.APP_NAME)
    print(settings.DATABASE_PATH)
"""

from procurement.core.config.settings import Settings, settings

__all__ = ["Settings", "settings"]