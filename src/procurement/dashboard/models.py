"""
procurement.dashboard.models

대시보드 화면이 그대로 사용할 수 있는 요약 데이터 구조(DTO)와 상태 구분을
정의합니다.

- :class:`DashboardStatus` — 정책 달성 상태(정상/주의/부족) 구분
- :class:`PolicySummary` — 정책 하나에 대한 요약(구매금액·목표율·달성률·부족률·상태)
- :class:`DashboardSummary` — 대시보드 전체 요약(전체 구매액 + 정책별 요약 목록)

.. note::
    본 모듈은 순수 데이터 컨테이너/열거형이며 Repository·Calculator 에 직접
    접근하지 않습니다. 값 조합은
    :class:`procurement.dashboard.data_service.DashboardDataService` 가 담당합니다.
    UI·API·차트는 이번 범위에 포함하지 않습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

#: 달성 상태 판정 임계값 — 이 값 이상이면 '정상'
NORMAL_THRESHOLD = Decimal("100")

#: 달성 상태 판정 임계값 — 이 값 이상 ~ NORMAL_THRESHOLD 미만이면 '주의'
WARNING_THRESHOLD = Decimal("80")


class DashboardStatus(Enum):
    """정책 달성 상태 구분.

    달성률(%)을 기준으로 세 단계로 나눕니다 (경계값 기준은 PM 확정).

    - ``NORMAL`` (정상): 달성률 >= 100
    - ``WARNING`` (주의): 80 <= 달성률 < 100
    - ``SHORTAGE`` (부족): 달성률 < 80

    위 세 가지와 별개로, 목표율이 등록되지 않아 **달성률을 계산할 수 없는**
    상태를 나타내는 값이 있습니다.

    - ``TARGET_RATE_NOT_SET`` (목표율 미설정): ``target_rate`` 가 없어 계산하지 않음

    ``TARGET_RATE_NOT_SET`` 은 달성률로부터 판정되지 않으며
    (:meth:`from_achievement_rate` 는 이 값을 반환하지 않습니다),
    "정책이 없음"과 "목표율이 아직 등록되지 않음"을 구분하기 위해 사용합니다.
    """

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    SHORTAGE = "SHORTAGE"
    TARGET_RATE_NOT_SET = "TARGET_RATE_NOT_SET"

    @property
    def label(self) -> str:
        """화면 표시용 한글 상태명을 반환합니다."""
        return _STATUS_LABELS[self]

    @classmethod
    def from_achievement_rate(cls, achievement_rate: Decimal) -> DashboardStatus:
        """달성률(%)로부터 상태를 판정합니다.

        Args:
            achievement_rate: 목표 대비 달성률(%).

        Returns:
            달성률 구간에 해당하는 :class:`DashboardStatus`.
        """
        if achievement_rate >= NORMAL_THRESHOLD:
            return cls.NORMAL
        if achievement_rate >= WARNING_THRESHOLD:
            return cls.WARNING
        return cls.SHORTAGE


#: 상태별 화면 표시용 한글 라벨
_STATUS_LABELS: dict[DashboardStatus, str] = {
    DashboardStatus.NORMAL: "정상",
    DashboardStatus.WARNING: "주의",
    DashboardStatus.SHORTAGE: "부족",
    DashboardStatus.TARGET_RATE_NOT_SET: "목표율 미설정",
}


@dataclass(frozen=True, kw_only=True)
class PolicySummary:
    """정책 하나에 대한 대시보드 요약(DTO).

    목표율(``target_rate``)이 등록되지 않은 정책은 달성률을 계산할 수 없으므로,
    계산 관련 값이 모두 ``None`` 이고 상태는
    :attr:`DashboardStatus.TARGET_RATE_NOT_SET` 이 됩니다. 이 경우에도 정책은
    요약에서 제외되지 않으며, 화면은 "목표율 미설정"으로 표시할 수 있습니다.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명.
        purchase_amount: 해당 정책 구매금액. 목표율 미설정이면 ``None``
            (계산을 수행하지 않았음을 의미하며 ``0`` 과 구분됩니다).
        total_purchase_amount: 기관 전체 구매액(모든 정책 요약이 공유하는 분모).
        target_rate: 목표 구매비율(%). 미설정이면 ``None``.
        achievement_rate: 목표 대비 달성률(%). 목표율 미설정이면 ``None``.
        shortage_rate: 목표 달성까지 부족한 비율(%). ``max(0, 100 - 달성률)``.
            목표율 미설정이면 ``None``.
        status: 달성 상태(정상/주의/부족/목표율 미설정).
    """

    policy_id: int
    policy_code: str
    policy_name: str
    purchase_amount: Decimal | None
    total_purchase_amount: Decimal
    target_rate: Decimal | None
    achievement_rate: Decimal | None
    shortage_rate: Decimal | None
    status: DashboardStatus


@dataclass(frozen=True, kw_only=True)
class DashboardSummary:
    """대시보드 전체 요약(DTO).

    Attributes:
        total_purchase_amount: 기관 전체 구매액.
        policy_summaries: 정책별 요약 목록. 요청한 정책이 없으면 빈 목록.
    """

    total_purchase_amount: Decimal
    policy_summaries: list[PolicySummary]
