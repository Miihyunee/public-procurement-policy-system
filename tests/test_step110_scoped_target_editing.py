"""
목표비율을 **사용자가 고칠 수 있는가** — 연도별 · 분모 기준별.

목표비율은 해마다 달라집니다. 그래서 값을 코드에 박아 두지 않고 화면에서
고칩니다. 연도별 수정은 이미 있었고(STEP 98), 여기서 막혀 있던 마지막
한 가지 — **구매유형마다 목표가 다른 정책**(여성기업: 공사 3% · 용역 5% ·
물품 5%) — 을 풉니다.

무엇을 지키는가
===============

1. 한 해의 목표를 고쳐도 **다른 해는 그대로**다.
2. 분모 기준마다 값을 **따로** 고칠 수 있다.
3. 한 기준을 고쳐도 **다른 기준은 그대로** 남는다.
4. 빈 값은 **해제**다 — ⛔ 0 으로 저장하지 않는다.
5. 저장할 수 있다고 계산까지 되는 것은 아니다 — 분모를 구할 수 없는 기준은
   값이 남되 달성률은 «계산 보류» 다.
6. 목표를 고치면 **달성률이 그 값으로 다시 계산**된다.

.. warning::
    ⛔ 유형별 목표를 하나로 합치거나 평균 내지 않습니다 — 그렇게 하면 고객이
    준 값이 아닌 숫자로 달성률을 재게 됩니다.

.. note::
    합성 데이터만 씁니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.core.target_scope import PRODUCIBLE_ITEMS, TOTAL
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 체크섬만 맞춘 값입니다.
_BUSINESS_NO = "1000000009"

#: 설정 변경(쓰기) API 에 필요한 관리자 토큰 — 시험용 합성 값입니다.
_ADMIN_TOKEN = "step110-token"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "targets.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, admin_token=_ADMIN_TOKEN))


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


def _put(
    client: TestClient,
    auth: dict[str, str],
    year: int,
    code: str,
    rate: str | None,
    scope: str | None = None,
) -> httpx.Response:
    url = f"/policy-targets/{year}/{code}"
    if scope is not None:
        url += f"/{scope}"
    response: httpx.Response = client.put(url, json={"target_rate": rate}, headers=auth)
    return response


def _scoped(client: TestClient, year: int, code: str) -> dict[str, str | None]:
    items = client.get("/policy-targets", params={"year": year}).json()["items"]
    row = next(item for item in items if item["policy_code"] == code)
    return {s["scope"]: s["target_rate"] for s in row["scoped_targets"]}


def _total(client: TestClient, year: int, code: str) -> str | None:
    items = client.get("/policy-targets", params={"year": year}).json()["items"]
    row = next(item for item in items if item["policy_code"] == code)
    value = row["target_rate"]
    return None if value is None else str(value)


class TestEachYearStandsAlone:
    """목표비율은 해마다 달라진다 — 한 해를 고쳐도 다른 해는 그대로."""

    def test_setting_one_year_leaves_the_others_untouched(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        assert _put(client, auth, 2026, "STARTUP", "3.4").status_code == 200
        assert _put(client, auth, 2027, "STARTUP", "5").status_code == 200

        assert _total(client, 2026, "STARTUP") == "3.4"
        assert _total(client, 2027, "STARTUP") == "5"
        # 손대지 않은 해는 여전히 미설정이다 — ⛔ 다른 해 값을 끌어오지 않는다.
        assert _total(client, 2025, "STARTUP") is None

    def test_clearing_one_year_leaves_the_others(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        _put(client, auth, 2026, "STARTUP", "3.4")
        _put(client, auth, 2027, "STARTUP", "5")
        assert _put(client, auth, 2026, "STARTUP", None).status_code == 200

        assert _total(client, 2026, "STARTUP") is None  # 해제 — ⛔ 0 이 아니다
        assert _total(client, 2027, "STARTUP") == "5"


class TestEachScopeStandsAlone:
    """분모 기준마다 따로 고친다 — 여성기업 공사·용역·물품."""

    def test_three_targets_are_stored_as_given(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        assert _put(client, auth, 2026, "WOMAN", "3", CONSTRUCTION).status_code == 200
        assert _put(client, auth, 2026, "WOMAN", "5", SERVICE).status_code == 200
        assert _put(client, auth, 2026, "WOMAN", "5", GOODS).status_code == 200

        # ⛔ 셋을 합치지도, 평균 내지도 않았다.
        assert _scoped(client, 2026, "WOMAN") == {
            CONSTRUCTION: "3",
            SERVICE: "5",
            GOODS: "5",
        }

    def test_editing_one_scope_leaves_the_others(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        _put(client, auth, 2026, "WOMAN", "3", CONSTRUCTION)
        _put(client, auth, 2026, "WOMAN", "5", SERVICE)
        _put(client, auth, 2026, "WOMAN", "4", CONSTRUCTION)  # 공사만 고친다

        assert _scoped(client, 2026, "WOMAN") == {CONSTRUCTION: "4", SERVICE: "5"}

    def test_clearing_one_scope_leaves_the_others(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        _put(client, auth, 2026, "WOMAN", "3", CONSTRUCTION)
        _put(client, auth, 2026, "WOMAN", "5", SERVICE)
        _put(client, auth, 2026, "WOMAN", None, CONSTRUCTION)

        assert _scoped(client, 2026, "WOMAN") == {SERVICE: "5"}

    def test_a_scoped_target_does_not_become_the_total_target(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        """⛔ 유형별 목표가 총액 목표 칸으로 새어 들어가면 안 된다.

        새어 들어가면 화면이 「공사 3%」를 기관 전체 구매금액 기준 3% 로 읽어,
        분모가 전혀 다른 달성률이 나옵니다.
        """
        response = _put(client, auth, 2026, "WOMAN", "3", CONSTRUCTION)
        assert response.json()["target_rate"] is None
        assert _total(client, 2026, "WOMAN") is None

    def test_the_total_scope_behaves_like_the_short_path(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        """``scope=TOTAL`` 은 기준 없는 경로와 완전히 같다."""
        _put(client, auth, 2026, "STARTUP", "3.4", TOTAL)
        assert _total(client, 2026, "STARTUP") == "3.4"

        _put(client, auth, 2027, "STARTUP", "3.4")
        assert _scoped(client, 2027, "STARTUP") == {TOTAL: "3.4"}


class TestStorableIsNotTheSameAsCalculable:
    """저장할 수 있다고 달성률이 나오는 것은 아니다."""

    def test_a_target_with_no_denominator_is_stored_and_flagged(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        assert (
            _put(client, auth, 2026, "SELF_SUPPORT_VILLAGE", "7", PRODUCIBLE_ITEMS).status_code
            == 200
        )
        items = client.get("/policy-targets", params={"year": 2026}).json()["items"]
        row = next(i for i in items if i["policy_code"] == "SELF_SUPPORT_VILLAGE")
        scoped = row["scoped_targets"][0]
        assert scoped["target_rate"] == "7"  # 값은 그대로 남는다
        assert scoped["calculable"] is False  # 분모를 구할 방법이 아직 없다


class TestBadInputIsRefused:
    """⛔ 아무 값이나 받지 않는다."""

    @pytest.mark.parametrize("scope", ["TOTALS", "공사", "PURCHASE", "TOTAL "])
    def test_an_unknown_scope_is_refused(
        self, client: TestClient, auth: dict[str, str], scope: str
    ) -> None:
        response = _put(client, auth, 2026, "WOMAN", "3", scope)
        assert response.status_code in (404, 422), response.text
        assert _scoped(client, 2026, "WOMAN") == {}

    def test_an_empty_scope_segment_is_the_short_path(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        """빈 기준(``.../WOMAN/``)은 **기준 없는 경로**와 같다.

        끝의 ``/`` 만 남은 주소는 FastAPI 가 기준 없는 경로로 넘깁니다. 즉
        기관 전체 구매금액 기준으로 저장됩니다 — 알 수 없는 기준으로 오해해
        거절하는 것보다, 원래 있던 경로와 같게 동작하는 편이 안전합니다.
        """
        assert _put(client, auth, 2026, "WOMAN", "3", "").status_code == 200
        assert _total(client, 2026, "WOMAN") == "3"

    @pytest.mark.parametrize("rate", ["0", "-1", "101", "abc"])
    def test_a_rate_outside_the_range_is_refused(
        self, client: TestClient, auth: dict[str, str], rate: str
    ) -> None:
        assert _put(client, auth, 2026, "WOMAN", rate, CONSTRUCTION).status_code == 422
        assert _scoped(client, 2026, "WOMAN") == {}

    def test_an_unknown_policy_is_refused(self, client: TestClient, auth: dict[str, str]) -> None:
        assert _put(client, auth, 2026, "NO_SUCH_POLICY", "3", CONSTRUCTION).status_code == 404

    def test_it_needs_the_admin_token(self, client: TestClient) -> None:
        response = client.put(
            f"/policy-targets/2026/WOMAN/{CONSTRUCTION}", json={"target_rate": "3"}
        )
        assert response.status_code == 401, response.text


class TestChangingTheTargetChangesTheAchievement:
    """고친 목표가 **화면 달성률에 실제로 반영**된다."""

    @pytest.fixture
    def seeded(self, db: Path) -> Path:
        company = CompanyRepository(db).insert(
            Company(business_no=_BUSINESS_NO, company_name="합성업체", representative_name="가나다")
        )
        assert company.company_id is not None
        policy = PolicyRepository(db).find_by_policy_code("STARTUP")
        assert policy is not None and policy.policy_id is not None
        CertificationRepository(db).insert(
            Certification(
                company_id=company.company_id,
                policy_id=policy.policy_id,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_BUSINESS_NO,
                company_name="합성업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("300"),
                company_id=company.company_id,
            )
        )
        PurchaseRepository(db).insert(
            Purchase(
                business_no="1000000028",
                company_name="다른업체",
                resolution_date=date(2026, 5, 1),
                amount=Decimal("9700"),
            )
        )
        return db

    def _achievement(self, client: TestClient) -> str | None:
        payload = client.get("/dashboard/summary", params={"year": 2026}).json()
        row = next(r for r in payload["policies"] if r["policy_code"] == "STARTUP")
        value = row["achievement_rate"]
        return None if value is None else str(value)

    def test_the_dashboard_follows_the_edited_target(
        self, seeded: Path, client: TestClient, auth: dict[str, str]
    ) -> None:
        # 실적률은 300 / 10,000 = 3.0% 로 고정. 목표만 바꾼다.
        _put(client, auth, 2026, "STARTUP", "3")
        assert Decimal(self._achievement(client) or "0") == Decimal("100")

        _put(client, auth, 2026, "STARTUP", "6")
        assert Decimal(self._achievement(client) or "0") == Decimal("50")

        # 해제하면 달성률을 만들지 않는다 — ⛔ 0% 로 떨어뜨리지 않는다.
        _put(client, auth, 2026, "STARTUP", None)
        assert self._achievement(client) is None
