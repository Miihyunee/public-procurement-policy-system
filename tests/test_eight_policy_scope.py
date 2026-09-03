"""
tests.test_eight_policy_scope

**최종 정책 범위 8종**과 정책별 독립성 (STEP 97).

2026-09-03 PM 확정으로 관리 대상이 8종이 되었습니다. 이 파일이 지키는 것은
셋입니다.

1. 8종이 **모두 관리 대상**이다 — 기업정보가 없어도 목록에서 빠지지 않는다
2. 정책이 **있는 것**과 그 정책의 **기업정보가 등록된 것**은 다르다
3. 8종이 **서로 독립**이다 — 등록도, 실적도, 목표비율도

.. warning::
    ⛔ **합성 데이터만 사용합니다.** 사업자등록번호는 체크섬을 만족하는
    형식값이며 실제 거래처가 아닙니다. 실제 고객 파일을 쓰지 않았습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.dashboard.models import DashboardStatus
from procurement.database.bootstrap import MVP_POLICY_SEEDS, bootstrap, seed_policies
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.purchase import Purchase
from procurement.uploads.company_format import policy_scoped_header_row

ADMIN_TOKEN = "step97-token-not-a-real-secret"

#: 2026-09-03 PM 확정(STEP 97 §2) — 최종 관리 대상 8종.
FINAL_POLICY_CODES: tuple[str, ...] = (
    "SMALL_BUSINESS",
    "WOMAN",
    "STARTUP",
    "DISABLED",
    "SOCIAL_ENTERPRISE",
    "SOCIAL_COOPERATIVE",
    "DISABLED_STANDARD_WORKPLACE",
    "SELF_SUPPORT_VILLAGE",
)

#: 이번에 새로 등록한 4종.
NEW_POLICY_CODES: tuple[str, ...] = FINAL_POLICY_CODES[4:]

#: 합성 사업자등록번호(체크섬 만족).
BIZ_A = "220-81-62517"
BIZ_B = "104-81-24017"
NORM_A = "2208162517"
NORM_B = "1048124017"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "step97.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=db_path, admin_token=ADMIN_TOKEN))


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None
    assert policy.policy_id is not None
    return policy.policy_id


def _policy_file(path: Path, rows: list[list[object]]) -> Path:
    """정책을 고르고 올리는 파일 — ⛔ 인증종류 칸이 없다."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(policy_scoped_header_row()))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def _row(business_no: str, name: str) -> list[object]:
    return [business_no, name, "홍길동", date(2026, 1, 1), date(2026, 12, 31)]


def _register(client: TestClient, path: Path, policy_code: str) -> dict[str, object]:
    response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _summary(client: TestClient, year: int = 2026) -> dict[str, dict[str, object]]:
    body = client.get(f"/dashboard/summary?year={year}").json()
    return {item["policy_code"]: item for item in body["policies"]}


def _seed_purchase(db_path: Path, business_no: str, amount: str) -> None:
    PurchaseRepository(db_path).insert(
        Purchase(
            business_no=business_no,
            company_name="거래처",
            amount=Decimal(amount),
            resolution_date=date(2026, 3, 10),
        )
    )


