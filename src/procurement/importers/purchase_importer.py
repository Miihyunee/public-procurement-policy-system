"""
procurement.importers.purchase_importer

구매(지출) 데이터를 검증·정규화하여 적재하고, 기업과 연결하는 서비스입니다.

처리 흐름 (``docs/PURCHASE_IMPORT_DESIGN.md`` 3장)::

    원본 행 → 값 정규화 → Validation → Company 연결 → Purchase 저장

각 행은 다음 세 가지로 구분됩니다.

- ``IMPORTED``: 정상 적재
- ``WARNING``: 저장했으나 확인 필요(체크섬·자릿수 보정·미매칭 등)
- ``FAILED``: 검증 실패로 적재하지 않음

.. note::
    본 서비스는 Repository 를 통해서만 데이터에 접근하며 SQL 을 직접 다루지
    않습니다. 한 행이 실패해도 예외를 던지지 않고 다음 행을 계속 처리합니다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import (
    PurchaseRepository,
    PurchaseValidationError,
)
from procurement.matchers.business_no import normalize_business_no
from procurement.models.purchase import Purchase

#: 업체명이 비어 있을 때 사용하는 대체값. 사업자번호가 있으면 계산은 가능하므로
#: 업체명 때문에 행을 버리지 않는다.
UNKNOWN_COMPANY_NAME = "(미상)"

#: 지원하는 날짜 문자열 형식
_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y.%m.%d")

#: 금액에서 제거할 문자(천단위 구분자·통화기호·공백)
_AMOUNT_NOISE_PATTERN = re.compile(r"[,\s원₩]")


class ImportStatus(Enum):
    """행 단위 적재 결과 상태."""

    IMPORTED = "IMPORTED"
    WARNING = "WARNING"
    FAILED = "FAILED"


@dataclass(frozen=True, kw_only=True)
class ImportRowResult:
    """행 하나의 적재 결과.

    Attributes:
        row_number: 입력 순서(1부터). 고객 파일의 행을 지목할 때 사용합니다.
        status: 적재 결과.
        business_no: 정규화된 사업자등록번호. 실패 시 ``None``.
        messages: 경고·실패 사유. 고객에게 설명할 수 있는 문장으로 남깁니다.
        purchase_id: 저장된 구매 ID. 저장하지 않았으면 ``None``.
        matched: 기업 연결 성공 여부.
    """

    row_number: int
    status: ImportStatus
    business_no: str | None = None
    messages: list[str] = field(default_factory=list)
    purchase_id: int | None = None
    matched: bool = False

    @property
    def is_stored(self) -> bool:
        """DB 에 저장되었는지 여부."""
        return self.status is not ImportStatus.FAILED


@dataclass(frozen=True, kw_only=True)
class ImportReport:
    """적재 결과 전체 리포트.

    Attributes:
        rows: 행별 결과.
    """

    rows: list[ImportRowResult] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        """입력된 전체 행 수."""
        return len(self.rows)

    @property
    def imported_count(self) -> int:
        """경고 없이 정상 적재된 행 수."""
        return sum(1 for row in self.rows if row.status is ImportStatus.IMPORTED)

    @property
    def warning_count(self) -> int:
        """저장되었으나 확인이 필요한 행 수."""
        return sum(1 for row in self.rows if row.status is ImportStatus.WARNING)

    @property
    def failed_count(self) -> int:
        """검증 실패로 저장하지 않은 행 수."""
        return sum(1 for row in self.rows if row.status is ImportStatus.FAILED)

    @property
    def stored_count(self) -> int:
        """실제로 저장된 행 수(정상 + 경고)."""
        return self.imported_count + self.warning_count

    @property
    def matched_count(self) -> int:
        """기업 연결에 성공한 행 수."""
        return sum(1 for row in self.rows if row.matched)

    @property
    def unmatched_count(self) -> int:
        """저장되었으나 기업 연결에 실패한 행 수."""
        return sum(1 for row in self.rows if row.is_stored and not row.matched)

    @property
    def match_rate(self) -> Decimal:
        """저장된 행 기준 기업 매칭률(%).

        **달성률의 신뢰도 지표**입니다. 매칭률이 낮으면 정책 실적이 실제보다
        적게 집계되어 달성률이 낮게 나옵니다.
        """
        if self.stored_count == 0:
            return Decimal("0")
        return (Decimal(self.matched_count) / Decimal(self.stored_count) * 100).quantize(
            Decimal("0.01")
        )

    def failed_rows(self) -> list[ImportRowResult]:
        """적재하지 못한 행 목록."""
        return [row for row in self.rows if row.status is ImportStatus.FAILED]

    def format_report(self) -> str:
        """콘솔·보고용 요약 문자열을 만듭니다."""
        lines = [
            f"전체 행   : {self.total_count}",
            f"정상 적재 : {self.imported_count}",
            f"경고      : {self.warning_count} (저장됨)",
            f"실패      : {self.failed_count} (저장 안 됨)",
            f"기업 매칭 : {self.matched_count} (매칭률 {self.match_rate}%)",
            f"미매칭    : {self.unmatched_count}",
        ]
        return "\n".join(lines)


class PurchaseImporter:
    """구매데이터를 검증·정규화하여 적재하고 기업과 연결합니다."""

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        company_repository: CompanyRepository,
    ) -> None:
        """Importer 를 초기화합니다.

        Args:
            purchase_repository: 구매실적 저장에 사용할 :class:`PurchaseRepository`.
            company_repository: 기업 조회에 사용할 :class:`CompanyRepository`.
        """
        self._purchase_repository = purchase_repository
        self._company_repository = company_repository

    def import_rows(
        self, rows: Iterable[Mapping[str, Any]], batch_id: int | None = None
    ) -> ImportReport:
        """컬럼 매핑이 끝난 행들을 적재합니다.

        각 행은 다음 키를 가질 수 있습니다.

        - ``business_no`` (필수), ``amount`` (필수)
        - ``contract_date`` (필수), ``payment_date`` (필수)
        - ``resolution_date`` (**선택**) — 결의일자. 표준 업로드 양식에서
          들어옵니다. 없으면 ``None`` 으로 저장하며, 기존 동작과 동일합니다.
        - ``issue_date`` (**선택**) — 세금계산서 발행일자(``신고기준일``).
          음수 상계 판정에 사용합니다. 없으면 ``None`` 으로 저장합니다.
        - ``description`` · ``budget_account`` (**선택**) — 적요 · 예산과목.
          **판정에 쓰지 않고 그대로 보관**합니다. 공란은 정상입니다.
        - ``company_name`` (없으면 ``"(미상)"`` 으로 대체하고 경고)

        한 행이 실패해도 중단하지 않고 다음 행을 계속 처리합니다.

        Args:
            rows: 행(매핑) 목록.
            batch_id: 이 적재가 속한 업로드 배치 ID. ``None`` 이면 배치 없이
                저장하며 **기존과 동일하게 동작**합니다(하위 호환).

        Returns:
            행별 결과와 집계를 담은 :class:`ImportReport`.
        """
        results = [
            self._import_row(row_number, row, batch_id)
            for row_number, row in enumerate(rows, start=1)
        ]
        return ImportReport(rows=results)

    def rematch(self) -> int:
        """미매칭 구매를 기업과 다시 연결합니다.

        구매데이터가 먼저 들어오고 기업정보가 나중에 수집되는 상황
        (``docs/PURCHASE_IMPORT_DESIGN.md`` 6.3절 "경우 B")을 위한 수단입니다.
        미매칭 건만 대상으로 하므로 **반복 실행해도 안전**하며, 이미 연결된
        구매는 건드리지 않습니다.

        Returns:
            새로 연결된 구매 건수.
        """
        from procurement.matchers.company_matcher import CompanyMatcher

        matcher = CompanyMatcher(self._company_repository, self._purchase_repository)
        return matcher.match_all()

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _import_row(
        self, row_number: int, row: Mapping[str, Any], batch_id: int | None = None
    ) -> ImportRowResult:
        """행 하나를 정규화·검증하고 저장합니다."""
        messages: list[str] = []

        normalized = normalize_business_no(row.get("business_no"))
        messages.extend(normalized.warnings)
        if not normalized.is_valid:
            return self._failed(row_number, messages or ["사업자등록번호를 확인할 수 없습니다."])
        business_no = normalized.value
        assert business_no is not None  # is_valid 가 보장

        contract_date, error = _parse_date(row.get("contract_date"), "계약일")
        if error is not None:
            return self._failed(row_number, [*messages, error], business_no)

        payment_date, error = _parse_date(row.get("payment_date"), "지급일")
        if error is not None:
            return self._failed(row_number, [*messages, error], business_no)

        # 결의일자는 선택 항목이다. 값이 없으면 None 으로 두고, 있는데 형식이
        # 틀리면 조용히 버리지 않고 실패로 처리한다.
        raw_resolution_date = row.get("resolution_date")
        resolution_date: date | None = None
        # datetime 은 date 의 하위형이므로 date 검사 하나로 둘 다 걸린다.
        if isinstance(raw_resolution_date, date) or _clean_text(raw_resolution_date):
            resolution_date, error = _parse_date(raw_resolution_date, "결의일자")
            if error is not None:
                return self._failed(row_number, [*messages, error], business_no)

        # 발행일자(신고기준일)도 같은 규칙이다 — 없으면 None, 있는데 형식이
        # 틀리면 실패. ⛔ 없는 값을 다른 날짜로 대체하지 않는다.
        raw_issue_date = row.get("issue_date")
        issue_date: date | None = None
        if isinstance(raw_issue_date, date) or _clean_text(raw_issue_date):
            issue_date, error = _parse_date(raw_issue_date, "신고기준일")
            if error is not None:
                return self._failed(row_number, [*messages, error], business_no)

        # 적요·예산과목은 판정에 쓰지 않고 그대로 보관한다. 공란은 정상이다.
        description = _clean_text(row.get("description")) or None
        budget_account = _clean_text(row.get("budget_account")) or None

        amount, error = _parse_amount(row.get("amount"))
        if error is not None:
            return self._failed(row_number, [*messages, error], business_no)

        assert contract_date is not None and payment_date is not None and amount is not None

        # 선지급·정산으로 실제 역전이 발생할 수 있어 거부하지 않고 경고만 남긴다.
        if contract_date > payment_date:
            messages.append(
                f"계약일({contract_date})이 지급일({payment_date})보다 늦습니다. 확인이 필요합니다."
            )

        company_name = _clean_text(row.get("company_name"))
        if not company_name:
            company_name = UNKNOWN_COMPANY_NAME
            messages.append("업체명이 비어 있어 '(미상)' 으로 저장했습니다.")

        company = self._company_repository.find_by_business_no(business_no)
        company_id = company.company_id if company is not None else None
        if company_id is None:
            messages.append(
                "등록된 기업을 찾지 못해 미매칭으로 저장했습니다. "
                "기업정보 수집 후 재매칭하면 연결됩니다."
            )

        try:
            saved = self._purchase_repository.insert(
                Purchase(
                    business_no=business_no,
                    company_name=company_name,
                    contract_date=contract_date,
                    payment_date=payment_date,
                    resolution_date=resolution_date,
                    issue_date=issue_date,
                    description=description,
                    budget_account=budget_account,
                    amount=amount,
                    company_id=company_id,
                    batch_id=batch_id,
                )
            )
        except PurchaseValidationError as exc:
            # 예: 금액 0 이하 — 현재 Repository 제약(D-003 과 충돌, 별도 Issue)
            return self._failed(row_number, [*messages, str(exc)], business_no)

        return ImportRowResult(
            row_number=row_number,
            status=ImportStatus.WARNING if messages else ImportStatus.IMPORTED,
            business_no=business_no,
            messages=messages,
            purchase_id=saved.purchase_id,
            matched=company_id is not None,
        )

    @staticmethod
    def _failed(
        row_number: int, messages: list[str], business_no: str | None = None
    ) -> ImportRowResult:
        """실패 결과를 만듭니다."""
        return ImportRowResult(
            row_number=row_number,
            status=ImportStatus.FAILED,
            business_no=business_no,
            messages=messages,
        )


def _clean_text(value: object) -> str:
    """값을 문자열로 바꾸고 앞뒤 공백을 제거합니다."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(value: object, label: str) -> tuple[date | None, str | None]:
    """날짜 값을 :class:`datetime.date` 로 변환합니다.

    Args:
        value: 변환할 값.
        label: 오류 메시지에 사용할 항목명(예: ``"계약일"``).

    Returns:
        ``(날짜, 오류 메시지)``. 성공하면 오류는 ``None`` 입니다.
    """
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None

    text = _clean_text(value)
    if not text:
        return None, f"{label}이(가) 비어 있습니다."

    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date(), None
        except ValueError:
            continue
    return None, f"{label} 형식을 인식할 수 없습니다: {text!r}"


def _parse_amount(value: object) -> tuple[Decimal | None, str | None]:
    """금액 값을 :class:`decimal.Decimal` 로 변환합니다.

    천단위 구분자·통화기호를 제거하고 변환하며, 부동소수 오차를 피하기 위해
    실수형은 문자열을 거쳐 변환합니다.

    Returns:
        ``(금액, 오류 메시지)``. 성공하면 오류는 ``None`` 입니다.
    """
    if isinstance(value, Decimal):
        return value, None
    if isinstance(value, bool):  # bool 은 int 의 하위형이라 먼저 걸러낸다.
        return None, "금액이 올바르지 않습니다."
    if isinstance(value, int):
        return Decimal(value), None
    if isinstance(value, float):
        return Decimal(str(value)), None

    text = _clean_text(value)
    if not text:
        return None, "구매금액이 비어 있습니다."

    cleaned = _AMOUNT_NOISE_PATTERN.sub("", text)
    try:
        return Decimal(cleaned), None
    except InvalidOperation:
        return None, f"구매금액을 숫자로 변환할 수 없습니다: {text!r}"
