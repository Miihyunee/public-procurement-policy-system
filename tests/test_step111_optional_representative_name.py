"""
대표자명은 **선택값**이다 — 기업을 식별하는 값은 기업명과 사업자등록번호 둘.

🟢 2026-09-05 PM 확정

    기업목록 등록 시 대표자명은 필수가 아니다. 기업 식별에 필요한 값은
    기업명과 사업자등록번호 둘이다. 대표자명은 선택값(nullable)로 처리한다.

실제 사회적기업 자료 6,128행 중 **1,491행에 대표자명이 없었고**, 그 때문에
등록되지 못한 거래처 5곳이 달성(115.0%)과 미달(86.6%)을 갈랐습니다.

무엇을 지키는가 (지시서 §3)
===========================

① 기업명 + 사업자번호 + 대표자명 → 정상 등록
② 기업명 + 사업자번호 + 대표자명 ``None`` → 정상 등록
③ 기업명 + 사업자번호 + 대표자명 빈값 → 정상 등록 (``None`` 으로 정규화)
④ 기업명 없음 → 실패
⑤ 사업자번호 없음 → 실패
⑥ 사업자번호 형식 오류 → 실패
⑦ 대표자명 없이 등록된 기업도 인증을 붙일 수 있다
⑧ 대표자명이 없어도 사업자등록번호로 구매와 정확 매칭된다

.. warning::
    ⛔ 없는 대표자명을 지어내지 않습니다 — "미상" · "없음" · "-" · "N/A" 는
    전부 시스템이 만들어낸 정보입니다. 빈 값은 비운 채로 둡니다.

.. note::
    합성 데이터만 씁니다. 실제 기업명·사업자등록번호는 넣지 않습니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import (
    CompanyRepository,
    CompanyValidationError,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값이며 실제 업체의 번호가 아닙니다.
_NO_REP = "1000000009"  # 대표자명이 없는 업체
_WITH_REP = "1000000014"  # 대표자명이 있는 업체


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "representative.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _company_file(path: Path, rows: list[list[object]]) -> Path:
    """정책을 고르고 올리는 표준 양식 파일."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _upload(client: TestClient, path: Path) -> dict[str, Any]:
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": "SOCIAL_ENTERPRISE"}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestTheRepositoryAcceptsAMissingName:
    """①②③ — 저장 계층이 대표자명 없이도 기업을 받는다."""

    def test_a_name_is_stored_as_given(self, db: Path) -> None:
        stored = CompanyRepository(db).insert(
            Company(business_no=_WITH_REP, company_name="합성기업", representative_name="가나다")
        )
        assert stored.representative_name == "가나다"

    def test_none_is_accepted(self, db: Path) -> None:
        stored = CompanyRepository(db).insert(
            Company(business_no=_NO_REP, company_name="합성기업", representative_name=None)
        )
        assert stored.company_id is not None
        assert stored.representative_name is None

    def test_the_field_defaults_to_none(self, db: Path) -> None:
        """대표자명을 아예 주지 않아도 된다 — 선택값이므로 기본값이 있다."""
        stored = CompanyRepository(db).insert(Company(business_no=_NO_REP, company_name="합성기업"))
        assert stored.representative_name is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_name_becomes_none(self, db: Path, blank: str) -> None:
        """③ 빈 값은 ``None`` 으로 맞춘다 — 빈 문자열과 ``None`` 이 섞이면
        읽는 쪽이 둘을 각각 다뤄야 한다."""
        stored = CompanyRepository(db).insert(
            Company(business_no=_NO_REP, company_name="합성기업", representative_name=blank)
        )
        assert stored.representative_name is None

        raw = CompanyRepository(db).execute("SELECT representative_name FROM company")
        assert [row["representative_name"] for row in raw] == [None]

    @pytest.mark.parametrize("company_name", [None, "", "   "])
    def test_a_missing_company_name_is_refused(self, db: Path, company_name: str | None) -> None:
        """④ 기업명은 여전히 필수다."""
        with pytest.raises(CompanyValidationError):
            CompanyRepository(db).insert(
                Company(
                    business_no=_NO_REP,
                    company_name=company_name,  # type: ignore[arg-type]
                    representative_name=None,
                )
            )

    @pytest.mark.parametrize("business_no", [None, "", "  "])
    def test_a_missing_business_no_is_refused(self, db: Path, business_no: str | None) -> None:
        """⑤ 사업자등록번호도 여전히 필수다."""
        with pytest.raises(CompanyValidationError):
            CompanyRepository(db).insert(
                Company(
                    business_no=business_no,  # type: ignore[arg-type]
                    company_name="합성기업",
                    representative_name=None,
                )
            )


