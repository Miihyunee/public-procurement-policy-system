"""
procurement.models.certification

Certification 도메인 모델을 정의합니다.

Certification 은 기업이 보유한 정책 인증 정보를 담으며, Company 와 Policy 를
연결하는 핵심 엔티티입니다. 하나의 기업은 여러 개의 인증을 보유할 수 있습니다.
필드 구성은 ``docs/DATABASE_DESIGN.md`` 의 Certification 테이블을 그대로
따릅니다.

.. note::
    본 모델은 순수 데이터 컨테이너이며 비즈니스 로직을 포함하지 않습니다.
    영속화(저장/조회)는
    :class:`procurement.database.certification_repository.CertificationRepository`
    가 담당합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(kw_only=True)
class Certification:
    """기업 정책 인증 정보 모델.

    Attributes:
        company_id: Company 테이블 참조 ID (필수).
        policy_id: Policy 테이블 참조 ID (필수).
        valid_from: 인증 시작일 (필수).
        valid_to: 인증 종료일. **``None`` 을 허용합니다** — 종료일이 없는
            인증을 뜻하며, 시작일 이후로 **계속 유효**합니다.

            🟢 2026-09-04 고객 확정(STEP 108 §2): *사회적기업과
            사회적협동조합은 종료일이 없으며 계속 유효한 것으로 판단한다.*
            실제 진흥원 자료에 「인가일」만 있고 종료일 칸 자체가 없습니다.

            ⛔ 없는 종료일을 만들어 넣지 않습니다 — 인가일 + N년, 연말,
            ``9999-12-31`` 같은 값은 전부 시스템이 지어낸 규칙입니다.
        policy_company_source_id: 이 인증이 **어느 등록 버전에서 왔는지**.
            🟢 2026-09-05 고객 확정으로 정책마다 등록 버전이 생겼고, 계산은
            **활성 버전의 인증만** 씁니다. 예전 버전의 인증은 지워지지 않고
            이력으로 남습니다. 직접 넣은 인증은 ``None`` 이며, 그때는 어느
            버전에도 매이지 않아 **항상** 계산에 듭니다.
        cancelled_on: 인증이 **취소된 날짜**. ``None`` 이면 취소되지 않은
            — 즉 현재 유효한 — 인증입니다.

            🟢 2026-09-06 PM 확정(STEP 129 §1): *비어 있으면 현재 유효한
            인증으로 인정하고, 값이 있으면 취소된 인증 상태로 보관한다.*

            ⛔ 취소 **사유**를 구분하지 않습니다. 정상 발급 후 사후 변동으로
            취소된 것과 거짓·부정한 방법으로 발급되어 취소된 것을 현재
            버전은 같게 봅니다.
            ⛔ 이 날짜를 **실적 판정에 쓰지 않습니다.** 값이 있어도 과거
            거래는 그대로 셉니다(🟢 2026-09-06 PM 확정 · STEP 130 §2).
            소급 인정 기준은 향후 고도화 과제입니다.
        certificate_number: 인증서 번호 (선택).
        issuing_agency: 발급기관 (선택).
        certification_id: 내부 고유 ID (Primary Key). 저장 전에는 ``None`` 입니다.
        created_at: 데이터 생성일시. 저장 시 채워집니다.
        updated_at: 데이터 최종 수정일시. 저장 시 채워집니다.
    """

    company_id: int
    policy_id: int
    valid_from: date
    valid_to: date | None
    cancelled_on: date | None = None
    policy_company_source_id: int | None = None
    certificate_number: str | None = None
    issuing_agency: str | None = None
    certification_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_cancelled(self) -> bool:
        """취소된 인증인가.

        🟢 2026-09-06 PM 확정(STEP 129 §1)

        ==================  ==========================
        ``cancelled_on``    판정
        ==================  ==========================
        ``None``            취소되지 않음 — 유효
        날짜가 있음          취소됨
        ==================  ==========================

        파일에서 읽을 때 빈 셀 · 빈 문자열 · 공백은 모두 ``None`` 이 되므로,
        여기서는 값이 있는지만 봅니다.

        .. warning::
            ⛔ **이 값으로 실적을 빼지 않습니다.** 인증 데이터의 상태를 읽는
            것일 뿐입니다.

            🟢 2026-09-06 PM 확정(STEP 130 §2): *취소일자가 존재한다는
            이유만으로 과거 거래의 실적을 제외하지 않는다.* 취소일자만
            놓고는 정상적인 사후 변동인지, 거짓·부정한 발급인지, 효력이
            소급되는지를 가릴 수 없기 때문입니다.

        ⛔ 취소 **사유**를 구분하지 않습니다.
        ⛔ 취소일을 거래일자와 견주지 않습니다 — 과거 실적의 소급 인정 여부는
           향후 고도화 과제입니다(DECISIONS §0.51).
        """
        return self.cancelled_on is not None
