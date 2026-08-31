"""
procurement.core.performance_exclusion

**실적 산입 여부** — 어떤 구매를 달성률 계산에서 빼는가.

2026-08-31 고객 회신으로 확정된 업무규칙입니다(``DECISIONS.md`` §0.10).

.. warning::
    ⛔ **구매유형과 다른 개념입니다.**

    ==================== ==========================================
    구매유형              ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS``
    실적 산입 여부        :data:`INCLUDED` · :data:`EXCLUDED`
    ==================== ==========================================

    유형을 용역으로 확정한다고 실적에서 빠지지 않고, 물품으로 확정한다고
    포함되지도 않습니다. 두 값은 **다른 필드**에 따로 저장합니다 —
    한 필드에 섞으면 "강사료라서 뺐다" 와 "용역이다" 가 구분되지 않습니다.

빼는 경로는 **둘뿐**입니다
==========================

1. **예산과목 규칙** — :data:`EXCLUDED_BUDGET_ACCOUNTS` 6종.
   고객이 *"적요나 내용과 관계없이 무조건 실적에서 삭제한다"* 고 답한
   항목이므로 자동으로 뺍니다.
2. **담당자 확정** — 검토 화면에서 사람이 사유를 골라 확정한 건.

.. warning::
    ⛔ **적요 낱말로 빼지 않습니다.** `교육` · `강사` · `임차` · `렌트` ·
    `단기` · `1일` 같은 말이 들어 있다는 이유만으로 자동 제외하지 않습니다.

    고객은 교육비·강사료를 **지출결의서와 세금계산서 내역까지 확인**해서
    판단하고, 단기 차량 임차는 **사업부서 품의서**를 보고 판단한다고
    답했습니다. 그 자료는 지금 시스템에 없습니다. 없는 근거로 자동 판정하면
    담당자가 확인하지 않은 판정이 실적 숫자로 굳습니다.

.. warning::
    ⛔ **임차 기간으로 판정하지 않습니다.** 고객이 *"기간과 상관없이"* 라고
    명시했습니다 — 단발성 출장을 위해 출장지에서 빌린 차량이면 하루든
    2박 3일이든 단기 차량 임차입니다. "○일 이하" 규칙을 만들지 않습니다.
"""

from __future__ import annotations

from typing import Final

#: 실적에 **포함**되는 상태. 아무것도 하지 않은 행의 기본값입니다.
INCLUDED: Final = "INCLUDED"

#: 실적에서 **빠지는** 상태. 담당자가 사유와 함께 확정합니다.
EXCLUDED: Final = "EXCLUDED"

#: 허용되는 실적 산입 상태.
PERFORMANCE_STATUSES: Final[frozenset[str]] = frozenset({INCLUDED, EXCLUDED})

#: 화면에 쓰는 한글 라벨.
PERFORMANCE_STATUS_LABELS: Final[dict[str, str]] = {
    INCLUDED: "실적 포함",
    EXCLUDED: "실적 제외",
}

# ----------------------------------------------------------------------
# 예산과목 규칙 (고객 확정 · 자동 적용)
# ----------------------------------------------------------------------

#: 내용과 **관계없이** 실적에서 빼는 예산과목.
#:
#: 고객 회신(2026-08-31):
#:
#:     "교육훈련비 · 사업추진경비 · 의료비 · 수도광열비 · 기타운영비 ·
#:      복리후생비 는 적요나 내용과 관계없이 무조건 실적에서 삭제한다."
#:
#: .. warning::
#:     ⛔ **정확히 같은 값만** 봅니다. 부분 문자열로 판단하지 않습니다 —
#:     `교육훈련비지원` 같은 다른 과목이 휩쓸려 들어가면 안 됩니다.
#:
#: ⛔ **여기에 다른 예산과목을 임의로 더하지 않습니다.** 고객이 지목한 6개가
#: 전부입니다.
EXCLUDED_BUDGET_ACCOUNTS: Final[frozenset[str]] = frozenset(
    {
        "교육훈련비",
        "사업추진경비",
        "의료비",
        "수도광열비",
        "기타운영비",
        "복리후생비",
    }
)

# ----------------------------------------------------------------------
# 담당자 확정 사유
# ----------------------------------------------------------------------

