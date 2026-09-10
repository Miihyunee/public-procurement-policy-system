"""
procurement.admin.policy_company_source_response

**정책별 기업정보 등록 현황**의 응답 스키마입니다(STEP 96 §2 · §17).

.. warning::
    ⛔ **미등록과 미해당을 섞지 않습니다.** ``registered`` 가 ``False`` 면
    조회불가이며, "해당 기업이 없다" 는 뜻이 아닙니다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

#: 기업정보를 받은 적이 있다.
REGISTERED = "REGISTERED"

#: 기업정보를 받은 적이 없다 → **조회불가**.
NOT_REGISTERED = "NOT_REGISTERED"

#: 등록을 시작했으나 **끝나지 않았다** → 아직 조회불가 (STEP 154).
#:
#: ⛔ 「등록완료」와 섞지 않습니다. 적재가 끊긴 자료로 계산하면 실적이 실제보다
#: 적게 나오고, 담당자는 화면만 보고 그 사실을 알 수 없습니다.
IN_PROGRESS = "IN_PROGRESS"


class ImportProgressModel(BaseModel):
    """지금 **돌고 있는** 등록 하나 (STEP 157).

    .. warning::
        ⛔ 이 값으로 계산하지 않습니다. 끝나지 않은 자료입니다.

        ⭐ 이미 «등록완료» 인 정책에 새 파일을 올리는 경우가 있습니다.
        그때 :class:`PolicyCompanySourceItemModel` 의 본체는 **끝난 자료**를
        그대로 말해야 하고(계산이 거기서 나오므로), 돌고 있는 등록은 이
        블록으로 따로 알립니다. 두 가지를 한 칸에 담으면 담당자가 어느
        쪽 숫자인지 알 수 없습니다.

    Attributes:
        source_label: 지금 올리고 있는 자료 표시(파일명 등).
        processed_count: 지금까지 **실제로 처리한** 행 수.
        total_count: 이번 등록의 전체 행 수. 아직 모르면 ``0``.
        progress_percent: 진행률(%). 전체 행 수를 모르면 ``0.0``.
    """

    model_config = ConfigDict(frozen=True)

    source_label: str | None = None
    processed_count: int = 0
    total_count: int = 0
    progress_percent: float = 0.0


class PolicyCompanySourceItemModel(BaseModel):
    """정책 하나의 기업정보 등록 현황.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명. ⛔ 화면이 정책명을 들고 있지 않도록 서버가 줍니다.
        registered: 기업정보를 **끝까지** 받은 적이 있는가. 진행 중이면
            ``False`` 입니다 — 아직 조회불가이기 때문입니다.
        status: ``REGISTERED`` / ``IN_PROGRESS`` / ``NOT_REGISTERED``.
        status_label: 화면 표시용 — "등록완료" / "등록 진행 중" / "미등록".
        source: 어디서 받았는지(``FILE`` / ``API``). 미등록이면 ``None``.
        source_label: 사용자가 알아볼 출처 표시(파일명 등). 없으면 ``None``.
        company_count: 확인한 기업 수. 미등록이면 ``None``.
        certification_count: 저장한 인증 수. 미등록이면 ``None``.
        processed_count: 지금까지 **실제로 처리한** 행 수(STEP 156).
            미등록이면 ``None``. ⛔ 짐작한 값이 아닙니다.
        total_count: 이번 등록의 전체 행 수. 미등록이면 ``None``.
        progress_percent: 진행률(%). 소수 첫째 자리까지.
            완료된 등록은 언제나 ``100.0`` 이며, 전체 행 수를 모르면
            ``0.0`` 입니다 — ⛔ 0 으로 나누지 않습니다.
        in_progress: 지금 **돌고 있는** 등록(STEP 157). 없으면 ``None``.
            ⭐ 이미 등록완료인 정책에 새 파일을 올리는 중일 때, 위의
            본체 값은 **끝난 자료**를 그대로 말하고 돌고 있는 쪽은
            여기로 알립니다. ⛔ 이 값으로 계산하지 않습니다.
        updated_at: 최종 등록 시각. 미등록이면 ``None``.
        available_methods: 이 정책에서 **고를 수 있는** 확보 방법.
            ⛔ 실제 조회가 구현되지 않은 정책에는 ``API`` 를 넣지 않습니다
            (STEP 96 §3).
    """

    model_config = ConfigDict(frozen=True)

    policy_id: int
    policy_code: str
    policy_name: str
    registered: bool
    status: str
    status_label: str
    source: str | None = None
    source_label: str | None = None
    company_count: int | None = None
    certification_count: int | None = None
    processed_count: int | None = None
    total_count: int | None = None
    progress_percent: float | None = None
    in_progress: ImportProgressModel | None = None
    updated_at: datetime | None = None
    available_methods: list[str] = []


def progress_percent_of(processed_count: int, total_count: int) -> float:
    """실제 처리 건수로 진행률을 냅니다 (STEP 156).

    ⛔ 시간으로 어림하지 않습니다. ⛔ 0 으로 나누지 않습니다.

    Args:
        processed_count: 지금까지 처리한 행 수.
        total_count: 전체 행 수.

    Returns:
        0.0 ~ 100.0. 전체 행 수를 모르면 ``0.0``.
    """
    if total_count <= 0:
        return 0.0
    return round(min(processed_count, total_count) / total_count * 100, 1)


class PolicyCompanySourceListModel(BaseModel):
    """정책별 기업정보 등록 현황 목록.

    Attributes:
        items: 활성 정책 전체. 미등록 정책도 **빼지 않습니다** — 화면이 등록
            버튼을 그리려면 목록 자체가 필요하고, "정책이 없음" 과 "기업정보를
            아직 받지 못함" 은 다른 상태이기 때문입니다.
    """

    model_config = ConfigDict(frozen=True)

    items: list[PolicyCompanySourceItemModel]