# ======================================================================
# §2 · §17  정책 범위
# ======================================================================
class TestFinalPolicyScope:
    """최종 8종이 관리 대상인가."""

    def test_all_eight_are_seeded_and_active(self, db_path: Path) -> None:
        active = {policy.policy_code for policy in PolicyRepository(db_path).find_active()}

        assert active == set(FINAL_POLICY_CODES)

    def test_the_existing_four_codes_were_not_renamed(self, db_path: Path) -> None:
        """⛔ §2 — 기존 코드를 임의로 바꾸지 않았다."""
        codes = {policy.policy_code for policy in PolicyRepository(db_path).find_all()}

        for code in ("SMALL_BUSINESS", "WOMAN", "STARTUP", "DISABLED"):
            assert code in codes

    def test_green_stays_registered_but_inactive(self, db_path: Path) -> None:
        """⛔ 녹색제품 행을 지우지 않았다 — 비활성으로 이력만 보존한다."""
        policy = PolicyRepository(db_path).find_by_policy_code("GREEN")

        assert policy is not None
        assert policy.is_active is False

    @pytest.mark.parametrize("code", NEW_POLICY_CODES)
    def test_new_policies_have_no_invented_target_rate(self, db_path: Path, code: str) -> None:
        """⛔ §18 — 목표비율을 임의로 만들지 않았다."""
        policy = PolicyRepository(db_path).find_by_policy_code(code)

        assert policy is not None
        assert policy.target_rate is None

    @pytest.mark.parametrize("code", NEW_POLICY_CODES)
    def test_new_policies_use_the_confirmed_general_basis(self, code: str) -> None:
        """⭐ §15 — 판정 기준일은 **결의일자**다. 새 규칙을 만들지 않았다."""
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == code)

        assert seed.evaluation_basis == "RESOLUTION_DATE"

    def test_startup_keeps_its_or_rule(self) -> None:
        """⛔ 창업기업만 OR 규칙이다. 이번 STEP 에서 바꾸지 않았다."""
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")

        assert seed.evaluation_basis == "RESOLUTION_OR_CONTRACT_DATE"

    def test_seeding_is_idempotent(self, db_path: Path) -> None:
        """다시 실행해도 중복 등록되지 않는다."""
        created = seed_policies(db_path)

        assert created == []
        assert len(PolicyRepository(db_path).find_all()) == len(MVP_POLICY_SEEDS)


# ======================================================================
# §3 · §4  등록 화면
# ======================================================================
class TestRegistrationScreen:
    """화면에 8종이 나오고, 고를 수 있는 방법만 나온다."""

    def test_all_eight_appear(self, client: TestClient) -> None:
        body = client.get("/companies/registration").json()

        codes = [item["policy_code"] for item in body["items"]]
        assert set(codes) == set(FINAL_POLICY_CODES)
        assert len(codes) == 8

    def test_all_eight_start_unregistered(self, client: TestClient) -> None:
        """⭐ 정책이 **있는 것**과 기업정보가 **등록된 것**은 다르다."""
        body = client.get("/companies/registration").json()

        assert all(item["registered"] is False for item in body["items"])
        assert all(item["status_label"] == "미등록" for item in body["items"])

    @pytest.mark.parametrize(
        ("code", "methods"),
        [
            ("SMALL_BUSINESS", ["FILE"]),
            ("WOMAN", ["FILE", "API"]),
            ("STARTUP", ["FILE", "API"]),
            ("DISABLED", ["FILE", "API"]),
            ("SOCIAL_ENTERPRISE", ["FILE"]),
            ("SOCIAL_COOPERATIVE", ["FILE"]),
            ("DISABLED_STANDARD_WORKPLACE", ["FILE"]),
            ("SELF_SUPPORT_VILLAGE", ["FILE"]),
        ],
    )
    def test_only_implemented_methods_are_offered(
        self, client: TestClient, code: str, methods: list[str]
    ) -> None:
        """⛔ §4 — 조회가 구현되지 않은 정책에 API 를 만들지 않는다."""
        body = client.get("/companies/registration").json()
        by_code = {item["policy_code"]: item for item in body["items"]}

        assert by_code[code]["available_methods"] == methods

    def test_the_four_new_policies_are_file_only(self, client: TestClient) -> None:
        """⭐ 새 4종은 조회 출처가 없으므로 파일 방식뿐이다."""
        body = client.get("/companies/registration").json()
        by_code = {item["policy_code"]: item for item in body["items"]}

        for code in NEW_POLICY_CODES:
            assert by_code[code]["available_methods"] == ["FILE"]


