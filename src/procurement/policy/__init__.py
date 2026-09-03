"""
procurement.policy

정책에 대한 **확정 사실**을 담는 패키지입니다. 계산·저장은 하지 않습니다.
"""

from __future__ import annotations

from procurement.policy.confirmed_targets import (
    CALCULABLE_TARGETS,
    CONFIRMED_TARGETS,
    ON_HOLD_REASONS,
    ON_HOLD_TARGETS,
    STORABLE_TARGET_RATES,
    ConfirmedTarget,
)

__all__ = [
    "CALCULABLE_TARGETS",
    "CONFIRMED_TARGETS",
    "ON_HOLD_REASONS",
    "ON_HOLD_TARGETS",
    "STORABLE_TARGET_RATES",
    "ConfirmedTarget",
]
