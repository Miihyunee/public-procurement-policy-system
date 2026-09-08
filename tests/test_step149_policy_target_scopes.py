"""STEP 149 — 목표비율 입력칸과 실적 화면 목표 표시.

STEP 148 Windows 검증에서 드러난 **기존 결함 2건**입니다.

① 여성기업의 공사·용역·물품 입력칸이 화면에 나타나지 않았습니다.

   ``GET /policy-targets`` 가 **이미 저장된** 목표만 돌려주었고, 화면은 그것을
   보고 칸을 그렸습니다. 그래서 세 칸은 값이 있을 때만 나타났고 첫 값을 넣을
   자리가 없었습니다 — 닭과 달걀. 매뉴얼 9장은 「입력칸이 세 개 나옵니다」 라고
   안내하는데 화면은 한 칸이었고, 그 한 칸은 기관 전체 구매금액(``TOTAL``)
   기준으로 저장되어 **분모가 틀린** 값이 되었습니다.

② 저장한 목표비율이 실적 화면에 «—» 로 나왔습니다.

   조회불가·계산 보류 상태에서 ``target_rate`` 를 ``None`` 으로 버렸습니다.
   담당자는 방금 넣은 50% 가 사라진 것으로 읽습니다 — 실제로 그렇게 읽혔습니다.

.. warning::
    ⛔ 이번 수정은 위 둘뿐입니다. 계산 규칙·구매유형 판정·기업정보 처리는
    건드리지 않았습니다.
    ⛔ 관리자 인증(``require_admin_token``)과 STEP 147 세션 토큰 배선을
    그대로 둡니다.

⚠️ 데이터는 전부 **합성**입니다. 사업자번호도 합성값이며 고객 자료가 아닙니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.core.target_scope import PRODUCIBLE_ITEMS, TOTAL
from procurement.database.bootstrap import MVP_POLICY_SEEDS, init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.models import Certification, Company
from procurement.policy import SCOPES_BY_POLICY, scopes_for

#: 테스트 전용 관리자 토큰(운영 값 아님).
TEST_TOKEN = "step149-test-token"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

#: 합성 사업자등록번호. ⛔ 실제 고객 값이 아닙니다.
SYNTHETIC_BUSINESS_NO = "1000000009"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step149.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, admin_token=TEST_TOKEN))


def _register_woman_company(db_path: Path) -> None:
    """여성기업 인증 1건을 넣어 «조회불가» 를 벗어나게 합니다(합성 데이터)."""
    policy = PolicyRepository(db_path).find_by_policy_code("WOMAN")
    assert policy is not None and policy.policy_id is not None
    company = CompanyRepository(db_path).insert(
        Company(business_no=SYNTHETIC_BUSINESS_NO, company_name="합성여성기업")
    )
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )


def _item(client: TestClient, code: str, year: int = 2026) -> dict:
    listed = client.get(f"/policy-targets?year={year}").json()
    return next(item for item in listed["items"] if item["policy_code"] == code)


def _scoped(item: dict) -> dict[str, str | None]:
    return {entry["scope"]: entry["target_rate"] for entry in item["scoped_targets"]}


def _summary(client: TestClient, code: str, year: int = 2026) -> dict:
    body = client.get(f"/dashboard/summary?year={year}").json()
    return next(policy for policy in body["policies"] if policy["policy_code"] == code)


# ----------------------------------------------------------------------
# ① 정책별 분모 기준이 코드의 정본이다
# ----------------------------------------------------------------------
class TestCanonicalScopes:
    """⛔ 저장된 값이 있는지로 추론하지 않는다."""

    def test_woman_uses_the_three_purchase_types(self) -> None:
        assert scopes_for("WOMAN") == (CONSTRUCTION, SERVICE, GOODS)

    def test_ordinary_policies_use_the_whole_amount(self) -> None:
        for code in (
            "SMALL_BUSINESS",
            "STARTUP",
            "SOCIAL_ENTERPRISE",
            "SOCIAL_COOPERATIVE",
            "DISABLED",
            "DISABLED_STANDARD_WORKPLACE",
        ):
            assert scopes_for(code) == (TOTAL,), code

    def test_self_support_village_keeps_its_own_basis(self) -> None:
        """⚠️ 기존 의미를 유지한다 — 생산가능품목 기준이며 TOTAL 이 아니다."""
        assert scopes_for("SELF_SUPPORT_VILLAGE") == (PRODUCIBLE_ITEMS,)

    def test_every_active_policy_has_a_basis(self) -> None:
        for seed in MVP_POLICY_SEEDS:
            if not seed.is_active:
                continue
            assert scopes_for(seed.policy_code), seed.policy_code

    def test_the_definition_is_not_duplicated(self) -> None:
        """⛔ 같은 사실을 적은 목록을 두 벌 만들지 않는다."""
        from procurement.policy import confirmed_targets

        source = Path(confirmed_targets.__file__).read_text(encoding="utf-8")
        # 확정 목록에서 끌어낸다 — 손으로 다시 적지 않는다.
        assert "def _scopes_by_policy()" in source
        assert "for target in CONFIRMED_TARGETS" in source

    def test_it_matches_what_the_customer_confirmed(self) -> None:
        from procurement.policy import CONFIRMED_TARGETS

        for target in CONFIRMED_TARGETS:
            assert target.scope in SCOPES_BY_POLICY[target.policy_code], target.policy_code


# ----------------------------------------------------------------------
# ② 빈 DB 에서도 입력칸이 나온다
# ----------------------------------------------------------------------
class TestEmptyDatabaseStillOffersTheBoxes:
    """이 결함의 핵심 — 값이 없어도 기준은 나와야 한다."""

    def test_woman_offers_three_scopes(self, client: TestClient) -> None:
        scoped = _scoped(_item(client, "WOMAN"))

        assert list(scoped) == [CONSTRUCTION, SERVICE, GOODS]

    def test_their_rates_are_null_not_zero(self, client: TestClient) -> None:
        """⛔ 미설정을 0 으로 채우지 않는다."""
        scoped = _scoped(_item(client, "WOMAN"))

        assert set(scoped.values()) == {None}

    def test_the_scope_labels_come_from_the_server(self, client: TestClient) -> None:
        labels = [entry["scope_label"] for entry in _item(client, "WOMAN")["scoped_targets"]]

        assert labels == ["공사", "용역", "물품"]

    def test_ordinary_policies_offer_one_box(self, client: TestClient) -> None:
        scoped = _scoped(_item(client, "SMALL_BUSINESS"))

        assert list(scoped) == [TOTAL]
        assert scoped[TOTAL] is None

    def test_self_support_village_offers_its_own_box(self, client: TestClient) -> None:
        scoped = _scoped(_item(client, "SELF_SUPPORT_VILLAGE"))

        assert list(scoped) == [PRODUCIBLE_ITEMS]

    def test_a_basis_without_a_denominator_says_so(self, client: TestClient) -> None:
        """저장은 되지만 달성률은 못 낸다는 사실을 화면이 알 수 있어야 한다."""
        entry = _item(client, "SELF_SUPPORT_VILLAGE")["scoped_targets"][0]

        assert entry["calculable"] is False


# ----------------------------------------------------------------------
# ③ 저장과 재조회
# ----------------------------------------------------------------------
class TestSavingScopedTargets:
    """화면이 보내는 요청 그대로."""

    def test_woman_three_five_five_round_trip(self, client: TestClient) -> None:
        wanted = {CONSTRUCTION: "3", SERVICE: "5", GOODS: "5"}

        for scope, rate in wanted.items():
            response = client.put(
                f"/policy-targets/2026/WOMAN/{scope}",
                json={"target_rate": rate},
                headers=AUTH,
            )
            assert response.status_code == 200, (scope, response.text)

        assert _scoped(_item(client, "WOMAN")) == wanted

    def test_saving_one_scope_keeps_the_others(self, client: TestClient) -> None:
        """⛔ 하나를 고쳤다고 나머지가 사라지면 안 된다."""
        for scope, rate in ((CONSTRUCTION, "3"), (SERVICE, "5"), (GOODS, "5")):
            client.put(
                f"/policy-targets/2026/WOMAN/{scope}", json={"target_rate": rate}, headers=AUTH
            )

        client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}",
            json={"target_rate": "4"},
            headers=AUTH,
        )

        assert _scoped(_item(client, "WOMAN")) == {CONSTRUCTION: "4", SERVICE: "5", GOODS: "5"}

    def test_clearing_one_scope_leaves_the_box(self, client: TestClient) -> None:
        """해제해도 **칸은 남는다** — 다시 넣을 수 있어야 한다."""
        client.put(f"/policy-targets/2026/WOMAN/{SERVICE}", json={"target_rate": "5"}, headers=AUTH)

        client.put(
            f"/policy-targets/2026/WOMAN/{SERVICE}", json={"target_rate": None}, headers=AUTH
        )

        scoped = _scoped(_item(client, "WOMAN"))
        assert list(scoped) == [CONSTRUCTION, SERVICE, GOODS]
        assert scoped[SERVICE] is None

    def test_small_business_fifty_round_trip(self, client: TestClient) -> None:
        response = client.put(
            "/policy-targets/2026/SMALL_BUSINESS", json={"target_rate": "50"}, headers=AUTH
        )

        assert response.status_code == 200
        item = _item(client, "SMALL_BUSINESS")
        assert Decimal(str(item["target_rate"])) == Decimal("50")
        assert _scoped(item) == {TOTAL: "50"}

    def test_other_years_are_untouched(self, client: TestClient) -> None:
        """⛔ 연도끼리 값을 빌려오지 않는다."""
        client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}",
            json={"target_rate": "3"},
            headers=AUTH,
        )

        assert _scoped(_item(client, "WOMAN", year=2025)) == {
            CONSTRUCTION: None,
            SERVICE: None,
            GOODS: None,
        }


class TestWrongBasisIsRefused:
    """⛔ 화면만 고치지 않는다 — 어느 경로로 와도 막힌다."""

    def test_woman_cannot_be_saved_without_a_purchase_type(self, client: TestClient) -> None:
        response = client.put("/policy-targets/2026/WOMAN", json={"target_rate": "8"}, headers=AUTH)

        assert response.status_code == 422

    def test_the_message_says_what_is_allowed(self, client: TestClient) -> None:
        response = client.put("/policy-targets/2026/WOMAN", json={"target_rate": "8"}, headers=AUTH)

        detail = response.json()["detail"]
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert scope in detail, detail

    def test_nothing_is_stored_when_refused(self, client: TestClient) -> None:
        client.put("/policy-targets/2026/WOMAN", json={"target_rate": "8"}, headers=AUTH)

        assert set(_scoped(_item(client, "WOMAN")).values()) == {None}

    def test_an_ordinary_policy_cannot_take_a_purchase_type(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/SMALL_BUSINESS/{CONSTRUCTION}",
            json={"target_rate": "3"},
            headers=AUTH,
        )

        assert response.status_code == 422

    def test_ordinary_policies_still_save_the_old_way(self, client: TestClient) -> None:
        """⛔ 다른 정책의 기존 저장 동작은 바꾸지 않았다."""
        for code in (
            "SMALL_BUSINESS",
            "STARTUP",
            "SOCIAL_ENTERPRISE",
            "SOCIAL_COOPERATIVE",
            "DISABLED",
            "DISABLED_STANDARD_WORKPLACE",
        ):
            response = client.put(
                f"/policy-targets/2026/{code}", json={"target_rate": "1"}, headers=AUTH
            )
            assert response.status_code == 200, (code, response.text)

    def test_self_support_village_saves_on_its_own_basis(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/SELF_SUPPORT_VILLAGE/{PRODUCIBLE_ITEMS}",
            json={"target_rate": "7"},
            headers=AUTH,
        )

        assert response.status_code == 200
        assert _scoped(_item(client, "SELF_SUPPORT_VILLAGE")) == {PRODUCIBLE_ITEMS: "7"}


# ----------------------------------------------------------------------
# ④ 실적 화면의 「목표」 칸
# ----------------------------------------------------------------------
class TestDashboardShowsTheSavedTarget:
    """목표비율은 계산 결과가 아니라 담당자가 넣은 설정값이다."""

    def test_target_shows_even_when_company_data_is_missing(self, client: TestClient) -> None:
        client.put("/policy-targets/2026/SMALL_BUSINESS", json={"target_rate": "50"}, headers=AUTH)

        summary = _summary(client, "SMALL_BUSINESS")
        assert summary["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert Decimal(str(summary["target_rate"])) == Decimal("50")

    def test_the_calculated_values_stay_none(self, client: TestClient) -> None:
        """⛔ 목표를 보여 준다고 달성률까지 만들어 내지 않는다."""
        client.put("/policy-targets/2026/SMALL_BUSINESS", json={"target_rate": "50"}, headers=AUTH)

        summary = _summary(client, "SMALL_BUSINESS")
        assert summary["achievement_rate"] is None
        assert summary["shortage_rate"] is None
        assert summary["purchase_amount"] is None

    def test_an_unset_policy_still_shows_nothing(self, client: TestClient) -> None:
        """⛔ 목표를 넣지 않았으면 그대로 «미설정» 이다."""
        summary = _summary(client, "STARTUP")

        assert summary["target_rate"] is None

    def test_on_hold_policy_shows_its_target(self, client: TestClient, db_path: Path) -> None:
        """자활용사촌 — 분모를 못 구해도 목표는 보인다."""
        policy = PolicyRepository(db_path).find_by_policy_code("SELF_SUPPORT_VILLAGE")
        assert policy is not None and policy.policy_id is not None
        company = CompanyRepository(db_path).insert(
            Company(business_no="1000000014", company_name="합성자활용사촌")
        )
        assert company.company_id is not None
        CertificationRepository(db_path).insert(
            Certification(
                company_id=company.company_id,
                policy_id=policy.policy_id,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )
        client.put(
            f"/policy-targets/2026/SELF_SUPPORT_VILLAGE/{PRODUCIBLE_ITEMS}",
            json={"target_rate": "7"},
            headers=AUTH,
        )

        summary = _summary(client, "SELF_SUPPORT_VILLAGE")
        assert summary["status"] == "CALCULATION_ON_HOLD"
        assert Decimal(str(summary["target_rate"])) == Decimal("7")
        assert summary["achievement_rate"] is None

    def test_woman_shows_each_purchase_type_target(self, client: TestClient, db_path: Path) -> None:
        """계산 보류여도 공사 3 · 용역 5 · 물품 5 가 보인다."""
        _register_woman_company(db_path)
        for scope, rate in ((CONSTRUCTION, "3"), (SERVICE, "5"), (GOODS, "5")):
            client.put(
                f"/policy-targets/2026/WOMAN/{scope}", json={"target_rate": rate}, headers=AUTH
            )

        summary = _summary(client, "WOMAN")
        assert summary["status"] == "SCOPED_BY_PURCHASE_TYPE"
        got = {
            entry["scope"]: Decimal(str(entry["target_rate"]))
            for entry in summary["scoped_achievements"]
        }
        assert got == {
            CONSTRUCTION: Decimal("3"),
            SERVICE: Decimal("5"),
            GOODS: Decimal("5"),
        }

    def test_woman_scoped_rates_stay_uncalculated(self, client: TestClient, db_path: Path) -> None:
        """⛔ 목표가 보인다고 달성률이 생기지 않는다(STEP 140 규칙 유지)."""
        _register_woman_company(db_path)
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            client.put(
                f"/policy-targets/2026/WOMAN/{scope}", json={"target_rate": "5"}, headers=AUTH
            )

        summary = _summary(client, "WOMAN")
        for entry in summary["scoped_achievements"]:
            assert entry["achievement_rate"] is None
            assert entry["status"] == "CALCULATION_ON_HOLD"


# ----------------------------------------------------------------------
# ⑤ 화면 — 저장된 값이 아니라 기준으로 그린다
# ----------------------------------------------------------------------
class TestScreenDrawsFromTheOfferedScopes:
    """``index.html``."""

    @pytest.fixture(scope="class")
    def page(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "procurement"
            / "web"
            / "static"
            / "index.html"
        )
        return path.read_text(encoding="utf-8")

    def test_an_empty_rate_renders_an_empty_box(self, page: str) -> None:
        assert 'input.value = scoped.target_rate === null ? "" : scoped.target_rate;' in page

    def test_each_scope_gets_its_own_input(self, page: str) -> None:
        assert 'input.id = "pt-input-" + item.policy_code + "-" + scoped.scope;' in page
        assert 'input.setAttribute("data-scope", scoped.scope);' in page

    def test_saving_sends_the_scope(self, page: str) -> None:
        assert 'if (change.scope) { url += "/" + encodeURIComponent(change.scope); }' in page

    def test_unset_boxes_say_so(self, page: str) -> None:
        """⛔ 빈칸에 0 을 넣어 두지 않는다."""
        assert 'input.placeholder = "미설정";' in page


# ----------------------------------------------------------------------
# ⑥ STEP 147 인증 회귀
# ----------------------------------------------------------------------
class TestAdminGuardIsUnchanged:
    """⛔ 이번 수정으로 인증을 약화시키지 않았다."""

    def test_no_header_is_rejected(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}", json={"target_rate": "3"}
        )

        assert response.status_code == 401

    def test_wrong_token_is_rejected(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}",
            json={"target_rate": "3"},
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == 401

    def test_the_session_token_still_works(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}",
            json={"target_rate": "3"},
            headers=AUTH,
        )

        assert response.status_code == 200

    def test_writes_stay_disabled_without_a_token(self, db_path: Path) -> None:
        unguarded = TestClient(create_app(db_path, admin_token=""))

        response = unguarded.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}", json={"target_rate": "3"}
        )

        assert response.status_code == 503

    def test_the_guard_is_still_declared(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "procurement" / "app.py").read_text(
            encoding="utf-8"
        )

        assert source.count("dependencies=[Depends(require_admin_token)]") == 3
