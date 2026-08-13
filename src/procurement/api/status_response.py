"""
procurement.api.status_response

데이터 적재 현황(:class:`DataStatus`)을 **API 응답 전용 Pydantic 모델**로
변환합니다.

직렬화 규칙은 기존 :mod:`procurement.api.response` 와 동일합니다.

- ``Decimal`` → **문자열**(정밀도 보존).
- ``date`` → ``YYYY-MM-DD`` 문자열(Pydantic 기본 직렬화).

.. note::
    응답에는 **기간 필터가 적용되지 않았다는 사실**이 반드시 포함됩니다
    (``period_filter_applied`` · ``period_notice``). 연도별 집계는 D-23 ~ D-27
    확정 후 별도 Issue 에서 구현합니다. 화면이 "2026년" 을 선택해도 지금은
    전체 데이터가 표시되며, 그 사실을 응답으로 알려 화면이 오해를 부르지 않도록
    합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from procurement.dashboard.status_service import DataStatus

#: 기간 필터가 아직 구현되지 않았음을 화면에 알리는 고정 문구
PERIOD_NOTICE = "기간 필터 미적용 — 전체 데이터 기준입니다(연도별 집계는 미구현)."


class DataStatusResponseModel(BaseModel):
    """데이터 적재 현황 API 응답 모델.

    Attributes:
        requested_year: 화면이 선택한 연도. 지정하지 않으면 ``null``.
            **현재는 조회 조건으로 사용되지 않고 그대로 되돌려줍니다.**
        period_filter_applied: 기간 필터 적용 여부. 현재 구현에서는 항상
            ``false`` 입니다.
        period_notice: 기간 필터 미적용 사실을 알리는 화면 표시용 문구.
        purchase_count: 적재된 구매 건수.
        purchase_total_amount: 구매금액 합계(직렬화 시 문자열).
        matched_purchase_count: 기업 매칭이 끝난 구매 건수.
        unmatched_purchase_count: 기업 매칭이 되지 않은 구매 건수.
        earliest_payment_date: 가장 이른 지급일. 데이터가 없으면 ``null``.
        latest_payment_date: 가장 늦은 지급일. 데이터가 없으면 ``null``.
        earliest_contract_date: 가장 이른 계약일. 데이터가 없으면 ``null``.
        latest_contract_date: 가장 늦은 계약일. 데이터가 없으면 ``null``.
        company_count: 적재된 기업 수.
        certification_count: 적재된 인증 건수.
        policy_count: 등록된 정책 수(비활성 포함).
        policy_with_target_rate_count: 목표율이 설정된 활성 정책 수.
        data_mode: 현재 데이터 모드(``demo`` / ``operational``). 설정값에서 옵니다.
    """

    model_config = ConfigDict(frozen=True)

    requested_year: int | None
    period_filter_applied: bool
    period_notice: str
    purchase_count: int
    purchase_total_amount: Decimal
    matched_purchase_count: int
    unmatched_purchase_count: int
    earliest_payment_date: date | None
    latest_payment_date: date | None
    earliest_contract_date: date | None
    latest_contract_date: date | None
    company_count: int
    certification_count: int
    policy_count: int
    policy_with_target_rate_count: int
    data_mode: str

    @field_serializer("purchase_total_amount", when_used="always")
    def _serialize_decimal(self, value: Decimal) -> str:
        """``Decimal`` 필드를 문자열로 직렬화합니다(python·json 모드 공통)."""
        return str(value)

    @classmethod
    def from_status(
        cls,
        status: DataStatus,
        *,
        data_mode: str,
        requested_year: int | None = None,
    ) -> DataStatusResponseModel:
        """:class:`DataStatus` 로부터 응답 모델을 생성합니다.

        Args:
            status: 저장소 적재 현황.
            data_mode: 현재 데이터 모드 문자열.
            requested_year: 화면이 선택한 연도(있으면 그대로 되돌려줍니다).

        Returns:
            :class:`DataStatusResponseModel`. ``period_filter_applied`` 는 항상
            ``False`` 입니다.
        """
        return cls(
            requested_year=requested_year,
            period_filter_applied=False,
            period_notice=PERIOD_NOTICE,
            purchase_count=status.purchase_count,
            purchase_total_amount=status.purchase_total_amount,
            matched_purchase_count=status.matched_purchase_count,
            unmatched_purchase_count=status.unmatched_purchase_count,
            earliest_payment_date=status.earliest_payment_date,
            latest_payment_date=status.latest_payment_date,
            earliest_contract_date=status.earliest_contract_date,
            latest_contract_date=status.latest_contract_date,
            company_count=status.company_count,
            certification_count=status.certification_count,
            policy_count=status.policy_count,
            policy_with_target_rate_count=status.policy_with_target_rate_count,
            data_mode=data_mode,
        )
