"""
procurement.api.response

대시보드 요약 DTO(:class:`DashboardSummary`)를 **API 응답 전용 Pydantic 모델**로
변환합니다.

내부 데이터 계층(:mod:`procurement.dashboard`)은 ``Decimal`` 과 ``Enum``
(:class:`DashboardStatus`)을 그대로 사용하지만, API 응답은 JSON 직렬화 가능한
형태여야 합니다. 본 모듈의 응답 모델은 다음 직렬화 규칙을 강제합니다.

- ``Decimal`` → **문자열**(정밀도 보존, 금액 저장 규약과 동일).
- :class:`DashboardStatus` → ``status``(코드) + ``status_label``(한글 라벨)
  **두 필드**로 분리해 기계 판별과 화면 표시를 모두 지원합니다.

.. note::
    본 모듈은 순수 응답 스키마입니다. HTTP 서버·라우터·인증은 포함하지 않으며,
    FastAPI 등을 도입할 때 그대로 응답 모델로 재사용할 수 있습니다. 값 조합은
    :class:`procurement.dashboard.data_service.DashboardDataService` 가, 응답
    변환은 :class:`procurement.api.dashboard_api.DashboardApiService` 가 담당합니다.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from procurement.dashboard.models import (
    DashboardSummary,
    MissingResolutionDate,
    PolicySummary,
)


class PolicySummaryResponseModel(BaseModel):
    """정책 하나에 대한 API 응답 모델.

    :class:`PolicySummary` 를 JSON 직렬화 가능한 형태로 변환합니다. 금액·비율은
    문자열로 직렬화되며, 상태는 ``status``(코드)와 ``status_label``(한글) 두
    필드로 제공됩니다.

    목표율이 등록되지 않은 정책은 달성률을 계산할 수 없으므로, 계산 관련 값이
    모두 ``null`` 이고 ``status`` 는 ``TARGET_RATE_NOT_SET`` 이 됩니다. 이 경우에도
    정책은 응답에서 제외되지 않으므로, 화면에서 "정책이 없음"과 "목표율 미설정"을
    구분할 수 있습니다.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명.
        purchase_amount: 해당 정책 구매금액(직렬화 시 문자열). 목표율 미설정이면
            ``null`` — 계산을 수행하지 않았음을 의미하며 ``"0"`` 과 구분됩니다.
        total_purchase_amount: 기관 전체 구매액(직렬화 시 문자열).
        target_rate: 목표 구매비율(%)(직렬화 시 문자열). 미설정이면 ``null``.
        achievement_rate: 목표 대비 달성률(%)(직렬화 시 문자열). 목표율 미설정이면 ``null``.
        shortage_rate: 목표 달성까지 부족한 비율(%)(직렬화 시 문자열).
            목표율 미설정이면 ``null``.
        status: 달성 상태 코드(``NORMAL`` / ``WARNING`` / ``SHORTAGE`` /
            ``TARGET_RATE_NOT_SET``).
        status_label: 화면 표시용 한글 상태명(정상 / 주의 / 부족 / 목표율 미설정).
    """

    model_config = ConfigDict(frozen=True)

    policy_id: int
    policy_code: str
    policy_name: str
    purchase_amount: Decimal | None
    total_purchase_amount: Decimal
    target_rate: Decimal | None
    achievement_rate: Decimal | None
    shortage_rate: Decimal | None
    status: str
    status_label: str

    @field_serializer(
        "purchase_amount",
        "total_purchase_amount",
        "target_rate",
        "achievement_rate",
        "shortage_rate",
        when_used="always",
    )
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        """``Decimal`` 필드를 문자열로 직렬화합니다(python·json 모드 공통).

        값이 없으면(``None``) 그대로 ``None`` 을 반환해 JSON ``null`` 로
        직렬화합니다. ``"None"`` 같은 문자열로 변환하지 않습니다.
        """
        return None if value is None else str(value)

    @classmethod
    def from_policy_summary(cls, summary: PolicySummary) -> PolicySummaryResponseModel:
        """:class:`PolicySummary` 로부터 응답 모델을 생성합니다.

        상태 Enum 은 코드(``status``)와 한글 라벨(``status_label``)로 분리합니다.
        """
        return cls(
            policy_id=summary.policy_id,
            policy_code=summary.policy_code,
            policy_name=summary.policy_name,
            purchase_amount=summary.purchase_amount,
            total_purchase_amount=summary.total_purchase_amount,
            target_rate=summary.target_rate,
            achievement_rate=summary.achievement_rate,
            shortage_rate=summary.shortage_rate,
            status=summary.status.value,
            status_label=summary.status.label,
        )


class MissingResolutionDateResponseModel(BaseModel):
    """결의일자 미기재 안내 응답 모델.

    .. warning::
        ⛔ **달성률과 무관한 값입니다.** 분모·분자 어디에도 들어가지 않으며,
        ``total_purchase_amount`` 에도 포함되지 않습니다. 화면이 "이만큼이 기간
        산정에서 빠졌습니다" 라고 알려 주기 위한 **표시 전용** 값입니다.

    Attributes:
        applies: 이 안내가 지금 조회에 해당하는지. 기간 판정 기준일이
            결의일자일 때만 ``true``. ``false`` 이면 화면은 아무것도 표시하지
            않습니다.
        count: 결의일자가 없는 구매 건수.
        amount: 그 구매들의 금액 합계(직렬화 시 문자열).
    """

    model_config = ConfigDict(frozen=True)

    applies: bool
    count: int
    amount: Decimal

    @field_serializer("amount", when_used="always")
    def _serialize_amount(self, value: Decimal) -> str:
        """``Decimal`` 을 문자열로 직렬화합니다."""
        return str(value)

    @classmethod
    def from_missing(cls, missing: MissingResolutionDate) -> MissingResolutionDateResponseModel:
        """DTO 로부터 응답 모델을 생성합니다."""
        return cls(applies=missing.applies, count=missing.count, amount=missing.amount)


class DashboardResponseModel(BaseModel):
    """대시보드 전체 API 응답 모델.

    :class:`DashboardSummary` 를 JSON 직렬화 가능한 형태로 변환합니다.

    Attributes:
        total_purchase_amount: 기관 전체 구매액(직렬화 시 문자열).
        policies: 정책별 요약 응답 목록. 대상 정책이 없으면 빈 목록.
        missing_resolution_date: 결의일자가 없어 기간 산정에서 빠진 건수·금액.
            ⛔ **위 두 값과 무관합니다** — 계산에 들어가지 않습니다.
    """

    model_config = ConfigDict(frozen=True)

    total_purchase_amount: Decimal
    policies: list[PolicySummaryResponseModel]
    missing_resolution_date: MissingResolutionDateResponseModel

    @field_serializer("total_purchase_amount", when_used="always")
    def _serialize_decimal(self, value: Decimal) -> str:
        """``Decimal`` 필드를 문자열로 직렬화합니다(python·json 모드 공통)."""
        return str(value)

    @classmethod
    def from_summary(cls, summary: DashboardSummary) -> DashboardResponseModel:
        """:class:`DashboardSummary` 로부터 응답 모델을 생성합니다."""
        return cls(
            total_purchase_amount=summary.total_purchase_amount,
            policies=[
                PolicySummaryResponseModel.from_policy_summary(item)
                for item in summary.policy_summaries
            ],
            missing_resolution_date=MissingResolutionDateResponseModel.from_missing(
                summary.missing_resolution_date
            ),
        )
