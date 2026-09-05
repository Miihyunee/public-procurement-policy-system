"""
procurement.importers.company_importer

기업정보를 **어디서 가져왔든 같은 자리에** 넣습니다.

::

    파일 업로드 ─┐
                 ├→ CompanyRecord →  Company  →  (인증이 있으면) Certification
    조회 결과   ─┘

.. warning::
    ⛔ **가져오는 방법만 다르고, 넣는 자리와 판정은 하나입니다.** 방법별로
    다른 저장 구조나 판정 규칙을 만들지 않습니다. 이 모듈을 통과한 뒤부터는
    기존 매칭(:class:`~procurement.matchers.company_matcher.CompanyMatcher`) ·
    기존 판정 규칙 · 기존 계산기가 그대로 동작합니다.

.. warning::
    ⛔ **없는 값을 지어내지 않습니다.** 기업명이 없으면 그 행을 실패로
    돌려보내며, 사업자등록번호로 대신 채우지 않습니다. 대표자명은 선택값이라
    (🟢 2026-09-05 PM 확정) 비어 있으면 **비운 채로** 저장합니다 — "미상" ·
    "없음" 같은 값을 넣지 않습니다.

.. warning::
    ⛔ **이미 있는 기업을 덮어쓰지 않습니다.** 같은 사업자등록번호가 다시
    들어오면 **그대로 두고** 건너뜁니다 — 어느 쪽 값이 옳은지는 업무 판단이며
    정해진 바가 없기 때문입니다. 몇 번을 다시 올려도 기존 구매 연결과 인증이
    깨지지 않습니다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from procurement.core.open_ended_certification import allows_open_ended
from procurement.database.certification_repository import (
    CertificationRepository,
    CertificationValidationError,
)
from procurement.database.company_repository import (
    CompanyRepository,
    CompanyValidationError,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.matchers.business_no import normalize_business_no
from procurement.models.certification import Certification
from procurement.models.company import Company


class CompanyImportStatus(Enum):
    """기업 한 건의 처리 결과."""

    #: 새로 등록했다.
    CREATED = "CREATED"

    #: 이미 있어서 그대로 두었다. ⛔ 덮어쓰지 않는다.
    ALREADY_EXISTS = "ALREADY_EXISTS"

    #: 값이 모자라거나 잘못되어 넣지 않았다.
    FAILED = "FAILED"


@dataclass(frozen=True, kw_only=True)
class CompanyRecord:
    """**어디서 왔든 같은 모양**이 되는 기업정보 한 건.

    파일 한 행도, 조회 결과 한 건도 이 모양으로 바뀐 뒤에야 저장됩니다.

    Attributes:
        business_no: 사업자등록번호. 정규화 전 값이어도 됩니다.
        company_name: 기업명. **없으면 저장하지 않습니다.**
        representative_name: 대표자명. **선택값입니다** — 없으면 ``None``.
        policy_code: 인증 정책 코드. 인증까지 넣을 때만 채웁니다.
        valid_from: 인증 유효 시작일.
        valid_to: 인증 유효 종료일. 비어 있어도 되는 정책은
            :data:`~procurement.core.open_ended_certification.OPEN_ENDED_POLICY_CODES`
            뿐입니다 — 그 경우 시작일 이후로 계속 유효한 인증이 됩니다.
        source_row: 사용자에게 알려 줄 원본 행 번호(파일) 또는 순번(조회).
    """

    business_no: object
    company_name: str | None = None
    representative_name: str | None = None
    policy_code: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_row: int = 0

    @property
    def has_certification(self) -> bool:
        """인증까지 함께 들어왔는가."""
        return self.policy_code is not None


@dataclass(frozen=True, kw_only=True)
class CompanyRowResult:
    """행 하나의 처리 결과.

    Attributes:
        source_row: 원본 행 번호(또는 순번).
        status: 기업 처리 결과.
        business_no: 정규화된 사업자등록번호. 실패하면 ``None``.
        company_id: 저장되었거나 이미 있던 기업의 ID.
        certification_saved: 인증을 새로 저장했는가.
        messages: 사용자에게 보여 줄 경고·실패 사유.
    """

    source_row: int
    status: CompanyImportStatus
    business_no: str | None = None
    company_id: int | None = None
    certification_saved: bool = False
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class CompanyImportReport:
    """기업정보 적재 결과 전체.

    Attributes:
        source: 어디서 가져왔는지 — :data:`SOURCE_FILE` 또는 :data:`SOURCE_API`.
        rows: 행별 결과.
    """

    source: str
    rows: list[CompanyRowResult] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        """처리한 전체 건수."""
        return len(self.rows)

    @property
    def created_count(self) -> int:
        """새로 등록한 기업 수."""
        return sum(1 for row in self.rows if row.status is CompanyImportStatus.CREATED)

    @property
    def existing_count(self) -> int:
        """이미 있어서 그대로 둔 기업 수."""
        return sum(1 for row in self.rows if row.status is CompanyImportStatus.ALREADY_EXISTS)

    @property
    def failed_count(self) -> int:
        """넣지 못한 건수."""
        return sum(1 for row in self.rows if row.status is CompanyImportStatus.FAILED)

    @property
    def certification_count(self) -> int:
        """새로 저장한 인증 수."""
        return sum(1 for row in self.rows if row.certification_saved)

    def failed_rows(self) -> list[CompanyRowResult]:
        """넣지 못한 행 목록."""
        return [row for row in self.rows if row.status is CompanyImportStatus.FAILED]


#: 기업정보를 파일에서 가져왔다.
SOURCE_FILE = "FILE"

#: 기업정보를 조회로 가져왔다.
SOURCE_API = "API"

#: 사용할 수 있는 출처.
COMPANY_SOURCES: tuple[str, ...] = (SOURCE_FILE, SOURCE_API)


class CompanyImporter:
    """기업정보(그리고 함께 온 인증)를 저장합니다."""

    def __init__(
        self,
        company_repository: CompanyRepository,
        certification_repository: CertificationRepository,
        policy_repository: PolicyRepository,
    ) -> None:
        """Importer 를 초기화합니다.

        Args:
            company_repository: 기업 저장·조회에 사용할 저장소.
            certification_repository: 인증 저장에 사용할 저장소.
            policy_repository: 인증 종류를 정책으로 확인할 저장소.
        """
        self._companies = company_repository
        self._certifications = certification_repository
        self._policies = policy_repository

    def import_records(
        self,
        records: Iterable[CompanyRecord],
        *,
        source: str,
        policy_company_source_id: int | None = None,
    ) -> CompanyImportReport:
        """기업정보를 저장합니다.

        한 건이 실패해도 멈추지 않고 다음 건을 계속 처리합니다.

        Args:
            records: 저장할 기업정보. **출처와 무관하게 같은 모양**입니다.
            source: 어디서 가져왔는지(:data:`SOURCE_FILE` · :data:`SOURCE_API`).
                기록·표시에만 쓰이며 **판정에 쓰이지 않습니다.**
            policy_company_source_id: 이번 등록의 **버전 ID**. 저장하는 인증마다
                이 값을 달아, 나중에 «어느 파일에서 온 인증인가» 를 알 수 있게
                합니다(🟢 2026-09-05 고객 확정). 주지 않으면 어느 버전에도
                매이지 않아 항상 계산에 듭니다.

        Returns:
            행별 결과와 집계를 담은 :class:`CompanyImportReport`.

        Raises:
            ValueError: 알 수 없는 출처인 경우.
        """
        if source not in COMPANY_SOURCES:
            raise ValueError(f"알 수 없는 기업정보 출처입니다: {source!r}")
        return CompanyImportReport(
            source=source,
            rows=[self._import_one(record, policy_company_source_id) for record in records],
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _import_one(
        self, record: CompanyRecord, policy_company_source_id: int | None = None
    ) -> CompanyRowResult:
        """한 건을 저장합니다."""
        messages: list[str] = []

        normalized = normalize_business_no(record.business_no)
        messages.extend(normalized.warnings)
        if not normalized.is_valid or normalized.value is None:
            return self._failed(record, [*messages, "사업자등록번호를 확인할 수 없습니다."])
        business_no = normalized.value

        # ⛔ 없는 값을 지어내지 않는다 — 여기서 막지 않으면 근거 없는 기업 정보가
        #    DB 에 남는다.
        company_name = (record.company_name or "").strip()
        if not company_name:
            return self._failed(record, [*messages, "기업명이 없습니다."], business_no)
        # 🟢 2026-09-05 PM 확정: 대표자명은 **선택값**입니다. 비어 있으면
        #    ``None`` 으로 둡니다 — ⛔ "미상" 같은 값을 지어내지 않습니다.
        representative_name = (record.representative_name or "").strip() or None

        existing = self._companies.find_by_business_no(business_no)
        if existing is not None and existing.company_id is not None:
            company_id = existing.company_id
            status = CompanyImportStatus.ALREADY_EXISTS
            # ⛔ 덮어쓰지 않는다. 다른 값이 왔다는 사실만 알린다.
            if existing.company_name != company_name:
                messages.append(
                    f"이미 등록된 기업명과 다릅니다(등록: {existing.company_name}). "
                    "기존 값을 그대로 두었습니다."
                )
        else:
            try:
                saved = self._companies.insert(
                    Company(
                        business_no=business_no,
                        company_name=company_name,
                        representative_name=representative_name,
                    )
                )
            except CompanyValidationError as error:
                return self._failed(record, [*messages, str(error)], business_no)
            assert saved.company_id is not None
            company_id = saved.company_id
            status = CompanyImportStatus.CREATED

        certification_saved = False
        if record.has_certification:
            saved_certification, issue = self._save_certification(
                record, company_id, policy_company_source_id
            )
            certification_saved = saved_certification
            if issue is not None:
                messages.append(issue)

        return CompanyRowResult(
            source_row=record.source_row,
            status=status,
            business_no=business_no,
            company_id=company_id,
            certification_saved=certification_saved,
            messages=messages,
        )

    def _save_certification(
        self,
        record: CompanyRecord,
        company_id: int,
        policy_company_source_id: int | None = None,
    ) -> tuple[bool, str | None]:
        """인증을 저장합니다. 같은 인증이 이미 있으면 다시 넣지 않습니다."""
        assert record.policy_code is not None
        policy = self._policies.find_by_policy_code(record.policy_code)
        if policy is None or policy.policy_id is None:
            return False, (
                f"등록되지 않은 인증 종류입니다: {record.policy_code}. "
                "기업은 저장했고 인증만 넣지 않았습니다."
            )
        if record.valid_from is None:
            return False, "인증 유효기간이 없어 인증을 넣지 않았습니다."
        # 🟢 2026-09-04 고객 확정: 사회적기업·사회적협동조합만 종료일 없이
        #    계속 유효합니다. 그 외 정책에서 종료일이 비어 있으면 예전처럼
        #    넣지 않습니다 — 빠진 값이 조용히 "영원히 유효" 가 되면 안 됩니다.
        if record.valid_to is None and not allows_open_ended(record.policy_code):
            return False, "인증 유효기간이 없어 인증을 넣지 않았습니다."

        # 재실행 안전성 — 같은 (정책, 시작일, 종료일)이면 넣지 않는다.
        same = next(
            (
                row
                for row in self._certifications.find_by_company(company_id)
                if (row.policy_id, row.valid_from, row.valid_to)
                == (policy.policy_id, record.valid_from, record.valid_to)
            ),
            None,
        )
        if same is not None:
            # ⭐ 최신 목록에 **그대로 들어 있는** 기업이다. 인증 내용이 같아
            #    새로 저장하지는 않지만, 지금 자료에서 확인되었다는 표시는
            #    옮겨 준다 — 옮기지 않으면 목록에 있는 기업이 예전 버전에
            #    묶인 채 계산에서 빠진다.
            if (
                policy_company_source_id is not None
                and same.certification_id is not None
                and same.policy_company_source_id != policy_company_source_id
            ):
                self._certifications.assign_source(same.certification_id, policy_company_source_id)
            return False, None

        try:
            self._certifications.insert(
                Certification(
                    company_id=company_id,
                    policy_id=policy.policy_id,
                    policy_company_source_id=policy_company_source_id,
                    valid_from=record.valid_from,
                    valid_to=record.valid_to,
                )
            )
        except CertificationValidationError as error:
            return False, str(error)
        return True, None

    @staticmethod
    def _failed(
        record: CompanyRecord, messages: list[str], business_no: str | None = None
    ) -> CompanyRowResult:
        """실패 결과를 만듭니다."""
        return CompanyRowResult(
            source_row=record.source_row,
            status=CompanyImportStatus.FAILED,
            business_no=business_no,
            messages=messages,
        )