# ======================================================================
# §5 · §17  정책별 등록 독립성
# ======================================================================
class TestPerPolicyRegistration:
    """정책마다 따로 등록되고 서로 간섭하지 않는다."""

    @pytest.mark.parametrize("code", FINAL_POLICY_CODES)
    def test_every_policy_accepts_a_file(
        self, client: TestClient, tmp_path: Path, code: str
    ) -> None:
        """⭐ 8종 **모두** 파일을 연결할 수 있다."""
        path = _policy_file(tmp_path / f"{code}.xlsx", [_row(BIZ_A, "가나산업")])

        body = _register(client, path, code)

        assert body["stored"] is True
        assert body["certifications"] == 1

    def test_registering_one_leaves_the_others_unregistered(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 등록 상태가 정책별로 독립이다."""
        _register(
            client,
            _policy_file(tmp_path / "sv.xlsx", [_row(BIZ_A, "가나산업")]),
            "SELF_SUPPORT_VILLAGE",
        )

        body = client.get("/companies/registration").json()
        by_code = {item["policy_code"]: item for item in body["items"]}

        assert by_code["SELF_SUPPORT_VILLAGE"]["registered"] is True
        for code in FINAL_POLICY_CODES:
            if code != "SELF_SUPPORT_VILLAGE":
                assert by_code[code]["registered"] is False, code


# ======================================================================
# §9 · §10  해당 / 미해당 / 조회불가
# ======================================================================
class TestThreeStates:
    """§10 의 예시를 그대로 고정한다."""

    @pytest.fixture
    def scenario(self, client: TestClient, db_path: Path, tmp_path: Path) -> Path:
        """§10 — 중소기업 · 여성기업 · 자활용사촌만 등록한다.

        사업자 A 는 중소기업·자활용사촌 목록에 있고, 여성기업 목록에는 없다.
        """
        _seed_purchase(db_path, NORM_A, "1000000")

        for code in ("SMALL_BUSINESS", "SELF_SUPPORT_VILLAGE"):
            _register(
                client,
                _policy_file(tmp_path / f"{code}.xlsx", [_row(BIZ_A, "A기업")]),
                code,
            )
        # 여성기업 목록은 받았지만 A 가 없다 → **미해당**
        _register(client, _policy_file(tmp_path / "w.xlsx", [_row(BIZ_B, "B기업")]), "WOMAN")
        client.post("/purchases/rematch")
        return db_path

    def test_matched_policies_count_the_purchase(self, scenario: Path, client: TestClient) -> None:
        """해당 — 목록에 있고 유효기간을 만족한다."""
        summary = _summary(client)

        assert summary["SMALL_BUSINESS"]["purchase_amount"] == "1000000"
        assert summary["SELF_SUPPORT_VILLAGE"]["purchase_amount"] == "1000000"

    def test_registered_but_absent_is_zero_not_null(
        self, scenario: Path, client: TestClient
    ) -> None:
        """⭐ 미해당 — 목록은 받았고 그 안에 없다. **0 이며 null 이 아니다.**"""
        item = _summary(client)["WOMAN"]

        assert item["purchase_amount"] == "0"
        assert item["status"] != DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value

    @pytest.mark.parametrize(
        "code",
        [
            "STARTUP",
            "DISABLED",
            "SOCIAL_ENTERPRISE",
            "SOCIAL_COOPERATIVE",
            "DISABLED_STANDARD_WORKPLACE",
        ],
    )
    def test_unregistered_policies_are_unknown(
        self, scenario: Path, client: TestClient, code: str
    ) -> None:
        """⭐ 조회불가 — 목록을 받지 못했다. ⛔ 미해당도 0원도 아니다."""
        item = _summary(client)[code]

        assert item["status"] == DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        assert item["purchase_amount"] is None
        assert item["achievement_rate"] is None

    def test_unknown_is_never_zero(self, scenario: Path, client: TestClient) -> None:
        """⛔ §18 — 0원·0% 어느 모양으로도 나가지 않는다."""
        item = _summary(client)["SOCIAL_ENTERPRISE"]

        for key in ("purchase_amount", "achievement_rate", "shortage_rate"):
            assert item[key] is None, key
            assert item[key] != "0"
            assert item[key] != 0

    def test_the_three_states_coexist(self, scenario: Path, client: TestClient) -> None:
        """⭐ 한 화면에 세 상태가 동시에 나온다."""
        summary = _summary(client)

        assert summary["SMALL_BUSINESS"]["purchase_amount"] == "1000000"  # 해당
        assert summary["WOMAN"]["purchase_amount"] == "0"  # 미해당
        assert summary["STARTUP"]["purchase_amount"] is None  # 조회불가


# ======================================================================
# §11  정책 간 중복 허용
# ======================================================================
class TestPolicyOverlap:
    """한 사업자가 여러 정책에 동시에 해당한다."""

    @pytest.fixture
    def overlapped(self, client: TestClient, db_path: Path, tmp_path: Path) -> Path:
        """§11 — 사업자 A 가 4개 정책 목록에 모두 있다."""
        _seed_purchase(db_path, NORM_A, "1000000")
        for code in ("SMALL_BUSINESS", "WOMAN", "STARTUP", "SELF_SUPPORT_VILLAGE"):
            _register(
                client,
                _policy_file(tmp_path / f"{code}.xlsx", [_row(BIZ_A, "A기업")]),
                code,
            )
        client.post("/purchases/rematch")
        return db_path

    def test_the_same_amount_lands_in_every_policy(
        self, overlapped: Path, client: TestClient
    ) -> None:
        """⭐ 같은 100만원이 네 정책 실적에 **각각** 들어간다."""
        summary = _summary(client)

        for code in ("SMALL_BUSINESS", "WOMAN", "STARTUP", "SELF_SUPPORT_VILLAGE"):
            assert summary[code]["purchase_amount"] == "1000000", code

    def test_the_sum_exceeds_the_total_and_that_is_correct(
        self, overlapped: Path, client: TestClient
    ) -> None:
        """⛔ 정책 간 중복을 제거하지 않는다."""
        body = client.get("/dashboard/summary?year=2026").json()
        total = Decimal(body["total_purchase_amount"])
        summed = sum(
            Decimal(item["purchase_amount"])
            for item in body["policies"]
            if item["purchase_amount"] is not None
        )

        assert total == Decimal("1000000")
        assert summed == Decimal("4000000")
        assert summed > total


# ======================================================================
# §12 · §13  정책별 목표비율
# ======================================================================
class TestPerPolicyTargets:
    """8종 각각에 연도별 목표비율을 둘 수 있다."""

    @pytest.mark.parametrize("code", FINAL_POLICY_CODES)
    def test_every_policy_accepts_a_target(self, client: TestClient, code: str) -> None:
        response = client.put(
            f"/policy-targets/2026/{code}",
            json={"target_rate": "37.5"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

        assert response.status_code == 200
        assert response.json()["target_rate"] == "37.5"

    def test_targets_do_not_leak_between_policies(self, client: TestClient, db_path: Path) -> None:
        """⭐ 정책 A 의 목표비율이 정책 B 에 영향을 주지 않는다."""
        PolicyTargetRepository(db_path).upsert(
            2026, _policy_id(db_path, "SOCIAL_ENTERPRISE"), Decimal("60")
        )

        body = client.get("/policy-targets?year=2026").json()
        rates = {item["policy_code"]: item["target_rate"] for item in body["items"]}

        assert rates["SOCIAL_ENTERPRISE"] == "60"
        for code in FINAL_POLICY_CODES:
            if code != "SOCIAL_ENTERPRISE":
                assert rates[code] is None, code

    def test_targets_do_not_leak_between_years(self, client: TestClient, db_path: Path) -> None:
        """연도별 목표비율이 분리된다."""
        PolicyTargetRepository(db_path).upsert(
            2026, _policy_id(db_path, "SELF_SUPPORT_VILLAGE"), Decimal("10")
        )

        body_2027 = client.get("/policy-targets?year=2027").json()
        rates = {item["policy_code"]: item["target_rate"] for item in body_2027["items"]}

        assert rates["SELF_SUPPORT_VILLAGE"] is None

    def test_the_target_listing_covers_all_eight(self, client: TestClient) -> None:
        body = client.get("/policy-targets?year=2026").json()

        assert {item["policy_code"] for item in body["items"]} == set(FINAL_POLICY_CODES)


# ======================================================================
# §13  기업정보 등록 ≠ 목표비율 등록
# ======================================================================
class TestRegisteredButNoTarget:
    """⭐ §13 — 목표비율이 없어도 **실적과 구매비율은 보여 준다.**"""

    @pytest.fixture
    def registered(self, client: TestClient, db_path: Path, tmp_path: Path) -> Path:
        _seed_purchase(db_path, NORM_A, "1000000")
        _register(
            client,
            _policy_file(tmp_path / "se.xlsx", [_row(BIZ_A, "A기업")]),
            "SOCIAL_ENTERPRISE",
        )
        client.post("/purchases/rematch")
        return db_path

    def test_the_amount_is_calculated(self, registered: Path, client: TestClient) -> None:
        """실적은 센다 — 누가 해당하는지 알기 때문이다."""
        item = _summary(client)["SOCIAL_ENTERPRISE"]

        assert item["purchase_amount"] == "1000000"
        assert item["total_purchase_amount"] == "1000000"

    def test_only_the_achievement_is_missing(self, registered: Path, client: TestClient) -> None:
        """달성률만 낼 수 없다 — 목표가 없기 때문이다."""
        item = _summary(client)["SOCIAL_ENTERPRISE"]

        assert item["target_rate"] is None
        assert item["achievement_rate"] is None
        assert item["status"] == DashboardStatus.TARGET_RATE_NOT_SET.value
        assert item["status_label"] == "목표율 미설정"

    def test_it_differs_from_unknown(self, registered: Path, client: TestClient) -> None:
        """⭐ 조회불가와 **다른 상태**다 — 실적 유무로 갈린다."""
        summary = _summary(client)

        assert summary["SOCIAL_ENTERPRISE"]["purchase_amount"] == "1000000"
        assert summary["SOCIAL_COOPERATIVE"]["purchase_amount"] is None
        assert (
            summary["SOCIAL_COOPERATIVE"]["status"]
            == DashboardStatus.COMPANY_DATA_NOT_REGISTERED.value
        )


# ======================================================================
# §18  하지 않기로 한 것
# ======================================================================
class TestForbidden:
    """⛔ 금지 목록."""

    def test_no_fuzzy_matching(self) -> None:
        import inspect

        from procurement.matchers.company_matcher import CompanyMatcher

        source = inspect.getsource(CompanyMatcher).lower()
        for term in ("fuzzy", "ratio", "similar", "levenshtein", "difflib", "bm25"):
            assert term not in source, term

    def test_company_size_is_still_unused(self) -> None:
        """⛔ 업체규모로 중소기업을 판정하지 않는다."""
        from pathlib import Path as _Path

        src = _Path(__file__).resolve().parents[1] / "src" / "procurement"
        hits = {
            path.name
            for path in src.rglob("*.py")
            if "업체규모" in path.read_text(encoding="utf-8")
        }
        assert hits == {"company_format.py"}, hits

    def test_the_resolution_date_basis_is_unchanged(self) -> None:
        from procurement.core.config import settings

        assert settings.PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    def test_the_calculator_was_not_rewritten(self) -> None:
        """⛔ 계산기는 정책 목록도 등록 여부도 모른다."""
        import inspect

        from procurement.calculators import ProcurementAchievementCalculator

        source = inspect.getsource(ProcurementAchievementCalculator)
        for term in ("SOCIAL_ENTERPRISE", "SELF_SUPPORT", "policy_company_source"):
            assert term not in source, term

    def test_policy_target_is_still_year_by_policy(self, db_path: Path) -> None:
        """⛔ 기업별 목표비율로 바꾸지 않았다."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_target)").fetchall()}
        conn.close()

        assert "company_id" not in columns
        assert "business_no" not in columns
        assert {"year", "policy_id", "target_rate"} <= columns