class TestTheColumnIsNullable:
    """스키마가 실제로 ``NULL`` 을 받는가 — 그리고 구 DB 도 열리는가."""

    def test_the_column_is_not_null_free(self, db: Path) -> None:
        columns = CompanyRepository(db).execute("PRAGMA table_info(company)")
        notnull = {row["name"]: row["notnull"] for row in columns}
        assert notnull["business_no"] == 1
        assert notnull["company_name"] == 1
        assert notnull["representative_name"] == 0  # 선택값

    def test_an_old_database_opens_with_its_values_intact(self, tmp_path: Path) -> None:
        """구 스키마(``NOT NULL``)를 값을 바꾸지 않고 연다."""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE company (
                company_id INTEGER PRIMARY KEY,
                business_no TEXT UNIQUE NOT NULL,
                company_name TEXT NOT NULL,
                representative_name TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO company VALUES
                (3, '1000000014', '옛기업', '가나다',
                 '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """
        )
        conn.commit()
        conn.close()

        repository = CompanyRepository(path)
        repository.create_table()

        kept = repository.find_by_business_no(_WITH_REP)
        assert kept is not None
        assert kept.company_id == 3
        assert kept.company_name == "옛기업"
        assert kept.representative_name == "가나다"  # ⛔ 값이 바뀌지 않았다

        # 이제 대표자명 없는 기업도 들어간다.
        repository.insert(Company(business_no=_NO_REP, company_name="새기업"))
        assert repository.count() == 2

        tables = repository.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert "company_pre_optional_representative" not in {row["name"] for row in tables}


class TestTheUploadPathAcceptsAMissingName:
    """②③④⑤⑥ — 파일 업로드에서도 같다."""

    def test_a_blank_representative_column_is_accepted(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        path = _company_file(
            tmp_path / "companies.xlsx",
            [
                [_WITH_REP, "합성기업 가", "가나다", "2026-01-01", "2026-12-31"],
                [_NO_REP, "합성기업 나", None, "2026-01-01", "2026-12-31"],
            ],
        )
        result = _upload(client, path)

        assert result["stored"] is True
        assert result["created"] == 2
        assert result["failed"] == 0
        assert result["certifications"] == 2  # ⑦ 인증도 붙는다

        stored = CompanyRepository(db).find_by_business_no(_NO_REP)
        assert stored is not None
        assert stored.representative_name is None

    @pytest.mark.parametrize(
        ("business_no", "company_name"),
        [
            (_NO_REP, None),  # ④ 기업명 없음
            (None, "합성기업"),  # ⑤ 사업자번호 없음
            ("12345", "합성기업"),  # ⑥ 사업자번호 형식 오류
        ],
    )
    def test_the_two_required_values_are_still_required(
        self,
        client: TestClient,
        db: Path,
        tmp_path: Path,
        business_no: str | None,
        company_name: str | None,
    ) -> None:
        path = _company_file(
            tmp_path / "bad.xlsx",
            [[business_no, company_name, None, "2026-01-01", "2026-12-31"]],
        )
        result = _upload(client, path)

        assert result["stored"] is False
        assert result["issues"] != []
        assert CompanyRepository(db).count() == 0


class TestCertificationAndMatching:
    """⑦⑧ — 대표자명이 없어도 인증이 붙고, 사업자번호로 매칭된다."""

    def test_a_certification_attaches_to_a_nameless_company(self, db: Path) -> None:
        """⑦ 인증은 기업 ID 로 붙는다 — 대표자명과 무관하다."""
        company = CompanyRepository(db).insert(
            Company(business_no=_NO_REP, company_name="합성기업")
        )
        assert company.company_id is not None
        stored = CertificationRepository(db).insert(
            Certification(
                company_id=company.company_id,
                policy_id=_policy_id(db, "SOCIAL_ENTERPRISE"),
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )
        assert stored.certification_id is not None

    def test_it_matches_a_purchase_by_business_number_alone(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⑧ 매칭 키는 사업자등록번호뿐이다.

        ⛔ 기업명·대표자명·적요로 맞추지 않습니다. 그래서 대표자명이 비어
        있어도 매칭에 아무 영향이 없습니다.
        """
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_NO_REP,
                company_name="구매원본에 적힌 다른 이름",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("1000"),
            )
        )
        path = _company_file(
            tmp_path / "companies.xlsx",
            [[_NO_REP, "합성기업 나", None, "2026-01-01", "2026-12-31"]],
        )
        _upload(client, path)
        client.post("/purchases/rematch")

        matched = PurchaseRepository(db).find_all()[0]
        assert matched.company_id is not None

        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        row = next(r for r in payload["policies"] if r["policy_code"] == "SOCIAL_ENTERPRISE")
        assert row["purchase_amount"] == "1000"
        # 대표자명 없음은 조회불가 사유가 아니다 (§10).
        assert row["status"] != "COMPANY_DATA_NOT_REGISTERED"


class TestNothingIsInvented:
    """⛔ 없는 대표자명을 만들어 넣는 코드가 없다."""

    @pytest.mark.parametrize("invented", ['"미상"', '"없음"', '"N/A"', '"해당없음"'])
    def test_no_placeholder_is_assigned(self, invented: str) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert f"representative_name = {invented}" not in source, path.name
            assert f"representative_name={invented}" not in source, path.name
