"""
procurement.models.import_rejection

**원본에는 있었지만 DB-1 에 적재되지 않은 행**의 기록.

.. warning::
    ⛔ **업무 판단이 아닙니다.**

    이 기록은 "이 행은 실적에서 제외한다" 는 뜻이 **아닙니다.** 담당자가
    "원본 2,292행 중 왜 2,162행만 화면에 보이는가" 를 설명할 수 있게 하는
    **추적 기록**일 뿐입니다. 어떤 행을 실적으로 인정할지는 고객 확인
    사항입니다(``CUSTOMER_DATA_QUESTIONS.md`` Q5-8).

.. note::
    **왜 별도 테이블인가.** 적재되지 않은 행은 정의상 ``purchase`` 에 넣을 수
    없습니다(넣으면 계산 대상이 되어 버립니다). 그렇다고 응답에만 담으면
    화면을 닫는 순간 사라집니다 — STEP 11 실데이터 리허설에서 실제로 130행이
    이렇게 보이지 않았습니다. 그래서 ``purchase`` 를 건드리지 않고 **기록만
    남기는** 테이블을 따로 둡니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

#: 금액이 0 이하라 현재 저장 규칙에 걸린 행.
#:
#: ⚠️ 사유 코드는 **무슨 일이 있었는지**를 적은 것이지, 업무 판단이 아닙니다.
REASON_NON_POSITIVE_AMOUNT: Final = "NON_POSITIVE_AMOUNT"

#: 필수값이 비어 있어 저장하지 못한 행.
REASON_MISSING_REQUIRED: Final = "MISSING_REQUIRED"

#: 값을 해석하지 못한 행(날짜·금액 형식 등).
REASON_UNPARSABLE: Final = "UNPARSABLE"

#: 위 어디에도 들어가지 않는 경우. 원문 메시지를 그대로 봅니다.
REASON_OTHER: Final = "OTHER"

#: 기록 가능한 사유 코드.
REJECTION_REASONS: Final[frozenset[str]] = frozenset(
    {
        REASON_NON_POSITIVE_AMOUNT,
        REASON_MISSING_REQUIRED,
        REASON_UNPARSABLE,
        REASON_OTHER,
    }
)

#: 사유 코드의 한국어 표시. ⛔ "제외" 같은 확정 표현을 쓰지 않습니다.
REJECTION_REASON_LABELS: Final[dict[str, str]] = {
    REASON_NON_POSITIVE_AMOUNT: "금액이 0 이하 (처리 방식 확인 필요)",
    REASON_MISSING_REQUIRED: "필수값 누락",
    REASON_UNPARSABLE: "값을 해석하지 못함",
    REASON_OTHER: "기타",
}


@dataclass(frozen=True, kw_only=True)
class ImportRejection:
    """적재되지 않은 원본 행 하나.

    Attributes:
        batch_id: 이 행이 들어 있던 업로드 배치 ID. 배치 없이 적재한 경우
            ``None``.
        row_number: 원본 파일에서의 행 번호(1부터). 담당자가 원본을 열어
            같은 행을 찾을 수 있어야 합니다.
        reason: :data:`REJECTION_REASONS` 중 하나.
        message: Importer 가 남긴 원문 사유. 여러 개면 줄바꿈으로 이어 둡니다.
        business_no: 원본의 사업자등록번호(정규화 전).
        company_name: 원본의 거래처명.
        description: 원본의 적요.
        budget_account: 원본의 예산과목.
        amount: 원본의 금액. **그대로 보존합니다** — 음수도 그대로입니다.
        resolution_date: 원본의 결의일자.
        issue_date: 원본의 신고기준일.
        rejection_id: 내부 고유 ID. 저장 전에는 ``None``.
        created_at: 기록 시각. 저장 시 채워집니다.
    """

    row_number: int
    reason: str
    message: str = ""
    batch_id: int | None = None
    business_no: str | None = None
    company_name: str | None = None
    description: str | None = None
    budget_account: str | None = None
    amount: Decimal | None = None
    resolution_date: date | None = None
    issue_date: date | None = None
    rejection_id: int | None = None
    created_at: datetime | None = None

    @property
    def reason_label(self) -> str:
        """사유의 한국어 표시."""
        return REJECTION_REASON_LABELS.get(self.reason, self.reason)


@dataclass(frozen=True, kw_only=True)
class ImportTrace:
    """한 배치의 **원본 → 적재 → 미적재** 대조표.

    ⛔ 어떤 행이 옳은지 판단하지 않습니다. 숫자가 서로 맞는지만 보여 줍니다.

    Attributes:
        source_rows: Importer 에 **넘긴** 원본 행 수. 세어서 기록한 값이며,
            적재/미적재 합계로 계산한 값이 **아닙니다** — 그래야 둘을 맞대어
            볼 수 있습니다.
        batch_id: 대상 배치 ID. 배치가 없으면 ``None``.
        file_name: 원본 파일명.
        stored: DB-1 에 적재된 행 수.
        rejected: 적재되지 않아 기록만 남은 행 수.
        reasons: 사유별 미적재 행 수.
    """

    source_rows: int = 0
    batch_id: int | None = None
    file_name: str = ""
    stored: int = 0
    rejected: int = 0
    reasons: dict[str, int] | None = None

    @property
    def unexplained(self) -> int:
        """설명되지 않은 행 수.

        ⚠️ 0 이 아니면 **어딘가에서 행이 조용히 사라진 것**입니다. 어느 쪽으로
        갔는지 모르는 행이 있다는 뜻이므로 그대로 두면 안 됩니다.
        """
        return self.source_rows - self.stored - self.rejected

    @property
    def complete(self) -> bool:
        """모든 원본 행이 설명되는가(적재되었거나, 사유와 함께 기록되었는가)."""
        return self.unexplained == 0