#: 교육비 — 고객이 지출결의서·세금계산서로 확인한 뒤 빼는 건.
REASON_EDUCATION_FEE: Final = "EDUCATION_FEE"

#: 강사료 — 〃
REASON_LECTURER_FEE: Final = "LECTURER_FEE"

#: 단기 차량 임차 — 단발성 출장을 위해 출장지에서 빌린 차량(기간 무관).
REASON_SHORT_TERM_VEHICLE_LEASE: Final = "SHORT_TERM_VEHICLE_LEASE"

#: 그 밖의 사유. 메모에 무엇인지 적습니다.
REASON_OTHER: Final = "OTHER"

#: 사유 코드 → 한글 라벨. 화면 선택지의 **순서**이기도 합니다.
EXCLUSION_REASON_LABELS: Final[dict[str, str]] = {
    REASON_EDUCATION_FEE: "교육비",
    REASON_LECTURER_FEE: "강사료",
    REASON_SHORT_TERM_VEHICLE_LEASE: "단기 차량 임차",
    REASON_OTHER: "기타",
}

#: 예산과목 규칙으로 자동 제외될 때 붙는 사유. 담당자가 고를 수 없습니다.
REASON_BUDGET_ACCOUNT_RULE: Final = "BUDGET_ACCOUNT_RULE"

#: 자동 제외 사유의 한글 라벨.
BUDGET_ACCOUNT_RULE_LABEL: Final = "예산과목 규칙"

#: 단기 차량 임차를 어떻게 판단하는지 담당자에게 알리는 문구.
#:
#: ⛔ **자동 판정 기준이 아닙니다.** 화면이 사람에게 보여 주는 안내일 뿐이며,
#: 코드가 이 문장으로 무엇을 결정하지 않습니다.
SHORT_TERM_VEHICLE_NOTICE: Final = (
    "단기 차량 임차 여부는 사업부서 품의서를 확인하여 판단합니다. "
    "단발성 출장을 위해 출장지에서 빌린 차량이면 기간과 관계없이 단기 차량 임차입니다."
)


class ExclusionReasonError(ValueError):
    """허용되지 않는 제외 사유 코드."""


def normalize_budget_account(value: str | None) -> str:
    """예산과목을 비교용으로 다듬습니다 — **앞뒤 공백만** 떼어 냅니다.

    ⛔ 그 이상 손대지 않습니다. 가운뎃점을 지우거나 띄어쓰기를 없애는 것은
    확인받지 않은 규칙이 됩니다.
    """
    return (value or "").strip()


def is_excluded_budget_account(value: str | None) -> bool:
    """이 예산과목이 **고객이 지목한 6종**인지 판정합니다.

    Examples:
        >>> is_excluded_budget_account("교육훈련비")
        True
        >>> is_excluded_budget_account(" 교육훈련비 ")
        True
        >>> is_excluded_budget_account("교육훈련비지원")
        False
        >>> is_excluded_budget_account("일반운영비")
        False
    """
    return normalize_budget_account(value) in EXCLUDED_BUDGET_ACCOUNTS


def validate_exclusion_reason(reason: str) -> str:
    """담당자가 고를 수 있는 사유인지 확인합니다.

    ⛔ :data:`REASON_BUDGET_ACCOUNT_RULE` 은 규칙이 붙이는 사유이므로 담당자가
    고를 수 없습니다.

    Raises:
        ExclusionReasonError: 허용 목록에 없는 값인 경우.
    """
    if reason not in EXCLUSION_REASON_LABELS:
        allowed = " · ".join(EXCLUSION_REASON_LABELS)
        raise ExclusionReasonError(f"허용되지 않는 제외 사유입니다: {reason!r} (허용: {allowed})")
    return reason


def exclusion_reason_label(reason: str | None) -> str | None:
    """사유 코드를 화면에 쓸 말로 바꿉니다. 모르는 값은 그대로 돌려줍니다."""
    if reason is None:
        return None
    if reason == REASON_BUDGET_ACCOUNT_RULE:
        return BUDGET_ACCOUNT_RULE_LABEL
    return EXCLUSION_REASON_LABELS.get(reason, reason)
