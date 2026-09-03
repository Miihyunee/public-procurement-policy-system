"""
procurement.uploads.company_source_service

기업정보를 **두 가지 방법 중 하나로** 가져와 같은 자리에 넣습니다.

::

    ① 파일 업로드 ─→ 읽기 → 검증 ─┐
                                   ├→ CompanyRecord → CompanyImporter → Company
    ② 조회        ─→ 조회 결과   ─┘                                        ↓
                                                              기존 매칭 · 판정 · 계산

.. warning::
    ⛔ **두 방법은 "가져오는 곳" 만 다릅니다.** 저장 구조도, 매칭도, 인증 판정도
    하나입니다. 방법별 분기가 이 모듈 **밖으로 나가지 않습니다.**

.. warning::
    ⛔ **없는 값을 지어내지 않습니다.** 조회 결과가 기업명·대표자명을 주지 않는
    경우가 있는데(여성·장애인 확인), 그때는 그 건을 넣지 않고 **왜 넣지
    못했는지** 를 그대로 돌려줍니다.

.. note::
    파일은 **경로로 전달**받습니다 — 기존 구매 업로드와 같은 방식입니다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from procurement.collectors.client import FetchResult
from procurement.importers.company_importer import (
    SOURCE_API,
    SOURCE_FILE,
    CompanyImporter,
    CompanyImportReport,
    CompanyRecord,
)
from procurement.uploads.company_format import (
    POLICY_SCOPED_COMPANY_COLUMNS,
    STANDARD_COMPANY_COLUMNS,
)
from procurement.uploads.excel_adapter import ExcelReadError, read_standard_workbook
from procurement.uploads.validation import (
    ValidationReport,
    validate_headers,
    validate_rows,
)

#: 조회 결과가 기업명·대표자명을 주지 않아 기업을 만들 수 없을 때의 사유.
API_MISSING_COMPANY_FIELDS = (
    "조회 결과에 기업명 또는 대표자명이 없어 기업을 등록하지 않았습니다. "
    "⛔ 없는 값을 임의로 채우지 않습니다 — 기업정보 파일로 올려 주세요."
)


class CompanyApiClient(Protocol):
    """조회에 필요한 **딱 하나의 동작**만 요구하는 창구.

    ⛔ **새 조회 기능을 정의한 것이 아닙니다.** 기존
    :class:`~procurement.collectors.client.CertificationApiClient` 가 이미
    만족하는 모양을 그대로 적었을 뿐이며, 이 서비스가 클라이언트의 나머지
    기능에 손대지 않는다는 사실을 타입으로 못 박습니다.
    """

    def fetch(self, source: str, business_no: str, *, stdr_date: date | None = None) -> FetchResult:
        """확인서를 조회합니다."""
        ...


class CompanySourceService:
    """기업정보를 파일 또는 조회로 확보해 저장합니다."""

    def __init__(
        self,
        importer: CompanyImporter,
        api_client: CompanyApiClient | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            importer: 저장을 담당하는 :class:`CompanyImporter`.
            api_client: 조회에 사용할 **기존** 클라이언트. 파일 방식만 쓸 때는
                ``None`` 이어도 됩니다.
        """
        self._importer = importer
        self._api_client = api_client

    # ------------------------------------------------------------------
    # ① 파일 방식
    # ------------------------------------------------------------------
    def validate_file(self, file_path: str, *, policy_code: str | None = None) -> ValidationReport:
        """기업정보 파일을 읽어 **검증만** 합니다. ⛔ 저장하지 않습니다.

        Args:
            file_path: 읽을 ``.xlsx`` 경로.
            policy_code: 사용자가 **화면에서 고른** 정책. 주면 파일에 ``인증종류``
                칸이 없어도 되며, 그 파일 전체를 이 정책의 목록으로 봅니다
                (STEP 96 §5). ⛔ 파일 내용을 보고 정책을 추론하지 않습니다.

        Returns:
            :class:`ValidationReport`. 파일을 열 수 없으면 ``file_errors`` 에
            사유가 담깁니다.
        """
        columns = STANDARD_COMPANY_COLUMNS if policy_code is None else POLICY_SCOPED_COMPANY_COLUMNS
        try:
            workbook = read_standard_workbook(file_path)
        except ExcelReadError as error:
            return ValidationReport(file_errors=[str(error)])

        header_errors = validate_headers(workbook.headers, columns=columns)
        if header_errors:
            return ValidationReport(file_errors=header_errors, total_rows=workbook.row_count)

        return validate_rows(
            workbook.rows,
            first_row_number=workbook.first_row_number,
            columns=columns,
        )

    def import_file(
        self, file_path: str, *, policy_code: str | None = None
    ) -> tuple[ValidationReport, CompanyImportReport | None]:
        """기업정보 파일을 검증하고, **오류가 하나도 없을 때만** 저장합니다.

        구매 업로드와 같은 **"전부 검증 → 전부 저장"** 원칙입니다. 한 행이라도
        오류가 있으면 저장 계층을 호출조차 하지 않으므로 DB 에 아무 변화가
        없습니다.

        Args:
            file_path: 읽을 ``.xlsx`` 경로.
            policy_code: 사용자가 **화면에서 고른** 정책. 주면 파일의 모든 행을
                이 정책의 인증으로 저장합니다 — ⛔ 파일 안에 다른 인증명이 적혀
                있어도 **다른 정책에 등록하지 않습니다**(STEP 96 §5).

        Returns:
            ``(검증 결과, 적재 결과)``. 저장하지 않았으면 적재 결과는 ``None``.
        """
        report = self.validate_file(file_path, policy_code=policy_code)
        if not report.ok:
            return report, None

        records = [
            CompanyRecord(
                business_no=row.values["business_no"],
                company_name=_text(row.values.get("company_name")),
                representative_name=_text(row.values.get("representative_name")),
                # ⭐ 사용자가 고른 정책이 이긴다. 파일 값은 정책을 고르지 않았을
                #    때만 쓴다(기존 통합 양식 호환).
                policy_code=policy_code or _text(row.values.get("policy_code")),
                valid_from=_date(row.values.get("valid_from")),
                valid_to=_date(row.values.get("valid_to")),
                source_row=row.row_number,
            )
            for row in report.rows
        ]
        return report, self._importer.import_records(records, source=SOURCE_FILE)

    # ------------------------------------------------------------------
    # ② 조회 방식
    # ------------------------------------------------------------------
    def import_from_api(
        self, source: str, business_numbers: list[str], *, stdr_date: date
    ) -> CompanyImportReport:
        """기존 조회 기능으로 기업정보를 확보해 저장합니다.

        ⛔ **새 조회 기능을 만들지 않았습니다.** 기존
        :class:`~procurement.collectors.client.CertificationApiClient` 를 그대로
        호출하고, 그 결과를 파일 방식과 **같은 모양**으로 옮길 뿐입니다.

        Args:
            source: 조회 출처 식별자(기존 상수).
            business_numbers: 조회할 사업자등록번호 목록.
            stdr_date: 조회 기준일자. **기본값이 없습니다** — 호출자가
                명시해야 하며 코드가 오늘 날짜 등을 채우지 않습니다.

        Returns:
            :class:`CompanyImportReport`.

        Raises:
            RuntimeError: 조회 클라이언트가 준비되지 않은 경우.
        """
        if self._api_client is None:
            raise RuntimeError("조회 클라이언트가 설정되지 않았습니다.")

        records: list[CompanyRecord] = []
        skipped: list[CompanyRecord] = []
        for order, business_no in enumerate(business_numbers, start=1):
            result = self._api_client.fetch(source, business_no, stdr_date=stdr_date)
            for record in result.records:
                # ⛔ 조회가 주지 않은 값을 채우지 않는다. 없으면 넣지 않고
                #    사유를 그대로 남긴다(아래 실패 결과로 나간다).
                candidate = CompanyRecord(
                    business_no=record.business_no,
                    company_name=record.company_name,
                    representative_name=record.representative_name,
                    valid_from=record.valid_from,
                    valid_to=record.valid_to,
                    source_row=order,
                )
                (records if _has_company_fields(record) else skipped).append(candidate)

        report = self._importer.import_records(records, source=SOURCE_API)
        if not skipped:
            return report

        # 넣지 못한 건도 결과에 함께 담아 사용자가 이유를 보게 한다.
        missing = self._importer.import_records(skipped, source=SOURCE_API)
        rows = list(report.rows)
        for row in missing.rows:
            rows.append(
                type(row)(
                    source_row=row.source_row,
                    status=row.status,
                    business_no=row.business_no,
                    messages=[*row.messages, API_MISSING_COMPANY_FIELDS],
                )
            )
        return CompanyImportReport(source=SOURCE_API, rows=rows)


def _text(value: object) -> str | None:
    """문자열 값을 다듬습니다. 빈 값은 ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date(value: object) -> date | None:
    """검증 단계가 만든 날짜 값을 그대로 돌려줍니다."""
    return value if isinstance(value, date) else None


def _has_company_fields(record: object) -> bool:
    """조회 결과가 기업을 만들 수 있을 만큼 값을 주었는가."""
    name = getattr(record, "company_name", None)
    representative = getattr(record, "representative_name", None)
    return bool((name or "").strip()) and bool((representative or "").strip())
