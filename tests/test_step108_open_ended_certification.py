"""
STEP 108 — **종료일 없는 인증**(사회적기업·사회적협동조합)을 잠급니다.

🟢 2026-09-04 고객 확정(지시서 §2)

    사회적기업과 사회적협동조합은 종료일이 없으며 계속 유효한 것으로 판단한다.

무엇을 지키는가
===============

1. 종료일 없는 인증을 **저장하고 그대로 다시 읽을 수 있다**.
2. 판정은 ``valid_from <= 기준일`` 이면 인정 — 끝이 없다.
3. 종료일이 **있는** 인증의 판정은 하나도 바뀌지 않는다 (§20 회귀).
4. 빈 종료일을 파일로 넣을 수 있는 정책은 **두 개뿐**이다 (§9).
5. 구 스키마 DB(``valid_to NOT NULL``)가 **날짜를 바꾸지 않고** 열린다.

.. warning::
    ⛔ 없는 종료일을 지어내지 않습니다 — 인가일 + N년, 연말, ``9999-12-31``
    같은 값은 전부 시스템이 만들어낸 규칙입니다.

.. note::
    합성 데이터만 씁니다 (§25). 실제 기업명·사업자등록번호는 넣지 않습니다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.calculators.rules import RuleContext, build_default_registry
from procurement.calculators.rules.date_rules import is_within_any
from procurement.core.open_ended_certification import OPEN_ENDED_POLICY_CODES, allows_open_ended
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import (
    CREATE_TABLE_SQL,
    CertificationRepository,
)
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.company_importer import (
    SOURCE_FILE,
    CompanyImporter,
    CompanyRecord,
)
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다 (§25).
#: 체크섬만 맞춘 값이라 등록 경고가 섞이지 않습니다.
_BUSINESS_NO = "1000000009"

#: 사회적협동조합 「인가일」 자리에 들어가는 합성 날짜.
_APPROVED_ON = date(2026, 3, 15)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step108.db"
    init_db(path)
    seed_policies(path)
    return path


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _company(db: Path) -> int:
    company = CompanyRepository(db).insert(
        Company(business_no=_BUSINESS_NO, company_name="합성조합 가", representative_name="가나다")
    )
    assert company.company_id is not None
    return company.company_id


def _purchase(db: Path, *, resolution: date, company_id: int, amount: str = "1000") -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=_BUSINESS_NO,
            company_name="합성조합 가",
            resolution_date=resolution,
            amount=Decimal(amount),
            company_id=company_id,
        )
    )


class TestTheRuleItself:
    """§9 — ``valid_to`` 가 ``None`` 이면 끝이 없다."""

    @pytest.mark.parametrize(
        ("basis", "expected"),
        [
            (date(2026, 3, 14), False),  # 인가일 **하루 전** → 불인정
            (_APPROVED_ON, True),  # 인가일 **당일** → 인정 (경계 포함)
            (date(2026, 3, 16), True),  # 인가일 다음날 → 인정
            (date(2099, 12, 31), True),  # 아주 먼 미래 → 여전히 인정 (끝이 없다)
        ],
    )
    def test_open_ended_range_has_only_a_start(self, basis: date, expected: bool) -> None:
        assert is_within_any(basis, [(_APPROVED_ON, None)]) is expected

    @pytest.mark.parametrize(
        ("basis", "expected"),
        [
            (date(2025, 12, 31), False),
            (date(2026, 1, 1), True),
            (date(2026, 12, 31), True),
            (date(2027, 1, 1), False),  # ⛔ 종료일이 **있으면** 끝난다
        ],
    )
    def test_closed_ranges_are_unchanged(self, basis: date, expected: bool) -> None:
        """§20 회귀 — 종료일이 있는 인증의 판정은 하나도 바뀌지 않았습니다."""
        assert is_within_any(basis, [(date(2026, 1, 1), date(2026, 12, 31))]) is expected

    def test_a_closed_range_next_to_an_open_one_still_ends(self) -> None:
        """구간이 섞여 있어도 각자의 규칙대로 판정합니다."""
        ranges: list[tuple[date, date | None]] = [
            (date(2026, 1, 1), date(2026, 6, 30)),
            (date(2027, 1, 1), None),
        ]
        assert is_within_any(date(2026, 6, 30), ranges) is True
        assert is_within_any(date(2026, 7, 1), ranges) is False  # 두 구간 사이 → 불인정
        assert is_within_any(date(2030, 1, 1), ranges) is True


class TestStoringAnOpenEndedCertification:
    """§8 — 저장하고 그대로 다시 읽는다."""

    def test_it_survives_a_round_trip(self, db: Path) -> None:
        repo = CertificationRepository(db)
        stored = repo.insert(
            Certification(
                company_id=_company(db),
                policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                valid_from=_APPROVED_ON,
                valid_to=None,
            )
        )
        assert stored.certification_id is not None
        reloaded = repo.find_by_id(stored.certification_id)
        assert reloaded is not None
        assert reloaded.valid_from == _APPROVED_ON
        assert reloaded.valid_to is None

    def test_no_end_date_is_invented_anywhere(self, db: Path) -> None:
        """⛔ DB 에 들어간 값 자체가 NULL 이어야 합니다 — 지어낸 날짜가 없습니다."""
        repo = CertificationRepository(db)
        repo.insert(
            Certification(
                company_id=_company(db),
                policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                valid_from=_APPROVED_ON,
                valid_to=None,
            )
        )
        rows = repo.execute("SELECT valid_to FROM certification")
        assert [row["valid_to"] for row in rows] == [None]


class TestTheCalculationEndToEnd:
    """§12 · §18 — 인가일 이전/당일/이후가 실적에 어떻게 반영되는가."""

    @pytest.mark.parametrize(
        ("resolution", "expected"),
        [
            (date(2026, 3, 14), "0"),  # 인가일 이전 → 실적 아님
            (_APPROVED_ON, "1000"),  # 인가일 당일 → 실적
            (date(2026, 11, 30), "1000"),  # 인가일 이후 → 실적 (끝이 없다)
        ],
    )
    def test_only_purchases_from_the_approval_date_count(
        self, db: Path, resolution: date, expected: str
    ) -> None:
        company_id = _company(db)
        CertificationRepository(db).insert(
            Certification(
                company_id=company_id,
                policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                valid_from=_APPROVED_ON,
                valid_to=None,
            )
        )
        _purchase(db, resolution=resolution, company_id=company_id)

        calculator = ProcurementAchievementCalculator(
            purchase_repository=PurchaseRepository(db),
            certification_repository=CertificationRepository(db),
            policy_repository=PolicyRepository(db),
            rule_registry=build_default_registry(),
        )
        assert calculator.calculate_policy_purchase(
            _policy_id(db, "SOCIAL_COOPERATIVE")
        ) == Decimal(expected)

    def test_the_rule_context_accepts_an_open_range(self, db: Path) -> None:
        """규칙에 ``(시작일, None)`` 구간을 그대로 넘길 수 있습니다."""
        rule = build_default_registry().get("RESOLUTION_DATE")
        purchase = Purchase(
            business_no=_BUSINESS_NO,
            company_name="합성조합 가",
            resolution_date=date(2030, 5, 5),
            amount=Decimal("1000"),
        )
        assert rule.matches(RuleContext(purchase=purchase, validity_ranges=[(_APPROVED_ON, None)]))


class TestWhichPoliciesMayOmitTheEndDate:
    """§9 — 이 규칙은 **두 정책에만** 적용합니다."""

    def test_only_the_confirmed_policies(self) -> None:
        """명단에는 **고객이 확정한 정책만** 있다.

        분류 A · 고객 확정 반영 (2026-09-05): *"종료(취소)일자가 없으면 그냥
        사회적기업, 사회적협동조합과 같은 규칙으로 가면 된다"* — 장애인표준
        사업장 자료도 「인증일자」만 있어 같은 규칙이 되었다. 그전까지 이
        시험은 두 정책만 있기를 요구했다.

        ⛔ 명단이 저절로 늘어나면 안 된다. 여기 없는 정책에서 종료일이 비면
        여전히 오류이며, 그래야 빠진 값이 조용히 "영원히 유효" 가 되지 않는다.
        """
        assert OPEN_ENDED_POLICY_CODES == frozenset(
            {"SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE", "DISABLED_STANDARD_WORKPLACE"}
        )
        for confirmed in ("SOCIAL_COOPERATIVE", "SOCIAL_ENTERPRISE", "DISABLED_STANDARD_WORKPLACE"):
            assert allows_open_ended(confirmed) is True, confirmed
        # ⛔ 장애인기업(DISABLED)은 표준사업장과 **다른 정책**이며 명단에 없다.
        for other in ("WOMAN", "STARTUP", "DISABLED", "SMALL_BUSINESS", None):
            assert allows_open_ended(other) is False, other

    def _import(self, db: Path, policy_code: str) -> tuple[bool, list[str]]:
        importer = CompanyImporter(
            company_repository=CompanyRepository(db),
            certification_repository=CertificationRepository(db),
            policy_repository=PolicyRepository(db),
        )
        result = importer.import_records(
            [
                CompanyRecord(
                    business_no=_BUSINESS_NO,
                    company_name="합성조합 가",
                    representative_name="가나다",
                    policy_code=policy_code,
                    valid_from=_APPROVED_ON,
                    valid_to=None,
                    source_row=1,
                )
            ],
            source=SOURCE_FILE,
        )
        row = result.rows[0]
        return row.certification_saved, list(row.messages)

    def test_a_blank_end_date_is_accepted_for_a_cooperative(self, db: Path) -> None:
        saved, messages = self._import(db, "SOCIAL_COOPERATIVE")
        assert saved is True
        assert messages == []

    def test_a_blank_end_date_is_still_refused_for_other_policies(self, db: Path) -> None:
        """⛔ 빠진 값이 조용히 "영원히 유효" 가 되면 안 됩니다."""
        saved, messages = self._import(db, "WOMAN")
        assert saved is False
        assert messages == ["인증 유효기간이 없어 인증을 넣지 않았습니다."]
        assert CertificationRepository(db).count() == 0


class TestMigratingAnExistingDatabase:
    """§8 — 이미 만들어진 DB 를 **값을 바꾸지 않고** 연다."""

    def test_old_rows_keep_their_dates(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE certification (
                certification_id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL,
                policy_id INTEGER NOT NULL,
                certificate_number TEXT,
                valid_from DATE NOT NULL,
                valid_to DATE NOT NULL,
                issuing_agency TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO certification VALUES
                (7, 3, 2, 'C-1', '2026-01-01', '2026-12-31', '기관',
                 '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """
        )
        conn.commit()
        conn.close()

        repo = CertificationRepository(path)
        repo.create_table()

        found = repo.find_by_id(7)
        assert found is not None
        assert found.company_id == 3
        assert found.policy_id == 2
        assert found.certificate_number == "C-1"
        assert found.valid_from == date(2026, 1, 1)
        assert found.valid_to == date(2026, 12, 31)  # ⛔ 날짜가 바뀌지 않았습니다.
        assert found.issuing_agency == "기관"

        # 이제 종료일 없는 인증도 들어갑니다.
        stored = repo.insert(
            Certification(company_id=3, policy_id=2, valid_from=_APPROVED_ON, valid_to=None)
        )
        assert stored.valid_to is None
        assert repo.count() == 2

        # 임시 테이블이 남지 않습니다.
        tables = repo.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert "certification_pre_open_ended" not in {row["name"] for row in tables}

    def test_migration_is_idempotent(self, db: Path) -> None:
        """새 스키마에서 반복 호출해도 아무 일도 일어나지 않습니다."""
        repo = CertificationRepository(db)
        repo.insert(
            Certification(
                company_id=_company(db),
                policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                valid_from=_APPROVED_ON,
                valid_to=None,
            )
        )
        for _ in range(3):
            repo.create_table()
        assert repo.count() == 1
        assert "valid_to DATE," in CREATE_TABLE_SQL  # NOT NULL 이 아니다


