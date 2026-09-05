"""
인증기업 자료의 **버전 관리** — 이력은 남기고, 계산은 최신만 본다.

🟢 2026-09-05 고객 확정

    기존 인증기업 데이터는 **이력으로 보관**한다. 새 인증기업 파일이
    업로드되면 그 파일을 그 정책의 **최신 데이터로 선택**한다. 현재 실적
    계산은 최신으로 선택된 파일의 기업정보를 기준으로 한다.

무엇을 지키는가 (지시서 §3 · §6~§9 · §15)
=========================================

1. 첫 파일 → 버전 1 · 활성.
2. 새 파일 → 버전 2 가 활성, 버전 1 은 **지워지지 않고** 비활성.
3. 계산은 **활성 버전의 인증만** 본다.
4. 최신 목록에서 빠진 기업은 계산에서 빠진다 — ⛔ DB 에서는 지우지 않는다.
5. 최신 목록에 새로 든 기업은 계산에 든다.
6. **같은 내용**을 다시 올리면 버전이 늘지 않는다(멱등).
7. 버전은 **정책마다 따로**다.
8. 인증기간이 바뀌면 두 기간 모두 남되, 계산은 **최신 것만** 쓴다.

.. warning::
    ⛔ 예전 버전을 물리 삭제하지 않습니다. **보관 범위와 계산 범위를 나눌
    뿐**입니다.

.. note::
    합성 데이터만 씁니다. 실제 기업명·사업자등록번호는 넣지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값이며 실제 업체의 번호가 아닙니다.
_A = "1000000009"
_B = "1000000014"
_C = "1000000028"
_D = "1000000033"

_FROM = "2026-01-01"
_TO = "2026-12-31"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "versions.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _list_file(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """``(사업자번호, 시작일, 종료일)`` 목록을 표준 양식으로 씁니다."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for business_no, valid_from, valid_to in rows:
        sheet.append([business_no, "합성기업", "가나다", valid_from, valid_to])
    book.save(path)
    return path


def _plain(*numbers: str) -> list[tuple[str, str, str]]:
    return [(number, _FROM, _TO) for number in numbers]


def _upload(client: TestClient, path: Path, code: str = "WOMAN") -> dict[str, Any]:
    response = client.post("/companies/upload", json={"file_path": str(path), "policy_code": code})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _versions(db: Path, code: str) -> list[tuple[int, bool, str | None]]:
    rows = PolicyCompanySourceRepository(db).find_versions(_policy_id(db, code))
    return [(row.version, row.is_active, row.source_label) for row in rows]


def _numbers_of(db: Path, certifications: list[Any]) -> set[str]:
    numbers = set()
    for certification in certifications:
        rows = CompanyRepository(db).execute(
            "SELECT business_no FROM company WHERE company_id = ?", (certification.company_id,)
        )
        numbers.add(rows[0]["business_no"])
    return numbers


def _stored(db: Path, code: str) -> set[str]:
    """DB 에 **남아 있는** 인증의 사업자번호 (이력 포함)."""
    return _numbers_of(db, CertificationRepository(db).find_by_policy(_policy_id(db, code)))


def _counted(db: Path, code: str) -> set[str]:
    """**계산에 드는** 인증의 사업자번호 (활성 버전만)."""
    return _numbers_of(db, CertificationRepository(db).find_active_by_policy(_policy_id(db, code)))


class TestTheFirstFileBecomesVersionOne:
    """§15 TEST 1."""

    def test_it_is_version_one_and_active(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _list_file(tmp_path / "v1.xlsx", _plain(_A, _B, _C)))

        assert _versions(db, "WOMAN") == [(1, True, "v1.xlsx")]
        assert _counted(db, "WOMAN") == {_A, _B, _C}


class TestANewFileBecomesTheActiveVersion:
    """§15 TEST 2 · TEST 6 · TEST 7 — 계산은 최신만, 이력은 남는다."""

    @pytest.fixture
    def two_versions(self, client: TestClient, db: Path, tmp_path: Path) -> Path:
        _upload(client, _list_file(tmp_path / "v1.xlsx", _plain(_A, _B, _C)))
        _upload(client, _list_file(tmp_path / "v2.xlsx", _plain(_A, _B, _D)))
        return db

    def test_the_new_version_is_active_and_the_old_one_survives(self, two_versions: Path) -> None:
        assert _versions(two_versions, "WOMAN") == [(1, False, "v1.xlsx"), (2, True, "v2.xlsx")]

    def test_the_dropped_company_leaves_the_calculation(self, two_versions: Path) -> None:
        """⭐ C 가 v1 에 있다는 이유만으로 계산에 들면 안 된다."""
        assert _C not in _counted(two_versions, "WOMAN")
        assert _counted(two_versions, "WOMAN") == {_A, _B, _D}

    def test_the_dropped_company_is_not_deleted(self, two_versions: Path) -> None:
        """§3 — ⛔ 지우지 않는다. 보관 범위와 계산 범위는 다르다."""
        assert _C in _stored(two_versions, "WOMAN")
        assert _stored(two_versions, "WOMAN") == {_A, _B, _C, _D}

    def test_the_new_company_enters_the_calculation(self, two_versions: Path) -> None:
        assert _D in _counted(two_versions, "WOMAN")


