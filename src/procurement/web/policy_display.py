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
``WOMAN``            D-2/W-6 확정 → STEP 103 으로 구매유형별 계산 경로가 생김.
                     확정된 구매유형만 세므로 확인이 끝나기 전에는 «계산 보류»
``DISABLED``         해당 없음 — 계산 경로가 구현되어 있음.
                     단 목표율은 D-7 미확정으로 등록하지 않음
``STARTUP``          해당 없음 — 계산 경로가 구현되어 있음
``GREEN``            §0.5.1 (**이번 MVP 계산 대상에서 제외**)
``SOCIAL_ENTERPRISE``
                     §0.22 확정 — 계산 경로는 일반 규칙(결의일자).
                     종료일 없는 인증 처리는 확인 요청서 ③ 대기
``SOCIAL_COOPERATIVE``
                     위와 같음
``DISABLED_STANDARD_WORKPLACE``
                     §0.22 확정 — 해당 없음, 계산 경로가 구현되어 있음
``SELF_SUPPORT_VILLAGE``
                     §0.22.3 (**판정 기준 미확정** — 임의로 정하지 않고 보류)
===================  ==========================================================

.. note::
    **2026-08-20 — ``GREEN`` 은 이제 비활성 정책입니다.** 고객 결정(§0.5.1)에
    따라 ``is_active=False`` 로 seed 되므로 ``find_active()`` 에 잡히지 않고,
    대시보드 요약에도 나타나지 않습니다. 따라서 아래 ``GREEN`` 표시 정보는
    사실상 도달하지 않습니다.

    ⛔ **표시 정보를 지우지 않습니다.** 정책 행 자체는 남아 있고(이력 보존),
    ``GET /policies`` 같은 전체 조회 경로에서는 여전히 노출될 수 있습니다.
    표시 문구 변경은 화면 변경에 해당하므로 별도 승인 대상입니다.

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
    # ── 2026-09-08 · STEP 151. 여성기업은 더 이상 «개발 보류» 가 아니다.
    #    STEP 103 으로 구매유형별 계산 경로가 생겼고, ON_HOLD_REASONS 에서도
    #    그때 빠졌다. 그런데 이 표시 정보만 D-1 시절 문구로 남아 있어서
    #    화면 배지가 「개발 보류」, 달성률 칸이 언제나 «계산 보류» 로 굳어
    #    **구매유형 확인을 끝내도 계속 보류처럼 보였다.**
    #    ⛔ 계산은 건드리지 않는다 — 확인이 덜 끝난 동안의 보류는 그대로다.
    #       그 보류는 화면에서 「구매유형 확인 중」으로 따로 말한다(§작업 1).
    "WOMAN": PolicyDisplayInfo(
        development_status=READY,
        development_label="계산 가능",
        note=(
            "목표가 공사·용역·물품으로 나뉩니다. 담당자가 확정한 구매유형만 "
            "세므로 확인이 끝나기 전에는 달성률을 내지 않습니다."
        ),
    ),
    "GREEN": PolicyDisplayInfo(
        development_status=ON_HOLD,
        development_label="계산 보류",
        note="D-3 확정 — 정책은 유지하되 공식 기준 재정립 전까지 계산을 보류합니다.",
    ),
    # ── 2026-09-03 PM 확정(§0.22 · STEP 97) 으로 추가된 4종.
    #    셋은 일반 규칙(결의일자)이 이미 구현되어 있으므로 계산 가능이고,
    #    자활용사촌만 **판정 기준 자체가 미확정**이라 보류다(§0.22.3).
    "SOCIAL_ENTERPRISE": PolicyDisplayInfo(
        development_status=READY,
        development_label="계산 가능",
        note="종료일 없는 인증의 처리 방식은 고객 확인 대기입니다(확인 요청서 ③).",
    ),
    "SOCIAL_COOPERATIVE": PolicyDisplayInfo(
        development_status=READY,
        development_label="계산 가능",
        note="종료일 없는 인증의 처리 방식은 고객 확인 대기입니다(확인 요청서 ③).",
    ),
    "DISABLED_STANDARD_WORKPLACE": _READY,
    "SELF_SUPPORT_VILLAGE": PolicyDisplayInfo(
        development_status=ON_HOLD,
        development_label="계산 보류",
        note=(
            "§0.22.3 — 판정 기준이 결의일자인지 「기간 무관·거래 유무」인지 "
            "확정되지 않았습니다. 임의로 정하지 않고 확인을 기다립니다."
        ),
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
