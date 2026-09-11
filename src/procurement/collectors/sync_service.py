"""
procurement.collectors.sync_service

조회한 확인서를 **기존** :class:`~procurement.models.certification.Certification`
구조에 연결해 저장합니다.

이 계층이 하는 일은 "가져온 것을 기존 자리에 넣는 것" 뿐입니다. 새 업무 규칙을
만들지 않습니다. 특히 다음은 **건드리지 않습니다.**

- 여성기업 · 장애인기업 판정 기준
- 창업기업 인증기간 해석 (``confmdoc_isu_dt`` 의 의미 포함)
- 녹색제품 기준
- 정책 목표율 · 달성률 계산식

저장하지 않고 건너뛰는 경우
==========================

**기업이 아직 등록되어 있지 않으면 저장하지 않습니다.**
:class:`~procurement.models.company.Company` 는 ``company_name`` 과
``representative_name`` 이 **필수(NOT NULL)** 인데, 여성·장애인 확인 API 는 둘 다
제공하지 않습니다(``docs/DATA_ACQUISITION_PLAN.md`` §2.2.0.1). 없는 값을 코드가
지어내면 근거 없는 기업 정보가 DB 에 남습니다. 따라서 이 서비스는 **기존 기업에
연결만** 하고, 없으면 :data:`SKIP_COMPANY_NOT_FOUND` 로 보고합니다.

스키마를 바꿔 해결할지(``representative_name`` nullable 전환) 여부는 PM 결정
사항이며 이번 작업 범위 밖입니다.

기업을 **만들어야** 할 때
------------------------

기업 자체를 등록하는 일은 이 모듈이 하지 않습니다.
:class:`~procurement.uploads.company_source_service.CompanySourceService` 가
파일·조회 **두 방법 모두**에 대해
:class:`~procurement.importers.company_importer.CompanyImporter` 로 보냅니다.

::

    기업 등록 → CompanySourceService → CompanyImporter → Company
    인증 연결 → CertificationSyncService(이 모듈) → Certification

⛔ **기업 생성 규칙을 두 곳에 두지 않습니다.** 조회 결과가 기업명·대표자명을
주면 ``CompanySourceService.import_from_api`` 가 기업을 만들고, 주지 않으면
만들지 않고 사유를 돌려줍니다. 이 모듈은 그 뒤 **이미 있는 기업에 인증을
연결**하는 역할 그대로입니다 — 그래서 여기에는 변경이 없습니다.

.. note::
    같은 인증을 두 번 저장하지 않습니다. ``(정책, 유효기간 시작일, 종료일)`` 이
    같은 인증이 이미 있으면 건너뜁니다. 이는 업무 규칙이 아니라 **재실행 안전성**
    을 위한 처리입니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from procurement.collectors.client import (
    SOURCE_POLICY_CODES,
    CertificationApiClient,
)
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.models.certification import Certification

#: 해당 사업자번호의 기업이 DB 에 없어 연결하지 못한 경우.
SKIP_COMPANY_NOT_FOUND = "COMPANY_NOT_FOUND"

#: 출처가 가리키는 정책이 DB 에 없는 경우.
SKIP_POLICY_NOT_FOUND = "POLICY_NOT_FOUND"


class PolicyNotRegisteredError(LookupError):
    """조회 출처에 대응하는 정책이 DB 에 없습니다."""


@dataclass(frozen=True, kw_only=True)
class SyncResult:
    """한 사업자번호 · 한 출처에 대한 저장 결과.

    Attributes:
        business_no: 조회에 사용한 사업자등록번호.
        source: 조회 출처 식별자.
        policy_code: 대응 정책 코드.
        fetched: API 가 준 확인서 건수.
        saved: 새로 저장한 인증 건수.
        skipped_duplicate: 이미 같은 인증이 있어 건너뛴 건수.
        skip_reason: 아무것도 저장하지 못한 이유. 정상이면 ``None``.
        warnings: 사업자등록번호 정규화 경고 등(데이터는 버리지 않습니다).
    """

    business_no: str
    source: str
    policy_code: str
    fetched: int = 0
    saved: int = 0
    skipped_duplicate: int = 0
    skip_reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class CertificationSyncService:
    """API 조회 → 파싱 → ``Certification`` 저장을 연결합니다.

    Args:
        client: :class:`~procurement.collectors.client.CertificationApiClient`.
        company_repository: 사업자번호로 기업을 찾는 데 사용합니다.
        policy_repository: 정책 코드를 ``policy_id`` 로 바꾸는 데 사용합니다.
        certification_repository: 저장 대상.
    """

    def __init__(
        self,
        *,
        client: CertificationApiClient,
        company_repository: CompanyRepository,
        policy_repository: PolicyRepository,
        certification_repository: CertificationRepository,
    ) -> None:
        """서비스를 초기화합니다."""
        self._client = client
        self._companies = company_repository
        self._policies = policy_repository
        self._certifications = certification_repository

    def sync_one(
        self,
        source: str,
        business_no: str,
        *,
        stdr_date: date | None,
    ) -> SyncResult:
        """사업자번호 한 건을 조회해 저장합니다.

        Args:
            source: 조회 출처 식별자
                (:data:`~procurement.collectors.client.SOURCE_WOMAN` 등).
            business_no: 조회할 사업자등록번호.
            stdr_date: 기준일자. **기본값이 없습니다.** 호출자가 명시적으로
                전달해야 하며, 코드가 오늘 날짜 등을 임의로 채우지 않습니다.

        Returns:
            :class:`SyncResult`.

        Raises:
            PolicyNotRegisteredError: 대응 정책이 DB 에 없는 경우.
            StdrDateRequiredError: 기준일이 필요한 조회인데 전달되지 않은 경우.
            ApiKeyNotConfiguredError · Api\\*Error: 호출·파싱 계층의 오류를
                그대로 올립니다. 이 계층에서 삼키지 않습니다.
        """
        policy_code = SOURCE_POLICY_CODES.get(source)
        if policy_code is None:
            raise ValueError(f"알 수 없는 조회 출처입니다: {source!r}")

        policy = self._policies.find_by_policy_code(policy_code)
        if policy is None or policy.policy_id is None:
            raise PolicyNotRegisteredError(
                f"정책 {policy_code} 가 등록되어 있지 않습니다. "
                "`python -m procurement init` 으로 기본 정책을 생성하세요."
            )

        result = self._client.fetch(source, business_no, stdr_date=stdr_date)

        warnings: list[str] = []
        for record in result.records:
            warnings.extend(record.business_no_warnings)

        if not result.records:
            return SyncResult(
                business_no=business_no,
                source=source,
                policy_code=policy_code,
                fetched=0,
                warnings=tuple(warnings),
            )

        # 응답이 준 사업자번호(정규화값)로 기업을 찾습니다. 여성·장애인 확인은
        # 응답에 사업자번호가 없어 요청값이 그대로 들어옵니다.
        normalized = result.records[0].business_no
        company = self._companies.find_by_business_no(normalized)
        if company is None or company.company_id is None:
            return SyncResult(
                business_no=business_no,
                source=source,
                policy_code=policy_code,
                fetched=len(result.records),
                skip_reason=SKIP_COMPANY_NOT_FOUND,
                warnings=tuple(warnings),
            )

        existing = {
            (row.policy_id, row.valid_from, row.valid_to)
            for row in self._certifications.find_by_company(company.company_id)
        }

        saved = 0
        duplicates = 0
        for record in result.records:
            key = (policy.policy_id, record.valid_from, record.valid_to)
            if key in existing:
                duplicates += 1
                continue
            self._certifications.insert(
                Certification(
                    company_id=company.company_id,
                    policy_id=policy.policy_id,
                    valid_from=record.valid_from,
                    valid_to=record.valid_to,
                    certificate_number=record.certificate_number,
                    issuing_agency=record.issuing_agency,
                )
            )
            existing.add(key)
            saved += 1

        return SyncResult(
            business_no=business_no,
            source=source,
            policy_code=policy_code,
            fetched=len(result.records),
            saved=saved,
            skipped_duplicate=duplicates,
            warnings=tuple(warnings),
        )
