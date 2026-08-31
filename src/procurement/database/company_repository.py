"""
procurement.database.company_repository

Company 엔티티의 영속화(저장/조회)를 담당하는 Repository.

:class:`procurement.database.base.BaseRepository` 를 상속하며, SQLite 표준 SQL
만 사용합니다. 테이블 컬럼은 ``docs/DATABASE_DESIGN.md`` 의 Company 정의를
그대로 따르고, 설계에 없는 컬럼은 추가하지 않습니다.

.. note::
    본 Repository 는 Foundation 단계 범위로, Insert/조회/집계만 제공합니다.
    Update/Delete 및 비즈니스 로직은 이후 Issue 에서 구현합니다.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from procurement.core.business_no_storage import to_storage_business_no
from procurement.database.base import BaseRepository
from procurement.models.company import Company


class CompanyValidationError(ValueError):
    """필수값 누락 등 Company 데이터 검증 실패 시 발생하는 예외."""


class DuplicateBusinessNoError(Exception):
    """이미 등록된 사업자등록번호로 저장을 시도할 때 발생하는 예외."""


# DATABASE_DESIGN.md 의 Company 테이블 정의를 그대로 반영한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS company (
    company_id INTEGER PRIMARY KEY,
    business_no TEXT UNIQUE NOT NULL,
    company_name TEXT NOT NULL,
    representative_name TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (business_no 는 UNIQUE 제약과 별개로 NOT NULL/비어있지 않아야 함)
_REQUIRED_FIELDS = ("business_no", "company_name", "representative_name")

#: 저장된 값에 구분자가 남아 있는가 — **정리 대상이 있는지 빠르게 보는 조건**.
#:
#: ⚠️ 하이픈·공백·마침표만 봅니다. 판정은
#: :func:`~procurement.matchers.business_no.to_storage_business_no` 가 하며,
#: 이 조건은 "훑어볼 필요가 있는가" 만 가릅니다.
_HAS_SEPARATOR_SQL = (
    "business_no <> REPLACE(REPLACE(REPLACE(business_no, '-', ''), ' ', ''), '.', '')"
)


@dataclass(frozen=True, kw_only=True)
class BusinessNoFormatSurvey:
    """기업 사업자등록번호 **표기 현황**. ⛔ 아무것도 바꾸지 않는 조사 결과입니다.

    Attributes:
        total: 기업 전체 건수.
        with_hyphen: 하이픈 등 구분자가 들어 있는 건수.
        with_space: 공백이 들어 있는 건수.
        digits_only: 구분자 없이 숫자만 저장된 건수.
        conflicting: 구분자를 지우면 **다른 행과 같아지는** 건수.
            ⛔ 이 행들은 자동으로 합치지 않습니다 — :meth:`CompanyRepository.
            find_normalization_conflicts` 로 확인해 사람이 판단합니다.
    """

    total: int
    with_hyphen: int
    with_space: int
    digits_only: int
    conflicting: int


@dataclass(frozen=True, kw_only=True)
class BusinessNoConflict:
    """구분자를 지우면 같은 번호가 되는 기업들.

    Attributes:
        business_no: 구분자를 지운 값.
        companies: 그 값으로 모이는 기존 기업들(2건 이상).
    """

    business_no: str
    companies: tuple[Company, ...]


@dataclass(frozen=True, kw_only=True)
class NormalizationPlan:
    """저장 형식 정리 계획.

    Attributes:
        changed: 바꿀 수 있는(또는 바꾼) 기업들 — ``(company_id, 이전, 이후)``.
        conflicts: 바꾸면 다른 기업과 같은 번호가 되어 **손대지 않은** 것들.
        applied: 실제로 DB 에 반영했는지 여부.
    """

    changed: tuple[tuple[int, str, str], ...]
    conflicts: tuple[BusinessNoConflict, ...]
    applied: bool


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


class CompanyRepository(BaseRepository):
    """Company 테이블에 대한 데이터 접근 계층."""

    table_name = "company"

    def create_table(self) -> None:
        """Company 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def insert(self, company: Company) -> Company:
        """기업을 등록하고 채번된 ``company_id`` 와 타임스탬프를 반영해 반환합니다.

        Args:
            company: 저장할 :class:`Company`. ``company_id`` 는 무시되고 자동 채번됩니다.

        Returns:
            ``company_id`` / ``created_at`` / ``updated_at`` 가 채워진 새 :class:`Company`.

        사업자등록번호는 **구분자를 지운 형태로 저장**합니다 —
        ``220-81-62517`` 을 넣어도 ``2208162517`` 로 남습니다. 구매 데이터가
        같은 형태로 적재되므로, 이렇게 해야 같은 사업자가 실제로 연결됩니다
        (:func:`~procurement.matchers.business_no.to_storage_business_no`).

        ⛔ **숫자를 고치거나 만들어내지 않습니다.** 자릿수·체크섬도 보지
        않습니다 — 지우는 것은 표기용 구분자뿐입니다.

        Raises:
            CompanyValidationError: 필수값(사업자번호·기업명·대표자명)이 비어 있는 경우.
                구분자만 들어온 값(예: ``"--"``)도 지우고 나면 비므로 여기서 걸립니다.
            DuplicateBusinessNoError: 동일한 사업자등록번호가 이미 존재하는 경우.
        """
        stored_business_no = to_storage_business_no(company.business_no)
        company = replace(company, business_no=stored_business_no)
        self._validate_required(company)

        now = datetime.now()
        created_at = company.created_at or now
        updated_at = company.updated_at or now

        sql = (
            "INSERT INTO company "
            "(business_no, company_name, representative_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        params = (
            company.business_no,
            company.company_name,
            company.representative_name,
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            try:
                cursor = conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise DuplicateBusinessNoError(
                    f"이미 등록된 사업자등록번호입니다: {company.business_no}"
                ) from exc
            new_id = cursor.lastrowid

        return Company(
            company_id=new_id,
            business_no=company.business_no,
            company_name=company.company_name,
            representative_name=company.representative_name,
            created_at=created_at,
            updated_at=updated_at,
        )

    def find_by_business_no(self, business_no: str) -> Company | None:
        """사업자등록번호로 기업을 조회합니다.

        ``220-81-62517`` 로 물어도 ``2208162517`` 로 저장된 기업을 찾습니다 —
        **찾는 쪽의 표기**를 저장 규칙과 같은 형태로 맞춰서 봅니다. 부르는 쪽이
        어느 표기를 들고 있는지에 따라 답이 달라지면 안 되기 때문입니다.

        ⛔ **부분 번호는 찾지 않습니다.** ``22081`` 은 사업자등록번호가 아니라
        검색어이며, 여기서 통하면 **엉뚱한 기업과 연결**됩니다. 부분 일치는
        검색 기능(:func:`~procurement.matchers.business_no.business_no_search_key`)의
        몫입니다.

        Args:
            business_no: 조회할 사업자등록번호. 구분자가 있어도 됩니다.

        Returns:
            일치하는 :class:`Company`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM company WHERE business_no = ?", (business_no,))
        if rows:
            return self._row_to_company(rows[0])

        # 저장된 값이 옛 표기(구분자 포함)일 수 있다. 기존 데이터를 건드리지
        # 않고도 연결되도록, 저장 규칙을 양쪽에 같이 적용해 한 번 더 본다.
        # ⚠️ 정리되지 않은 기존 행을 위한 경로다. 새로 넣는 값은 insert 에서
        #    이미 같은 형태가 되므로, 정리된 DB 에서는 아래 스캔이 돌지 않는다.
        wanted = to_storage_business_no(business_no)
        if not wanted:
            return None
        if wanted != business_no:
            # 물어본 쪽이 구분자를 들고 왔다 — 저장 규칙에 맞춰 한 번 더 본다.
            rows = self.execute("SELECT * FROM company WHERE business_no = ?", (wanted,))
            if rows:
                return self._row_to_company(rows[0])
        row = self._find_legacy_row(wanted)
        return self._row_to_company(row) if row is not None else None

    def _find_legacy_row(self, wanted: str) -> sqlite3.Row | None:
        """옛 표기로 저장된 행을 찾습니다. 없으면 ``None``.

        ⚠️ 전체를 훑기 전에 **구분자가 든 행이 있는지부터** 봅니다. 정리된
        DB 에서는 매칭에 실패할 때마다 기업 전체를 읽는 일이 없어야 합니다 —
        적재 한 번에 미매칭이 수천 건일 수 있습니다.

        같은 값으로 모이는 행이 둘 이상이면 ``company_id`` 가 작은 쪽을
        돌려줍니다. ⛔ 어느 쪽이 옳은지 **판단하지 않습니다** — 그 상황 자체가
        사람이 확인해야 할 충돌이며 :meth:`find_normalization_conflicts` 가
        알려 줍니다.
        """
        if not self.execute(f"SELECT 1 FROM company WHERE {_HAS_SEPARATOR_SQL} LIMIT 1"):
            return None
        for row in self.execute("SELECT * FROM company ORDER BY company_id"):
            if to_storage_business_no(row["business_no"]) == wanted:
                return row
        return None

    def find_by_id(self, company_id: int) -> Company | None:
        """company_id 로 기업을 조회합니다.

        Args:
            company_id: 조회할 내부 고유 ID.

        Returns:
            일치하는 :class:`Company`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM company WHERE company_id = ?", (company_id,))
        return self._row_to_company(rows[0]) if rows else None

    def exists(self, business_no: str) -> bool:
        """해당 사업자등록번호의 기업이 존재하는지 확인합니다.

        :meth:`find_by_business_no` 와 **같은 기준**으로 봅니다 — 한쪽은 찾고
        다른 쪽은 없다고 답하면 안 됩니다.

        Args:
            business_no: 확인할 사업자등록번호. 구분자가 있어도 됩니다.

        Returns:
            존재하면 ``True``, 아니면 ``False``.
        """
        rows = self.execute("SELECT 1 FROM company WHERE business_no = ? LIMIT 1", (business_no,))
        if rows:
            return True
        return self.find_by_business_no(business_no) is not None

    def count(self) -> int:
        """등록된 기업 수를 반환합니다.

        Returns:
            company 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM company")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 저장 형식 점검 — ⛔ 아무것도 바꾸지 않습니다
    # ------------------------------------------------------------------
    def survey_business_no_formats(self) -> BusinessNoFormatSurvey:
        """기업 사업자등록번호가 **어떤 표기로 저장되어 있는지** 셉니다.

        고치기 전에 무엇이 있는지부터 봅니다. 정리 대상이 몇 건이고 그중 몇
        건이 서로 부딪히는지 모르는 채로 일괄 변경하면, 되돌릴 수 없는 상태로
        기업이 뒤바뀔 수 있습니다.

        ⛔ **읽기만 합니다.**

        Returns:
            :class:`BusinessNoFormatSurvey`.
        """
        rows = self.execute("SELECT business_no FROM company")
        values = [str(row["business_no"]) for row in rows]
        grouped: dict[str, int] = defaultdict(int)
        for value in values:
            grouped[to_storage_business_no(value)] += 1

        return BusinessNoFormatSurvey(
            total=len(values),
            with_hyphen=sum(1 for value in values if "-" in value),
            with_space=sum(1 for value in values if any(char.isspace() for char in value)),
            digits_only=sum(1 for value in values if value == to_storage_business_no(value)),
            conflicting=sum(count for count in grouped.values() if count > 1),
        )

    def find_normalization_conflicts(self) -> list[BusinessNoConflict]:
        """구분자를 지우면 **같은 번호가 되는** 기업들을 찾습니다.

        ``A사 / 220-81-62517`` 과 ``A사 / 2208162517`` 이 함께 있으면 정리하는
        순간 UNIQUE 제약에 부딪힙니다. 그리고 **둘이 같은 회사인지 아닌지는
        시스템이 알 수 없습니다** — 이름이 달라도 같은 회사일 수 있고, 이름이
        같아도 잘못 입력된 남남일 수 있습니다.

        ⛔ **자동으로 합치지 않습니다.** 기업 병합은 사람이 결정합니다.

        Returns:
            충돌 묶음 목록. 없으면 빈 목록. ``business_no`` 순으로 정렬합니다.
        """
        grouped: dict[str, list[Company]] = defaultdict(list)
        for row in self.execute("SELECT * FROM company ORDER BY company_id"):
            grouped[to_storage_business_no(row["business_no"])].append(self._row_to_company(row))

        return [
            BusinessNoConflict(business_no=key, companies=tuple(companies))
            for key, companies in sorted(grouped.items())
            if len(companies) > 1
        ]

    def normalize_stored_business_numbers(self, *, apply: bool = False) -> NormalizationPlan:
        """옛 표기로 저장된 기업 번호를 정리합니다. **기본은 계획만 세웁니다.**

        ``apply=False`` (기본)면 **무엇을 바꿀지만** 돌려주고 DB 는 그대로
        둡니다. 실제 반영은 사람이 결과를 보고 ``apply=True`` 로 다시 불러야
        일어납니다 — 고객 DB 를 자동으로 고치지 않습니다.

        충돌하는 묶음(:meth:`find_normalization_conflicts`)은 **건드리지
        않습니다.** 정리하면 다른 기업과 같은 번호가 되므로, 어느 쪽을 남길지
        사람이 정해야 합니다.

        ⛔ 이 메서드는 **어디서도 자동 호출되지 않습니다.** 시작·부트스트랩·
        업로드 어느 경로에도 붙어 있지 않습니다.

        Args:
            apply: ``True`` 면 계획대로 ``UPDATE`` 합니다.

        Returns:
            :class:`NormalizationPlan` — 바꿀(바꾼) 것과 손대지 않은 충돌.
        """
        conflicts = self.find_normalization_conflicts()
        blocked = {conflict.business_no for conflict in conflicts}

        changed: list[tuple[int, str, str]] = []
        for row in self.execute("SELECT * FROM company ORDER BY company_id"):
            current = str(row["business_no"])
            wanted = to_storage_business_no(current)
            if wanted == current or not wanted or wanted in blocked:
                continue
            changed.append((int(row["company_id"]), current, wanted))

        if apply and changed:
            now = _to_db(datetime.now())
            with self.connection() as conn:
                for company_id, _, wanted in changed:
                    conn.execute(
                        "UPDATE company SET business_no = ?, updated_at = ? WHERE company_id = ?",
                        (wanted, now, company_id),
                    )

        return NormalizationPlan(
            changed=tuple(changed), conflicts=tuple(conflicts), applied=apply and bool(changed)
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _validate_required(self, company: Company) -> None:
        """필수 입력값이 비어 있지 않은지 검증합니다."""
        for field in _REQUIRED_FIELDS:
            value = getattr(company, field)
            if value is None or not str(value).strip():
                raise CompanyValidationError(f"필수값이 누락되었습니다: {field}")

    @staticmethod
    def _row_to_company(row: sqlite3.Row) -> Company:
        """SQLite Row 를 :class:`Company` 로 변환합니다."""
        return Company(
            company_id=row["company_id"],
            business_no=row["business_no"],
            company_name=row["company_name"],
            representative_name=row["representative_name"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