class TestTheSameFileMakesNoNewVersion:
    """§7 · §15 TEST 4 — 같은 **내용**이면 버전이 늘지 않는다."""

    def test_uploading_the_same_file_twice(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        path = _list_file(tmp_path / "v1.xlsx", _plain(_A, _B))
        _upload(client, path)
        _upload(client, path)

        assert _versions(db, "WOMAN") == [(1, True, "v1.xlsx")]

    def test_the_same_content_under_a_different_name(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """§8 — 이름이 달라도 **내용이 같으면** 같은 자료다."""
        _upload(client, _list_file(tmp_path / "a.xlsx", _plain(_A, _B)))
        _upload(client, _list_file(tmp_path / "b.xlsx", _plain(_A, _B)))

        assert [(v, active) for v, active, _ in _versions(db, "WOMAN")] == [(1, True)]

    def test_different_content_under_the_same_name(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """§8 — 이름이 같아도 **내용이 다르면** 새 버전이다."""
        _upload(client, _list_file(tmp_path / "list.xlsx", _plain(_A, _B)))
        _upload(client, _list_file(tmp_path / "list.xlsx", _plain(_A, _B, _C)))

        assert [(v, active) for v, active, _ in _versions(db, "WOMAN")] == [(1, False), (2, True)]


class TestVersionsAreIndependentPerPolicy:
    """§2 · §15 TEST 3."""

    def test_uploading_one_policy_does_not_bump_another(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _list_file(tmp_path / "w1.xlsx", _plain(_A)), "WOMAN")
        _upload(client, _list_file(tmp_path / "s1.xlsx", _plain(_A)), "STARTUP")
        _upload(client, _list_file(tmp_path / "w2.xlsx", _plain(_A, _B)), "WOMAN")

        assert [(v, a) for v, a, _ in _versions(db, "WOMAN")] == [(1, False), (2, True)]
        assert [(v, a) for v, a, _ in _versions(db, "STARTUP")] == [(1, True)]
        assert _versions(db, "DISABLED") == []


class TestAChangedPeriodUsesTheLatest:
    """§15 TEST 5 — 두 기간 모두 남되, 계산은 최신 것만."""

    def test_only_the_latest_period_counts(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _list_file(tmp_path / "v1.xlsx", [(_A, "2026-01-01", "2026-12-31")]))
        _upload(client, _list_file(tmp_path / "v2.xlsx", [(_A, "2026-07-01", "2027-06-30")]))

        policy_id = _policy_id(db, "WOMAN")
        repository = CertificationRepository(db)

        # 이력에는 둘 다 남는다.
        stored = sorted(
            (str(c.valid_from), str(c.valid_to)) for c in repository.find_by_policy(policy_id)
        )
        assert stored == [("2026-01-01", "2026-12-31"), ("2026-07-01", "2027-06-30")]

        # 계산에는 최신 것만 든다.
        active = [
            (str(c.valid_from), str(c.valid_to))
            for c in repository.find_active_by_policy(policy_id)
        ]
        assert active == [("2026-07-01", "2027-06-30")]

    def test_a_purchase_outside_the_new_period_stops_counting(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⭐ 3월 거래는 v1(1~12월) 에는 들지만 v2(7월~) 에는 들지 않는다."""
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_A,
                company_name="합성업체",
                resolution_date=date(2026, 3, 1),
                amount=Decimal("1000"),
            )
        )
        _upload(client, _list_file(tmp_path / "v1.xlsx", [(_A, "2026-01-01", "2026-12-31")]))
        client.post("/purchases/rematch")
        assert _woman_amount(client) == "1000"

        _upload(client, _list_file(tmp_path / "v2.xlsx", [(_A, "2026-07-01", "2027-06-30")]))
        client.post("/purchases/rematch")
        assert _woman_amount(client) == "0"


def _woman_amount(client: TestClient) -> str | None:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    row = next(r for r in payload["policies"] if r["policy_code"] == "WOMAN")
    value = row["purchase_amount"]
    return None if value is None else str(value)


class TestTheStatusesSurviveVersioning:
    """§14 — 조회불가/미해당 뜻이 바뀌지 않는다."""

    def test_no_version_means_unavailable(self, client: TestClient, db: Path) -> None:
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_A,
                company_name="합성업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("1000"),
            )
        )
        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        row = next(r for r in payload["policies"] if r["policy_code"] == "STARTUP")
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None

    def test_an_active_version_with_no_match_is_zero(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_B,
                company_name="합성업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("1000"),
            )
        )
        _upload(client, _list_file(tmp_path / "s1.xlsx", _plain(_A)), "STARTUP")
        client.post("/purchases/rematch")

        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        row = next(r for r in payload["policies"] if r["policy_code"] == "STARTUP")
        assert row["status"] != "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] == "0"


class TestABadFileNeverBecomesTheLatest:
    """⛔ 저장할 수 없는 파일이 최신 자료가 되면 안 된다."""

    def test_a_rejected_upload_leaves_the_active_version_alone(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _list_file(tmp_path / "v1.xlsx", _plain(_A, _B)))

        broken = _list_file(tmp_path / "broken.xlsx", [("12345", _FROM, _TO)])
        result = _upload(client, broken)
        assert result["stored"] is False

        assert _versions(db, "WOMAN") == [(1, True, "v1.xlsx")]
        assert _counted(db, "WOMAN") == {_A, _B}


class TestDirectlyStoredCertificationsStillCount:
    """등록 이력 없이 직접 넣은 인증이 조용히 사라지면 안 된다."""

    def test_a_certification_with_no_version_is_counted(self, db: Path) -> None:
        saved = CompanyRepository(db).insert(Company(business_no=_A, company_name="합성기업"))
        assert saved.company_id is not None
        CertificationRepository(db).insert(
            Certification(
                company_id=saved.company_id,
                policy_id=_policy_id(db, "WOMAN"),
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )
        assert _counted(db, "WOMAN") == {_A}
