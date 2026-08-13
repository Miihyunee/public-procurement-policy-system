"""
procurement.web

Dashboard 화면(정적 페이지)과 화면 표시 전용 데이터를 제공하는 패키지.

계층 원칙은 그대로 유지합니다. 이 패키지는 **계산하지 않고 저장소에 접근하지
않습니다.** 화면은 기존 JSON API(``/dashboard/summary`` · ``/policies`` ·
``/dashboard/data-status``)를 브라우저에서 호출해 그립니다::

    브라우저(index.html) → HTTP JSON API → 기존 서비스 계층

.. note::
    페이지는 외부 CDN·차트 라이브러리를 사용하지 않습니다(순수 HTML/CSS/JS + SVG).
    새 런타임 의존성을 추가하지 않기 위한 선택입니다.
"""

from procurement.web.achievement_display import (
    DEFAULT_THRESHOLDS,
    AchievementDisplayLevel,
    ThresholdConfigError,
    parse_thresholds,
    resolve_level,
)
from procurement.web.page import INDEX_HTML_PATH, read_index_html
from procurement.web.policy_display import (
    DEFAULT_DISPLAY,
    ON_HOLD,
    POLICY_DISPLAY,
    READY,
    UNKNOWN,
    PolicyDisplayInfo,
    get_display_info,
)
from procurement.web.policy_display_response import (
    AchievementLevelsResponseModel,
    PolicyDisplayItemResponseModel,
    PolicyDisplayResponseModel,
    build_achievement_levels_response,
    build_policy_display_response,
)

__all__ = [
    "DEFAULT_DISPLAY",
    "DEFAULT_THRESHOLDS",
    "INDEX_HTML_PATH",
    "ON_HOLD",
    "POLICY_DISPLAY",
    "READY",
    "UNKNOWN",
    "AchievementDisplayLevel",
    "AchievementLevelsResponseModel",
    "PolicyDisplayInfo",
    "PolicyDisplayItemResponseModel",
    "PolicyDisplayResponseModel",
    "ThresholdConfigError",
    "build_achievement_levels_response",
    "build_policy_display_response",
    "get_display_info",
    "parse_thresholds",
    "resolve_level",
    "read_index_html",
]
