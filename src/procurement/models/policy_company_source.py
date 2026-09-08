"""
procurement.models.policy_company_source

**정책별 기업정보가 등록되었는가**를 기록하는 모델입니다.

.. warning::
    ⛔ **"등록되지 않았다" 와 "해당 기업이 없다" 는 다릅니다.**

    이 기록이 없으면 그 정책은 **조회불가**입니다 — 어떤 사업자가 그 정책의
    기업인지 판단할 근거 자체가 없기 때문입니다. ⛔ 미해당으로도, 0원으로도
    처리하지 않습니다(STEP 96 §8).

    ::

        기록 있음 + 사업자번호가 목록에 있음   → 해당
        기록 있음 + 사업자번호가 목록에 없음   → 미해당
        기록 없음                              → 조회불가

.. note::
    ``Certification`` 이 0건이어도 이 기록이 있으면 **등록완료**입니다. 목록을
    받았는데 그 안에 우리 거래처가 하나도 없을 수 있고, 그것은 "판단할 수 없다"
    가 아니라 "전부 미해당" 이기 때문입니다.

.. warning::
    ⛔ **«활성» 과 «완료» 는 다릅니다** (STEP 154).

    ``is_active`` 는 *지금 계산에 쓰는 버전인가*, :attr:`import_status` 는
    *그 등록이 끝까지 갔는가* 입니다. 예전에는 버전 행이 만들어지는 순간
    바로 활성이 되어, 적재가 끝나지 않아도 화면이 「등록완료」라고 말했습니다.
    실제로 98,832행 중 일부만 들어간 채 그렇게 표시된 적이 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: 등록을 시작했고 **아직 끝나지 않았다.** ⛔ 활성이 될 수 없습니다.
IMPORT_IN_PROGRESS: Final = "IN_PROGRESS"

#: 적재가 **끝까지 갔다.** 이때만 활성이 될 수 있습니다.
IMPORT_COMPLETED: Final = "COMPLETED"


@dataclass(kw_only=True)
class PolicyCompanySource:
    """한 정책의 기업정보 등록 기록.

    Attributes:
        policy_id: 대상 정책 ID.
        source: 어디서 받았는지 — ``FILE`` 또는 ``API``. **표시·이력용**이며
            ⛔ 판정에 쓰이지 않습니다. 두 방법의 결과는 같습니다.
        company_count: 이번 등록에서 확인한 기업 수(신규 + 기존).
        certification_count: 이번 등록에서 새로 저장한 인증 수.
        source_label: 사용자가 알아볼 출처 표시(파일명 등). 없으면 ``None``.
        version: 이 정책의 **몇 번째 등록**인가. 1 부터 시작합니다.
        file_checksum: 올린 파일의 내용 지문. **같은 내용이면 새 버전을 만들지
            않습니다** — 파일명이 같아도 내용이 다르면 새 버전입니다.
            조회 방식(API)처럼 파일이 없으면 ``None``.
        is_active: 지금 **계산에 쓰는** 버전인가. 정책마다 하나만 ``True``.
            🟢 2026-09-05 고객 확정: *"기존 인증기업 데이터는 이력으로 보관하고,
            새 파일이 올라오면 그 파일을 최신으로 선택한다."*
        import_status: 적재가 끝났는가 — :data:`IMPORT_IN_PROGRESS` 또는
            :data:`IMPORT_COMPLETED`. ⛔ 끝나지 않은 버전은 활성이 되지 않고
            화면에도 「등록완료」로 적지 않습니다(STEP 154).
        completed_at: 적재가 끝난 시각. 끝나지 않았으면 ``None``.
        processed_count: 지금까지 **실제로 처리한** 행 수(STEP 156).
            ⛔ 짐작한 값이 아니라 적재 루프가 센 값입니다.
        total_count: 이번 등록의 전체 행 수. 아직 모르면 ``0``.
        policy_company_source_id: 내부 고유 ID. 저장 전에는 ``None``.
        registered_at: 등록 시각.
        updated_at: 최종 갱신 시각.
    """

    policy_id: int
    source: str
    company_count: int = 0
    certification_count: int = 0
    source_label: str | None = None
    version: int = 1
    file_checksum: str | None = None
    is_active: bool = True
    import_status: str = IMPORT_COMPLETED
    completed_at: datetime | None = None
    processed_count: int = 0
    total_count: int = 0
    policy_company_source_id: int | None = None
    registered_at: datetime | None = None
    updated_at: datetime | None = None
