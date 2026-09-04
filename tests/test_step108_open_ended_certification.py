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
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

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

    def test_only_the_two_confirmed_policies(self) -> None:
        assert OPEN_ENDED_POLICY_CODES == frozenset({"SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE"})
        assert allows_open_ended("SOCIAL_COOPERATIVE") is True
        assert allows_open_ended("SOCIAL_ENTERPRISE") is True
        for other in ("WOMAN", "STARTUP", "DISABLED", "SMALL_BUSINESS", None):
            assert allows_open_ended(other) is False

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
