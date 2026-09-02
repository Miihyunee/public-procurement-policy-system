"""
procurement.admin.policy_target_response

**연도별 · 정책별 목표비율** API 의 요청·응답 스키마입니다.

직렬화 규약은 기존 목표율 관리 API(:mod:`procurement.admin.response`)와 같습니다.

- ``Decimal`` → **문자열**(정밀도 보존). 미설정은 JSON ``null``.
- 요청 본문의 ``target_rate`` 도 **문자열만** 받습니다. JSON number 로 받으면
  ``float`` 를 거치며 ``37.5`` 같은 값의 정밀도가 손상됩니다.

.. warning::
    ⛔ **구매처(기업)를 담는 항목이 없습니다.** 목표비율의 축은 **연도 × 정책**
    둘뿐입니다(``DECISIONS.md`` §0.20).

.. note::
    응답에는 목표비율이 **설정되지 않은 활성 정책도 포함**합니다. 화면이
    "정책이 없다" 와 "정책은 있는데 목표비율이 아직 없다" 를 구분해야 하고,
    입력칸을 그리려면 목록 자체가 필요하기 때문입니다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, StrictStr, field_serializer

from procurement.admin.response import TARGET_RATE_NOT_SET, TARGET_RATE_SET


class PolicyTargetUpdateRequest(BaseModel):
    """목표비율 저장 요청 본문.

    ``target_rate`` 는 **필수 키**입니다. 키가 아예 없으면 422 로 거부되며,
    이를 통해 "값을 바꾸지 않겠다" 와 "목표비율을 해제하겠다" 를 구분합니다.

    Attributes:
        target_rate: 새 목표비율 문자열(예: ``"37.5"``). ``None`` 이면 해당
            연도·정책의 목표비율을 **해제**합니다. JSON number 는 허용하지
            않습니다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_rate: StrictStr | None


class PolicyTargetItemModel(BaseModel):
    """한 연도 · 한 정책의 목표비율 응답.

    Attributes:
        year: 대상 회계연도. 구매의 **결의일자 연도**와 맞춥니다.
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명. ⛔ 화면이 정책명을 들고 있지 않도록 서버가 줍니다.
        is_active: 정책 활성 여부.
        target_rate: 목표 구매비율(%)(직렬화 시 문자열). 미설정이면 ``null``.
        target_rate_status: ``SET`` / ``NOT_SET``. ``null`` 을 0 으로 오해하지
            않도록 상태를 함께 제공합니다.
        updated_at: 목표비율 최종 수정일시. 미설정이면 ``null``.
    """

    model_config = ConfigDict(frozen=True)

    year: int
    policy_id: int
    policy_code: str
    policy_name: str
    is_active: bool
    target_rate: Decimal | None
    target_rate_status: str
    updated_at: datetime | None

    @field_serializer("target_rate", when_used="always")
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        """``Decimal`` 을 문자열로 직렬화합니다(미설정은 JSON ``null``)."""
        return None if value is None else str(value)

    @property
    def is_set(self) -> bool:
        """목표비율이 설정되어 있는가."""
        return self.target_rate_status == TARGET_RATE_SET


class PolicyTargetListResponseModel(BaseModel):
    """한 연도의 정책별 목표비율 목록.

    Attributes:
        year: 조회한 회계연도.
        items: 정책별 목표비율. **활성 정책 전체**가 담기며, 설정되지 않은
            정책은 ``target_rate`` 가 ``null`` 이고 상태가 ``NOT_SET`` 입니다.
    """

    model_config = ConfigDict(frozen=True)

    year: int
    items: list[PolicyTargetItemModel]


def target_rate_status(target_rate: Decimal | None) -> str:
    """목표비율 유무를 상태 코드로 바꿉니다."""
    return TARGET_RATE_NOT_SET if target_rate is None else TARGET_RATE_SET
