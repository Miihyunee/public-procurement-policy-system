"""
procurement.web.policy_display_response

정책 화면 표시 정보(:mod:`procurement.web.policy_display`)를 API 응답 모델로
변환합니다.

정책별 개발 진행 상태는 ``docs/DECISIONS.md`` 에 기록된 결정에서 나오므로,
브라우저 JavaScript 에 값을 복사해 두지 않고 서버가 응답으로 내려줍니다.
결정이 바뀌면 :mod:`procurement.web.policy_display` 한 곳만 고치면 됩니다.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from procurement.web.achievement_display import (
    LABEL_NOT_CALCULATED,
    LEVEL_NOT_CALCULATED,
    describe_levels,
)
from procurement.web.policy_display import POLICY_DISPLAY, PolicyDisplayInfo


class PolicyDisplayItemResponseModel(BaseModel):
    """정책 하나의 화면 표시 정보 응답 모델.

    Attributes:
        policy_code: 정책 코드.
        development_status: ``READY`` / ``ON_HOLD`` / ``UNKNOWN``.
        development_label: 화면 표시용 한글 라벨.
        note: 상태의 근거. 없으면 빈 문자열.
    """

    model_config = ConfigDict(frozen=True)

    policy_code: str
    development_status: str
    development_label: str
    note: str

    @classmethod
    def from_display_info(
        cls, policy_code: str, info: PolicyDisplayInfo
    ) -> PolicyDisplayItemResponseModel:
        """표시 정보로부터 응답 모델을 생성합니다."""
        return cls(
            policy_code=policy_code,
            development_status=info.development_status,
            development_label=info.development_label,
            note=info.note,
        )


class PolicyDisplayResponseModel(BaseModel):
    """정책 화면 표시 정보 목록 응답 모델.

    Attributes:
        items: 정책 코드별 표시 정보 목록.
    """

    model_config = ConfigDict(frozen=True)

    items: list[PolicyDisplayItemResponseModel]


def build_policy_display_response() -> PolicyDisplayResponseModel:
    """정의된 전체 정책 표시 정보를 응답 모델로 만듭니다.

    Returns:
        :class:`PolicyDisplayResponseModel`.
    """
    return PolicyDisplayResponseModel(
        items=[
            PolicyDisplayItemResponseModel.from_display_info(code, info)
            for code, info in POLICY_DISPLAY.items()
        ]
    )


class AchievementLevelItemResponseModel(BaseModel):
    """달성률 표시 구간 하나의 응답 모델.

    Attributes:
        code: ``LEVEL_1`` ~ ``LEVEL_6``.
        label: 화면 라벨.
        min_rate: 구간 시작 달성률(%).
        max_rate: 구간 끝 달성률(%). 마지막 구간은 빈 문자열(상한 없음).
    """

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    min_rate: str
    max_rate: str


class AchievementLevelsResponseModel(BaseModel):
    """달성률 표시 구간표 응답 모델.

    화면이 경계값을 하드코딩하지 않도록 서버가 표를 내려줍니다.

    Attributes:
        items: 낮은 구간부터 정렬된 목록.
        not_calculated_code: 계산되지 않은 정책에 사용하는 코드.
        not_calculated_label: 계산되지 않은 정책의 라벨.
        notice: 이 구간이 법정 기준이 아님을 알리는 문구.
    """

    model_config = ConfigDict(frozen=True)

    items: list[AchievementLevelItemResponseModel]
    not_calculated_code: str
    not_calculated_label: str
    notice: str


#: 표시 구간이 법정 기준이 아님을 화면에 알리는 고정 문구
ACHIEVEMENT_LEVEL_NOTICE = (
    "화면 표시용 임시 구간입니다. 법정 기준이나 정책 판정 기준이 아닙니다."
)


def build_achievement_levels_response(
    thresholds: tuple[Decimal, ...],
) -> AchievementLevelsResponseModel:
    """표시 구간표를 응답 모델로 만듭니다.

    Args:
        thresholds: 적용할 구간 경계.

    Returns:
        :class:`AchievementLevelsResponseModel`.
    """
    return AchievementLevelsResponseModel(
        items=[
            AchievementLevelItemResponseModel(**item) for item in describe_levels(thresholds)
        ],
        not_calculated_code=LEVEL_NOT_CALCULATED,
        not_calculated_label=LABEL_NOT_CALCULATED,
        notice=ACHIEVEMENT_LEVEL_NOTICE,
    )
