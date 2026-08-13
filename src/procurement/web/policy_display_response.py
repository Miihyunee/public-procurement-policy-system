"""
procurement.web.policy_display_response

정책 화면 표시 정보(:mod:`procurement.web.policy_display`)를 API 응답 모델로
변환합니다.

정책별 개발 진행 상태는 ``docs/DECISIONS.md`` 에 기록된 결정에서 나오므로,
브라우저 JavaScript 에 값을 복사해 두지 않고 서버가 응답으로 내려줍니다.
결정이 바뀌면 :mod:`procurement.web.policy_display` 한 곳만 고치면 됩니다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

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
