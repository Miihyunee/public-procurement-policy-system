"""
procurement.admin.response

정책 목표율 관리 API 의 **요청·응답 스키마**를 정의합니다.

직렬화 규약은 대시보드 API(:mod:`procurement.api.response`)와 동일합니다.

- ``Decimal`` → **문자열**(정밀도 보존). 미설정은 JSON ``null``.
- ``datetime`` → ISO 8601 문자열(Pydantic 기본 직렬화).

.. note::
    요청 본문의 ``target_rate`` 는 **문자열 또는 ``null`` 만** 허용합니다.
    JSON number 로 받으면 ``float`` 를 거치면서 ``Decimal`` 정밀도가 손상될 수
    있어, 숫자를 문자열로 바꿔주는 변환 로직을 두지 않고 422 로 거부합니다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, StrictStr, field_serializer

from procurement.models.policy import Policy

#: 목표율이 설정된 정책의 상태 코드.
TARGET_RATE_SET = "SET"
#: 목표율이 설정되지 않은 정책의 상태 코드.
TARGET_RATE_NOT_SET = "NOT_SET"


class TargetRateUpdateRequest(BaseModel):
    """목표율 변경 요청 본문.

    ``target_rate`` 는 **필수 키**입니다. 키가 아예 없으면 422 로 거부되며,
    이를 통해 "값을 바꾸지 않겠다"와 "목표율을 해제하겠다"를 구분합니다.

    Attributes:
        target_rate: 새 목표율 문자열(예: ``"8.0"``). ``None`` 이면 목표율을
            해제(미설정으로 되돌림)합니다. JSON number 는 허용하지 않습니다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_rate: StrictStr | None


class PolicyItemResponseModel(BaseModel):
    """정책 1건의 목표율 관리 응답 모델.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명.
        evaluation_basis: 판정 기준일 유형(``PAYMENT_DATE`` / ``CONTRACT_DATE``).
        is_active: 활성 여부. **비활성 정책은 목표율을 변경할 수 없으므로**
            클라이언트가 구분할 수 있어야 합니다.
        target_rate: 목표 구매비율(%)(직렬화 시 문자열). 미설정이면 ``null``.
        target_rate_status: ``SET`` / ``NOT_SET``. ``target_rate`` 의 ``null`` 을
            0 으로 오해하지 않도록 상태를 함께 제공합니다.
        updated_at: 최종 수정일시(ISO 8601).
    """

    model_config = ConfigDict(frozen=True)

    policy_id: int
    policy_code: str
    policy_name: str
    evaluation_basis: str
    is_active: bool
    target_rate: Decimal | None
    target_rate_status: str
    updated_at: datetime | None

    @field_serializer("target_rate", when_used="always")
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        """``Decimal`` 을 문자열로 직렬화합니다(미설정은 JSON ``null``)."""
        return None if value is None else str(value)

    @classmethod
    def from_policy(cls, policy: Policy) -> PolicyItemResponseModel:
        """:class:`Policy` 로부터 응답 모델을 생성합니다.

        Args:
            policy: 저장소에서 조회한 정책. ``policy_id`` 가 채워져 있어야 합니다.

        Returns:
            :class:`PolicyItemResponseModel`.

        Raises:
            ValueError: ``policy_id`` 가 ``None`` 인 경우(저장 전 엔티티).
        """
        if policy.policy_id is None:
            raise ValueError("저장되지 않은 정책은 응답으로 변환할 수 없습니다.")
        return cls(
            policy_id=policy.policy_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            evaluation_basis=policy.evaluation_basis,
            is_active=policy.is_active,
            target_rate=policy.target_rate,
            target_rate_status=(
                TARGET_RATE_NOT_SET if policy.target_rate is None else TARGET_RATE_SET
            ),
            updated_at=policy.updated_at,
        )


class PolicyListResponseModel(BaseModel):
    """정책 목록 응답 모델.

    Attributes:
        policies: 정책 목록(활성·비활성 모두 포함).
    """

    model_config = ConfigDict(frozen=True)

    policies: list[PolicyItemResponseModel]

    @classmethod
    def from_policies(cls, policies: list[Policy]) -> PolicyListResponseModel:
        """:class:`Policy` 목록으로부터 응답 모델을 생성합니다."""
        return cls(policies=[PolicyItemResponseModel.from_policy(item) for item in policies])
