"""
procurement.web.policy_display

정책별 **개발 진행 상태**를 화면 표시용으로 제공합니다.

이 모듈은 계산에 관여하지 않습니다. 달성률 계산·정책 판정은 Calculator 와 Rule
Engine 이 담당하며, 여기서는 "이 정책이 지금 계산 가능한 단계인가"를 화면에
알려주기 위한 **표시 정보**만 다룹니다.

값은 새로 만든 업무 규칙이 아니라 ``docs/DECISIONS.md`` 에 기록된 PM 확정·대기
사항을 그대로 옮긴 것입니다.

===================  ==========================================================
정책 코드            근거
===================  ==========================================================
``SMALL_BUSINESS``   해당 없음 — 계산 경로가 구현되어 있음
``WOMAN``            D-1 (개발 중단, D-2 선행) · D-2/W-6 (공사/용역/물품 구분 미확인)
``DISABLED``         해당 없음 — 계산 경로가 구현되어 있음.
                     단 목표율은 D-7 미확정으로 등록하지 않음
``STARTUP``          해당 없음 — 계산 경로가 구현되어 있음
``GREEN``            D-3 (유지 / **계산 보류** / 공식 기준 재정립 필요)
===================  ==========================================================

.. note::
    정책 코드는 D-15 에 따라 ``main`` 의 seed 가 정본입니다. 목록에 없는 코드가
    들어오면 :data:`DEFAULT_DISPLAY` 를 사용해 "상태 미정" 으로 표시하며,
    화면이 정책을 누락하지 않도록 합니다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 계산 경로가 준비된 정책
READY = "READY"

#: 결정 대기로 계산을 보류한 정책
ON_HOLD = "ON_HOLD"

#: 표시 정보가 정의되지 않은 정책
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, kw_only=True)
class PolicyDisplayInfo:
    """정책 하나의 화면 표시 정보.

    Attributes:
        development_status: :data:`READY` / :data:`ON_HOLD` / :data:`UNKNOWN`.
        development_label: 화면 표시용 한글 라벨.
        note: 상태의 근거(어느 결정 때문에 보류인지). 없으면 빈 문자열.
    """

    development_status: str
    development_label: str
    note: str


#: 표시 정보가 정의되지 않은 정책 코드에 사용하는 기본값
DEFAULT_DISPLAY = PolicyDisplayInfo(
    development_status=UNKNOWN,
    development_label="상태 미정",
    note="DECISIONS.md 에 표시 기준이 정의되지 않은 정책입니다.",
)

_READY = PolicyDisplayInfo(
    development_status=READY,
    development_label="계산 가능",
    note="",
)

#: 정책 코드별 표시 정보 (근거는 모듈 docstring 표 참조)
POLICY_DISPLAY: dict[str, PolicyDisplayInfo] = {
    "SMALL_BUSINESS": _READY,
    "DISABLED": PolicyDisplayInfo(
        development_status=READY,
        development_label="계산 가능",
        note="목표율은 D-7(장애인표준사업장 목표율 근거) 확정 전까지 등록하지 않습니다.",
    ),
    "STARTUP": _READY,
    "WOMAN": PolicyDisplayInfo(
        development_status=ON_HOLD,
        development_label="개발 보류",
        note="D-1 개발 중단 — D-2/W-6(공사·용역·물품 구분) 확인이 선행되어야 합니다.",
    ),
    "GREEN": PolicyDisplayInfo(
        development_status=ON_HOLD,
        development_label="계산 보류",
        note="D-3 확정 — 정책은 유지하되 공식 기준 재정립 전까지 계산을 보류합니다.",
    ),
}


def get_display_info(policy_code: str) -> PolicyDisplayInfo:
    """정책 코드에 대한 화면 표시 정보를 반환합니다.

    Args:
        policy_code: 정책 코드.

    Returns:
        :class:`PolicyDisplayInfo`. 정의되지 않은 코드면 :data:`DEFAULT_DISPLAY`.
    """
    return POLICY_DISPLAY.get(policy_code, DEFAULT_DISPLAY)
