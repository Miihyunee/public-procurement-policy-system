"""
STEP 74 — 기업 사업자등록번호가 **구매와 같은 형태로** 저장·조회되는가.

STEP 73 검수에서 드러난 것
==========================

구매는 적재하면서 숫자만 남지만, 기업은 넣은 그대로 저장되었습니다. 기업을
``220-81-62517`` 로 등록하면 ``2208162517`` 인 구매와 **영원히 연결되지
않습니다.** 오류가 나지 않는 것이 가장 나쁜 점입니다 — 화면에는 "미매칭" 으로만
보이고, **정책 구매액(분자)이 조용히 0** 이 됩니다.

세 규칙을 섞지 않습니다
=======================

===========================  ==========  ==============================
함수                          쓰임        부분 번호(``22081``)를 받는가
===========================  ==========  ==============================
``normalize_business_no``     결합 키     아니오 — 실패로 알림
``to_storage_business_no``    저장        형태만 정리(판정하지 않음)
``business_no_search_key``    검색 비교   예 — 앞자리만 넣는 검색이 흔함
===========================  ==========  ==============================

⛔ **부분 번호로 기업을 연결하지 않습니다.** 검색에서 통한다고 매칭에서도
통하면 **엉뚱한 회사의 실적**이 됩니다.

.. warning::
    ⛔ 이 파일은 업무규칙을 만들지 않습니다. 사업자등록번호의 업무적 유효성
    (자릿수·체크섬)을 새로 판정하지 않으며, 구매 데이터의 저장 형식도 바꾸지
    않습니다. 없앤 것은 **표기 차이 하나**뿐입니다.

.. note::
    합성 데이터만 씁니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.business_no_storage import to_storage_business_no
from procurement.core.performance_exclusion import EXCLUDED
from procurement.core.period import PAYMENT_DATE, PeriodFilter
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import (
    CompanyRepository,
    CompanyValidationError,
    DuplicateBusinessNoError,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers.business_no import business_no_search_key, normalize_business_no
from procurement.models import Certification, Company, Purchase

# 합성 사업자등록번호 — 인쇄 표기와 저장 표기.
_PRINTED = "220-81-62517"
_STORED = "2208162517"
_OTHER_PRINTED = "119-81-02316"
_OTHER_STORED = "1198102316"

_DAY = date(2026, 3, 1)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "business_no.db"
    bootstrap(path)
    PolicyRepository(path).update_target_rate("SMALL_BUSINESS", Decimal("30"))
    return path


@pytest.fixture
def repository(db: Path) -> CompanyRepository:
    return CompanyRepository(db)


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, period_date_field=PAYMENT_DATE))


def _company(business_no: str, name: str = "합성기업 가") -> Company:
    return Company(business_no=business_no, company_name=name, representative_name="홍길동")


def _legacy_insert(db: Path, business_no: str, name: str) -> int:
    """**정규화 없이** 직접 넣습니다 — 예전 방식으로 쌓인 기존 데이터 재현.

    ⛔ 저장소를 거치면 정리되므로, 정리되지 않은 상태를 만들려면 이 길밖에
    없습니다. 운영 코드는 이렇게 넣지 않습니다.
    """
    now = datetime.now().isoformat(sep=" ")
    connection = sqlite3.connect(db)
    cursor = connection.execute(
        "INSERT INTO company (business_no, company_name, representative_name,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (business_no, name, "홍길동", now, now),
    )
    connection.commit()
    company_id = int(cursor.lastrowid or 0)
    connection.close()
    return company_id


def _certify(db: Path, company_id: int) -> None:
    policy = PolicyRepository(db).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    CertificationRepository(db).insert(
        Certification(
            company_id=company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )


def _purchase(
    db: Path,
    business_no: str,
    amount: str,
    *,
    name: str = "합성기업 가",
    description: str = "합성 구매",
    budget_account: str | None = "일반운영비",
    day: date = _DAY,
) -> int:
    saved = PurchaseRepository(db).insert(
        Purchase(
            business_no=business_no,
            company_name=name,
            contract_date=day,
            payment_date=day,
            resolution_date=day,
            description=description,
            budget_account=budget_account,
            amount=Decimal(amount),
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _calculator(db: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
    )


def _policy_id(db: Path) -> int:
    policy = PolicyRepository(db).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _period() -> PeriodFilter:
    return PeriodFilter.for_year(2026, PAYMENT_DATE)


# ======================================================================
# 1. 저장 — 넣은 표기와 상관없이 같은 형태로 남는다
# ======================================================================
class TestStorageFormat:
    """구매와 **비교 가능한 형태**로 저장한다."""

    def test_hyphens_are_removed(self, repository: CompanyRepository) -> None:
        saved = repository.insert(_company(_PRINTED))
        assert saved.business_no == _STORED

    def test_spaces_are_removed(self, repository: CompanyRepository) -> None:
        assert repository.insert(_company("220 81 62517")).business_no == _STORED

    def test_digits_only_input_is_untouched(self, repository: CompanyRepository) -> None:
        """⛔ 이미 숫자만 있는 값은 **아무것도 바뀌지 않는다.**"""
        assert repository.insert(_company(_STORED)).business_no == _STORED

    def test_what_is_stored_is_what_is_read_back(self, repository: CompanyRepository) -> None:
        saved = repository.insert(_company(_PRINTED))
        assert saved.company_id is not None
        read = repository.find_by_id(saved.company_id)
        assert read is not None
        assert read.business_no == _STORED

    def test_the_digits_are_never_altered(self, repository: CompanyRepository) -> None:
        """⛔ 숫자를 고치거나 만들어내지 않는다 — 지우는 것은 구분자뿐."""
        saved = repository.insert(_company("220-81-6251"))  # 9자리
        assert saved.business_no == "220816251"

    def test_no_validity_judgement_was_added(self, repository: CompanyRepository) -> None:
        """⛔ 자릿수·체크섬으로 유효/무효를 판정하지 않는다(업무규칙 변경 금지)."""
        assert repository.insert(_company("0000000000")).business_no == "0000000000"
        assert repository.insert(_company("123-45", "짧은 번호")).business_no == "12345"

    def test_separators_only_is_rejected_as_missing(self, repository: CompanyRepository) -> None:
        """구분자만 들어오면 지우고 나서 비므로 **필수값 누락**이다."""
        with pytest.raises(CompanyValidationError):
            repository.insert(_company("--"))

    def test_the_same_number_in_two_notations_still_collides(
        self, repository: CompanyRepository
    ) -> None:
        """⭐ 같은 번호를 다른 표기로 두 번 넣으면 **중복으로 걸린다.**

        정리 전에는 둘 다 들어가 서로 다른 기업이 되어 있었다.
        """
        repository.insert(_company(_STORED))
        with pytest.raises(DuplicateBusinessNoError):
            repository.insert(_company(_PRINTED, "같은 회사 다른 표기"))


# ======================================================================
# 2. 매칭 — 표기가 달라도 같은 기업, 다르면 남남
# ======================================================================
class TestMatching:
    """§7 Case 1~6."""

    def test_case1_digits_to_digits(self, repository: CompanyRepository) -> None:
        repository.insert(_company(_STORED))
        found = repository.find_by_business_no(_STORED)
        assert found is not None

    def test_case2_purchase_digits_finds_a_legacy_hyphen_company(self, db: Path) -> None:
        """⭐ 정리되지 않은 기존 기업도 찾는다 — DB 를 고치지 않고도."""
        _legacy_insert(db, _PRINTED, "옛 표기로 등록된 기업")
        found = CompanyRepository(db).find_by_business_no(_STORED)
        assert found is not None
        assert found.business_no == _PRINTED  # 저장값은 그대로 두었다

    def test_case3_asking_with_hyphens_also_works(self, repository: CompanyRepository) -> None:
        """구매가 하이픈 표기를 들고 와도 같은 기업으로 본다."""
        repository.insert(_company(_STORED))
        found = repository.find_by_business_no(_PRINTED)
        assert found is not None

    def test_case4_a_different_number_never_matches(self, repository: CompanyRepository) -> None:
        repository.insert(_company(_STORED))
        assert repository.find_by_business_no(_OTHER_STORED) is None
        assert repository.find_by_business_no(_OTHER_PRINTED) is None

    def test_case5_the_same_name_is_not_the_same_company(self, db: Path) -> None:
        """⛔ 기업명이 같아도 번호가 다르면 연결하지 않는다(기업명 매칭 금지)."""
        company = CompanyRepository(db).insert(_company(_OTHER_STORED, "A기업"))
        assert company.company_id is not None
        _certify(db, company.company_id)
        _purchase(db, _STORED, "1000", name="A기업")

        client = TestClient(create_app(db, period_date_field=PAYMENT_DATE))
        client.post("/purchases/rematch")
        status: Any = client.get("/dashboard/data-status").json()
        assert status["matched_purchase_count"] == 0
        assert status["unmatched_purchase_count"] == 1

    def test_case6_a_partial_number_is_not_a_company(self, repository: CompanyRepository) -> None:
        """⛔ ``22081`` 은 검색어다. 매칭에 쓰면 엉뚱한 회사와 연결된다."""
        repository.insert(_company(_STORED))
        assert repository.find_by_business_no("22081") is None
        assert repository.find_by_business_no("220-81") is None

    def test_a_partial_number_is_not_a_company_for_legacy_rows_either(self, db: Path) -> None:
        _legacy_insert(db, _PRINTED, "옛 표기로 등록된 기업")
        assert CompanyRepository(db).find_by_business_no("22081") is None

    def test_exists_agrees_with_find(self, db: Path) -> None:
        """⛔ 한쪽은 찾고 다른 쪽은 없다고 답하면 안 된다."""
        _legacy_insert(db, _PRINTED, "옛 표기로 등록된 기업")
        repository = CompanyRepository(db)
        for asked in (_STORED, _PRINTED):
            assert repository.exists(asked) is (repository.find_by_business_no(asked) is not None)
            assert repository.exists(asked) is True
        assert repository.exists(_OTHER_STORED) is False
        assert repository.exists("22081") is False

    def test_an_empty_number_finds_nothing(self, repository: CompanyRepository) -> None:
        repository.insert(_company(_STORED))
        assert repository.find_by_business_no("") is None
        assert repository.exists("") is False


# ======================================================================
# 3. 세 규칙의 구분
# ======================================================================
class TestThreeRulesStaySeparate:
    """저장 · 결합키 · 검색은 **목적이 다르다.**"""

    def test_storage_does_not_judge_validity(self) -> None:
        assert to_storage_business_no("220-81") == "22081"
        assert normalize_business_no("220-81").value is None

    def test_storage_and_search_agree_on_separators(self) -> None:
        """지금은 결과가 같다 — 그래도 **다른 규칙**이라 따로 시험한다."""
        for value in (_PRINTED, "220 81 62517", _STORED):
            assert to_storage_business_no(value) == business_no_search_key(value)

    def test_storage_handles_numeric_input(self) -> None:
        """엑셀에서 숫자로 들어와도 지수·소수 표기가 남지 않는다."""
        assert to_storage_business_no(2208162517) == _STORED
        assert to_storage_business_no(2208162517.0) == _STORED

    def test_storage_of_nothing_is_empty(self) -> None:
        assert to_storage_business_no(None) == ""
        assert to_storage_business_no("   ") == ""


# ======================================================================
# 4. 중복 / 충돌 — ⛔ 자동 병합하지 않는다
# ======================================================================
class TestNormalizationConflicts:
    """정리하면 같은 번호가 되는 기존 기업들."""

    @pytest.fixture
    def conflicted(self, db: Path) -> Path:
        _legacy_insert(db, "101-81-16293", "합성 라기업")
        _legacy_insert(db, "1018116293", "합성 라기업(다른 등록)")
        _legacy_insert(db, _PRINTED, "합성 가기업")
        _legacy_insert(db, _OTHER_STORED, "합성 나기업")
        return db

    def test_the_conflict_is_found(self, conflicted: Path) -> None:
        conflicts = CompanyRepository(conflicted).find_normalization_conflicts()
        assert [conflict.business_no for conflict in conflicts] == ["1018116293"]
        assert len(conflicts[0].companies) == 2

    def test_a_clean_database_reports_nothing(self, repository: CompanyRepository) -> None:
        repository.insert(_company(_STORED))
        repository.insert(_company(_OTHER_PRINTED, "합성기업 나"))
        assert repository.find_normalization_conflicts() == []

    def test_the_survey_counts_the_notations(self, conflicted: Path) -> None:
        survey = CompanyRepository(conflicted).survey_business_no_formats()
        assert survey.total == 4
        assert survey.with_hyphen == 2
        assert survey.digits_only == 2
        assert survey.conflicting == 2

    def test_the_survey_counts_spaces(self, db: Path) -> None:
        _legacy_insert(db, "220 81 62517", "공백 표기")
        survey = CompanyRepository(db).survey_business_no_formats()
        assert survey.with_space == 1
        assert survey.digits_only == 0

    def test_the_survey_changes_nothing(self, conflicted: Path) -> None:
        """⛔ 조사는 읽기만 한다."""
        repository = CompanyRepository(conflicted)
        before = [row["business_no"] for row in repository.execute("SELECT * FROM company")]
        repository.survey_business_no_formats()
        repository.find_normalization_conflicts()
        after = [row["business_no"] for row in repository.execute("SELECT * FROM company")]
        assert after == before

    def test_conflicting_rows_are_never_merged(self, conflicted: Path) -> None:
        """⛔ 어느 쪽이 옳은지 시스템은 모른다 — 사람이 정한다."""
        repository = CompanyRepository(conflicted)
        repository.normalize_stored_business_numbers(apply=True)

        assert repository.count() == 4
        stored = {row["business_no"] for row in repository.execute("SELECT * FROM company")}
        assert "101-81-16293" in stored  # 손대지 않았다
        assert "1018116293" in stored
        assert [c.business_no for c in repository.find_normalization_conflicts()] == ["1018116293"]


class TestTheMigrationIsDeliberate:
    """정리는 **사람이 시켜야** 일어난다."""

    def test_the_default_is_a_plan_not_a_change(self, db: Path) -> None:
        _legacy_insert(db, _PRINTED, "옛 표기")
        repository = CompanyRepository(db)

        plan = repository.normalize_stored_business_numbers()

        assert plan.applied is False
        assert plan.changed == ((1, _PRINTED, _STORED),)
        assert [row["business_no"] for row in repository.execute("SELECT * FROM company")] == [
            _PRINTED
        ]

    def test_applying_rewrites_only_the_notation(self, db: Path) -> None:
        _legacy_insert(db, _PRINTED, "옛 표기")
        repository = CompanyRepository(db)

        applied = repository.normalize_stored_business_numbers(apply=True)

        assert applied.applied is True
        company = repository.find_by_id(1)
        assert company is not None
        assert company.business_no == _STORED
        assert company.company_name == "옛 표기"  # 다른 값은 그대로

    def test_bootstrap_does_not_run_it(self, db: Path) -> None:
        """⛔ 어디서도 자동으로 돌지 않는다 — 부트스트랩이 고객 DB 를 고치지 않는다."""
        _legacy_insert(db, _PRINTED, "옛 표기")
        bootstrap(db)
        rows = CompanyRepository(db).execute("SELECT business_no FROM company")
        assert [row["business_no"] for row in rows] == [_PRINTED]

    def test_nothing_to_do_is_not_an_error(self, repository: CompanyRepository) -> None:
        repository.insert(_company(_STORED))
        plan = repository.normalize_stored_business_numbers(apply=True)
        assert plan.changed == ()
        assert plan.applied is False


# ======================================================================
# 5. 검색 회귀 — STEP 73 에서 고친 것이 그대로인가
# ======================================================================
class TestSearchStillWorks:
    """⛔ 저장 규칙을 바꿨다고 검색 결과가 줄어들면 안 된다."""

    @pytest.fixture
    def seeded(self, db: Path) -> Path:
        _purchase(db, _OTHER_STORED, "1000", name="합성기업 나", description="사무용품 구매")
        _purchase(db, _OTHER_STORED, "2000", name="합성기업 나", description="청소 용역")
        return db

    def _ids(self, client: TestClient, search: str) -> list[int]:
        body: Any = client.get(f"/reviews?page=1&page_size=50&search={search}").json()
        return [item["source"]["purchase_id"] for item in body["items"]]

    def test_hyphenated_search_still_finds_them(self, seeded: Path, client: TestClient) -> None:
        assert len(self._ids(client, _OTHER_PRINTED)) == 2

    def test_digit_search_still_finds_them(self, seeded: Path, client: TestClient) -> None:
        assert self._ids(client, _OTHER_STORED) == self._ids(client, _OTHER_PRINTED)

    def test_description_search_is_unchanged(self, seeded: Path, client: TestClient) -> None:
        assert len(self._ids(client, "사무용품")) == 1

    def test_company_name_search_is_unchanged(self, seeded: Path, client: TestClient) -> None:
        assert len(self._ids(client, "합성기업 나")) == 2

    def test_another_number_is_still_excluded(self, seeded: Path, client: TestClient) -> None:
        assert self._ids(client, _PRINTED) == []

    def test_the_unmatched_screen_is_unchanged(self, seeded: Path, client: TestClient) -> None:
        body: Any = client.get(f"/dashboard/unmatched-companies?search={_OTHER_PRINTED}").json()
        assert [item["business_no"] for item in body["items"]] == [_OTHER_STORED]


# ======================================================================
# 6. 계산 — 매칭이 달라질 뿐, 공식은 그대로다
# ======================================================================
class TestCalculationIsUntouched:
    """⚠️ **정합성 개선 ≠ 달성률 변경.**

    표기가 맞아 기업이 연결되면 분자에 **반영될 수 있다.** 그것은 계산이
    바뀐 것이 아니라 **연결되지 않던 것이 연결된** 결과다.
    """

    def test_a_legacy_company_can_now_reach_the_numerator(self, db: Path) -> None:
        """⭐ 인증까지 있는 기업이라야 분자가 움직인다."""
        company_id = _legacy_insert(db, _PRINTED, "옛 표기로 등록된 기업")
        _certify(db, company_id)
        _purchase(db, _STORED, "1000")

        client = TestClient(create_app(db, period_date_field=PAYMENT_DATE))
        client.post("/purchases/rematch")

        calculator = _calculator(db)
        assert calculator.calculate_total_purchase(_period()) == Decimal("1000")
        assert calculator.calculate_policy_purchase(_policy_id(db), _period()) == Decimal("1000")

    def test_matching_without_certification_moves_nothing(self, db: Path) -> None:
        """⛔ 연결되었다고 실적이 되지 않는다 — 인증이 있어야 한다."""
        _legacy_insert(db, _PRINTED, "인증 없는 기업")
        _purchase(db, _STORED, "1000")

        client = TestClient(create_app(db, period_date_field=PAYMENT_DATE))
        client.post("/purchases/rematch")

        assert _calculator(db).calculate_policy_purchase(_policy_id(db), _period()) == Decimal("0")

    def test_the_denominator_does_not_move(self, db: Path) -> None:
        """⛔ 분모는 기업 매칭과 무관하다."""
        calculator = _calculator(db)
        _purchase(db, _STORED, "1000")
        before = calculator.calculate_total_purchase(_period())

        company_id = _legacy_insert(db, _PRINTED, "옛 표기")
        _certify(db, company_id)
        TestClient(create_app(db, period_date_field=PAYMENT_DATE)).post("/purchases/rematch")

        assert calculator.calculate_total_purchase(_period()) == before == Decimal("1000")

    def test_performance_exclusion_still_wins(self, db: Path) -> None:
        """⛔ 매칭되었다고 실적 제외가 풀리지 않는다."""
        company_id = _legacy_insert(db, _PRINTED, "옛 표기")
        _certify(db, company_id)
        purchase_id = _purchase(db, _STORED, "1000", budget_account="의료비")

        client = TestClient(create_app(db, period_date_field=PAYMENT_DATE))
        client.post("/purchases/rematch")

        assert client.get(f"/reviews/{purchase_id}").json()["performance"]["status"] == EXCLUDED
        calculator = _calculator(db)
        assert calculator.calculate_total_purchase(_period()) == Decimal("0")
        assert calculator.calculate_policy_purchase(_policy_id(db), _period()) == Decimal("0")

    def test_the_period_filter_still_applies(self, db: Path) -> None:
        """⛔ 매칭되었다고 기간 밖 구매가 들어오지 않는다."""
        company_id = _legacy_insert(db, _PRINTED, "옛 표기")
        _certify(db, company_id)
        _purchase(db, _STORED, "1000", day=date(2025, 3, 1))

        TestClient(create_app(db, period_date_field=PAYMENT_DATE)).post("/purchases/rematch")

        calculator = _calculator(db)
        assert calculator.calculate_policy_purchase(_policy_id(db), _period()) == Decimal("0")
        assert calculator.calculate_policy_purchase(
            _policy_id(db), PeriodFilter.for_year(2025, PAYMENT_DATE)
        ) == Decimal("1000")
