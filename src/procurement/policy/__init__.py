"""
procurement.policy

정책에 대한 **확정 사실**을 담는 패키지입니다. 계산·저장은 하지 않습니다.
"""

from __future__ import annotations

from procurement.policy.confirmed_targets import (
    BLOCKED_TARGETS,
    CONFIRMED_TARGETS,
    STORABLE_TARGET_RATES,
    ConfirmedTarget,
)

__all__ = [
    "BLOCKED_TARGETS",
    "CONFIRMED_TARGETS",
    "STORABLE_TARGET_RATES",
    "ConfirmedTarget",
]
