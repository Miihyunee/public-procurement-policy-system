"""
procurement.models.import_batch

ImportBatch 도메인 모델을 정의합니다.

ImportBatch 는 **한 번의 업로드 단위**를 나타냅니다. 매월 데이터를 누적으로
올리는 운영 방식에서, "이 행들이 어느 파일·어느 기간의 업로드로 들어왔는가"를
기록해 다음을 가능하게 합니다.

- 같은 달을 다시 올렸을 때 **이전 배치를 대체**(D-25 확정)
- 대체된 배치의 행을 **계산에서 제외**(행을 지우지 않고 상태로 구분)
- 언제 무엇이 대체되었는지 **추적**

.. note::
    본 모델은 순수 데이터 컨테이너입니다. 영속화는
    :class:`procurement.database.import_batch_repository.ImportBatchRepository`
    가 담당합니다.

.. warning::
    ``period_start`` / ``period_end`` 는 **호출자가 반드시 지정**합니다. 파일
    내용에서 자동으로 유추하지 않습니다 — 어느 날짜 컬럼으로 기간을 잡을지가
    **D-24 (미확정)** 에 종속되고, 대상 기간 결정 방식 자체도 아직 확정되지
    않았기 때문입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

#: 계산에 사용되는 배치
STATUS_ACTIVE = "ACTIVE"

#: 같은 기간의 새 배치로 대체된 배치. **계산에서 제외**됩니다.
STATUS_SUPERSEDED = "SUPERSEDED"

#: 허용되는 배치 상태. 지금 쓰이지 않는 상태를 미리 만들지 않습니다.
ALLOWED_STATUSES: frozenset[str] = frozenset({STATUS_ACTIVE, STATUS_SUPERSEDED})


@dataclass(kw_only=True)
class ImportBatch:
    """업로드 단위(배치) 모델.

    Attributes:
        file_name: 원본 파일명 (필수).
        period_start: 대상 기간 시작일 (필수). 호출자가 지정합니다.
        period_end: 대상 기간 종료일 (필수). 호출자가 지정합니다.
        file_hash: 원본 파일 내용 해시. 같은 파일 재업로드 감지에 사용하며
            선택 항목입니다.
        row_count: 이 배치로 적재된 행 수. 적재 후 갱신됩니다.
        total_amount: 이 배치의 금액 합계. 적재 후 갱신됩니다.
        status: 배치 상태(``ACTIVE`` / ``SUPERSEDED``).
        superseded_by: 이 배치를 대체한 배치 ID. 대체되지 않았으면 ``None``.
        uploaded_at: 업로드 시각. 저장 시 채워집니다.
        batch_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    file_name: str
    period_start: date
    period_end: date
    file_hash: str | None = None
    row_count: int = 0
    total_amount: Decimal = Decimal("0")
    status: str = STATUS_ACTIVE
    superseded_by: int | None = None
    uploaded_at: datetime | None = None
    batch_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """계산에 사용되는 배치인지 여부."""
        return self.status == STATUS_ACTIVE