# ======================================================================
# 실제 사회적협동조합 파일이 지나가는 경로 (지시서 §17)
#
# 실제 자료는 「연번·협동조합명·대표자·사업자등록번호·주소·전화번호·품목·
# 인가일」 여덟 칸이고 **종료일·인증상태·취소일 칸이 아예 없습니다.** 그
# 모양을 합성 데이터로 그대로 흉내 내어 파일 한 장이 실적 숫자가 되기까지를
# 확인합니다. ⛔ 실제 기업명·사업자등록번호는 쓰지 않습니다.
# ======================================================================

#: 목표율 — 🟢 2026-09-03 고객 확정(DECISIONS §0.24).
_COOP_TARGET_RATE = Decimal("0.1")

#: 합성 사업자등록번호 — 체크섬만 맞춘 값입니다.
_COOP = "1000000014"
_ABSENT = "1000000028"


def _coop_file(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """정책을 고르고 올리는 표준 양식 — 유효종료일은 **비워 둡니다**."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for business_no, name, representative, approved_on in rows:
        sheet.append([business_no, name, representative, approved_on, None])
    book.save(path)
    return path


def _plain_purchase(db: Path, business_no: str, *, resolution: date, amount: str) -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name="합성업체",
            resolution_date=resolution,
            amount=Decimal(amount),
        )
    )


@pytest.fixture
def coop_db(tmp_path: Path) -> Path:
    from procurement.__main__ import main

    path = tmp_path / "coop_flow.db"
    init_db(path)
    seed_policies(path)
    # 목표비율은 seed 가 아니라 ``targets --year`` 이 넣습니다(STEP 98).
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def coop_client(coop_db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(coop_db)) as client:
        yield client


def _coop_summary(client: TestClient) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": 2026}).json()
    row = next(r for r in payload["policies"] if r["policy_code"] == "SOCIAL_COOPERATIVE")
    return dict(row)


def _upload_coop(
    client: TestClient, path: Path, policy_code: str = "SOCIAL_COOPERATIVE"
) -> dict[str, Any]:
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestTheCooperativeFileFlow:
    """§4 · §11 · §12 — 파일 한 장이 0.1% 달성률이 되기까지."""

    def test_the_policy_is_already_wired_for_file_upload(self, coop_client: TestClient) -> None:
        rows = coop_client.get("/companies/registration").json()["items"]
        row = next(r for r in rows if r["policy_code"] == "SOCIAL_COOPERATIVE")
        assert "FILE" in row["available_methods"]

        targets = coop_client.get("/policy-targets", params={"year": 2026}).json()["items"]
        target = next(t for t in targets if t["policy_code"] == "SOCIAL_COOPERATIVE")
        assert Decimal(target["target_rate"]) == _COOP_TARGET_RATE

    def test_nothing_registered_is_not_zero(self, coop_client: TestClient, coop_db: Path) -> None:
        """§13 — 등록 전에는 **조회불가**. ⛔ 0원도 0% 도 아닙니다."""
        _plain_purchase(coop_db, _COOP, resolution=date(2026, 5, 1), amount="1000")
        row = _coop_summary(coop_client)
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None
        assert row["achievement_rate"] is None

    def test_the_whole_path_produces_the_achievement(
        self, coop_client: TestClient, coop_db: Path, tmp_path: Path
    ) -> None:
        """§5 · §12 — 하이픈 정규화 → 정확 매칭 → 0.1% 목표 → 달성률 100%."""
        # 분모 1,000,000원 중 조합 거래 1,000원 → 0.1% → 달성률 100%
        _plain_purchase(coop_db, _COOP, resolution=date(2026, 5, 1), amount="1000")
        _plain_purchase(coop_db, _ABSENT, resolution=date(2026, 5, 1), amount="999000")
        path = _coop_file(
            tmp_path / "coop.xlsx",
            [("100-00-00014", "합성사회적협동조합", "가나다", "2026-03-15")],
        )
        result = _upload_coop(coop_client, path)
        assert result["stored"] is True
        assert result["certifications"] == 1
        coop_client.post("/purchases/rematch")

        # ⛔ 종료일을 지어내지 않았습니다.
        stored = CertificationRepository(coop_db).find_by_policy(
            _policy_id(coop_db, "SOCIAL_COOPERATIVE")
        )
        assert [c.valid_to for c in stored] == [None]

        row = _coop_summary(coop_client)
        assert row["total_purchase_amount"] == "1000000"
        assert row["purchase_amount"] == "1000"
        assert Decimal(row["target_rate"]) == _COOP_TARGET_RATE
        assert Decimal(row["achievement_rate"]) == Decimal("100")
        assert row["status"] == "NORMAL"

    def test_a_company_not_on_the_list_is_not_counted(
        self, coop_client: TestClient, coop_db: Path, tmp_path: Path
    ) -> None:
        """§13 — 등록은 되어 있고 목록에 없으면 **미해당**입니다.

        조회불가와 달리 금액이 나옵니다 — 셀 수 있는데 이 업체가 아닐 뿐입니다.
        ⛔ 비슷한 번호·같은 이름으로 대신 맞추지 않습니다(§5).
        """
        _plain_purchase(coop_db, _ABSENT, resolution=date(2026, 5, 1), amount="1000")
        path = _coop_file(
            tmp_path / "coop.xlsx", [(_COOP, "합성사회적협동조합", "가나다", "2026-03-15")]
        )
        _upload_coop(coop_client, path)
        coop_client.post("/purchases/rematch")

        row = _coop_summary(coop_client)
        assert row["status"] != "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] == "0"

    def test_the_issue_date_is_not_the_basis(
        self, coop_client: TestClient, coop_db: Path, tmp_path: Path
    ) -> None:
        """§6 — ⛔ 신고기준일로 판정하지 않습니다.

        결의일자는 인가일 **이전**, 신고기준일은 인가일 이후에 두었습니다.
        신고기준일을 본다면 실적이 잡히는데, 잡히면 안 됩니다.
        """
        PurchaseRepository(coop_db).insert(
            Purchase(
                business_no=_COOP,
                company_name="합성업체",
                resolution_date=date(2026, 1, 5),  # 인가일 전 → 실적 아님
                issue_date=date(2026, 6, 1),  # 인가일 후 — ⛔ 판정에 쓰이면 안 된다
                amount=Decimal("1000"),
            )
        )
        path = _coop_file(
            tmp_path / "coop.xlsx", [(_COOP, "합성사회적협동조합", "가나다", "2026-03-15")]
        )
        _upload_coop(coop_client, path)
        coop_client.post("/purchases/rematch")
        assert _coop_summary(coop_client)["purchase_amount"] == "0"

    def test_one_purchase_lands_in_two_policies(
        self, coop_client: TestClient, coop_db: Path, tmp_path: Path
    ) -> None:
        """§14 — 같은 거래가 두 정책에 각각 들어갑니다. ⛔ 중복 제거 없음."""
        _plain_purchase(coop_db, _COOP, resolution=date(2026, 5, 1), amount="1000")
        _upload_coop(
            coop_client,
            _coop_file(
                tmp_path / "coop.xlsx", [(_COOP, "합성사회적협동조합", "가나다", "2026-03-15")]
            ),
        )
        # 창업기업은 종료일이 **필수**입니다 — 두 정책만 비울 수 있습니다.
        openpyxl = pytest.importorskip("openpyxl")
        startup_path = tmp_path / "startup.xlsx"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
        sheet.append([_COOP, "합성업체", "가나다", "2026-01-01", "2026-12-31"])
        book.save(startup_path)
        _upload_coop(coop_client, startup_path, "STARTUP")
        coop_client.post("/purchases/rematch")

        payload = coop_client.get("/dashboard/summary", params={"year": 2026}).json()
        amounts = {row["policy_code"]: row["purchase_amount"] for row in payload["policies"]}
        assert amounts["SOCIAL_COOPERATIVE"] == "1000"
        assert amounts["STARTUP"] == "1000"  # 같은 1,000원이 양쪽에 들어간다
        assert payload["total_purchase_amount"] == "1000"  # 분모는 한 번만 센다


class TestTheRealFileShapeHasNoCancellation:
    """§8 — 실제 자료에 없는 개념을 시스템이 만들어 내지 않았습니다."""

    def test_the_upload_form_has_no_status_or_cancellation_column(self) -> None:
        from procurement.uploads.company_format import COMPANY_REQUIRED_HEADERS

        for absent in ("인증상태", "인증취소일", "취소일"):
            assert absent not in COMPANY_REQUIRED_HEADERS, absent

    def test_no_source_file_judges_on_a_cancellation(self) -> None:
        """⛔ 취소로 판정하는 코드가 소스 어디에도 없습니다."""
        source_root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for term in ("인증취소", "취소일", "cancell", "revoke"):
            hits = [
                path.name
                for path in source_root.rglob("*.py")
                if term.lower() in path.read_text(encoding="utf-8").lower()
            ]
            assert hits == [], (term, hits)
