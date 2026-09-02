"""
Dashboard 화면 및 화면용 API 테스트.

``GET /`` (HTML), ``GET /dashboard/data-status``,
``GET /dashboard/policy-display`` 를 검증합니다. 기존 엔드포인트
(``/dashboard/summary`` · ``/policies``)의 동작이 바뀌지 않았는지도 함께
확인합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.api.status_response import PERIOD_NOTICE_AVAILABLE, PERIOD_NOTICE_UNAVAILABLE
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase
from procurement.web.policy_display import ON_HOLD, POLICY_DISPLAY, READY, get_display_info

#: 정본 정책 코드 개수(bootstrap seed 기준).
SEED_POLICY_COUNT = 5


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "dashboard_page.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path))


class TestDashboardPage:
    """``GET /`` — 브라우저 화면."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200

    def test_content_type_is_html(self, client: TestClient) -> None:
        assert client.get("/").headers["content-type"].startswith("text/html")

    def test_contains_year_selector(self, client: TestClient) -> None:
        assert 'id="year-select"' in client.get("/").text

    def test_contains_chart_containers(self, client: TestClient) -> None:
        """최소 2개 시각화 영역이 존재한다."""
        body = client.get("/").text
        assert 'id="chart-achievement"' in body
        assert 'id="chart-volume"' in body
        assert 'id="chart-gauge"' in body

    def test_surfaces_server_detail_on_error(self, client: TestClient) -> None:
        """503 등의 상태를 'HTTP 503' 이 아니라 서버가 준 사유로 표시한다."""
        assert "body.detail" in client.get("/").text

    def test_contains_theme_toggle(self, client: TestClient) -> None:
        """라이트/다크 전환 버튼이 존재한다."""
        assert 'id="theme-toggle"' in client.get("/").text

    def test_defines_both_themes(self, client: TestClient) -> None:
        """두 테마의 색 토큰이 모두 정의되어 있다."""
        body = client.get("/").text
        assert ':root[data-theme="dark"]' in body
        assert ':root[data-theme="light"]' in body

    def test_contains_upload_status_area(self, client: TestClient) -> None:
        assert 'id="status-table"' in client.get("/").text

    def test_does_not_load_external_resources(self, client: TestClient) -> None:
        """CDN·외부 라이브러리를 사용하지 않는다(오프라인에서도 동작)."""
        body = client.get("/").text
        assert "http://" not in body.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in body

    def test_page_is_not_in_openapi_schema(self, client: TestClient) -> None:
        assert "/" not in client.get("/openapi.json").json()["paths"]


class TestDataStatusEndpoint:
    """``GET /dashboard/data-status``."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").status_code == 200

    def test_empty_db_reports_zero(self, client: TestClient) -> None:
        body = client.get("/dashboard/data-status").json()
        assert body["purchase_count"] == 0
        assert body["purchase_total_amount"] == "0"
        assert body["earliest_payment_date"] is None

    def test_policy_count_matches_seed(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").json()["policy_count"] == SEED_POLICY_COUNT

    def test_amount_is_serialized_as_string(self, client: TestClient, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2026, 1, 1),
                payment_date=date(2026, 1, 31),
                amount=Decimal("1234.56"),
            )
        )
        body = client.get("/dashboard/data-status").json()
        assert body["purchase_total_amount"] == "1234.56"

    def test_period_filter_is_never_applied(self, client: TestClient) -> None:
        """적재 현황 자체는 항상 전체 데이터 기준이다."""
        body = client.get("/dashboard/data-status?year=2026").json()
        assert body["period_filter_applied"] is False

    def test_the_default_basis_is_the_resolution_date(self, client: TestClient) -> None:
        """🟢 기본 설정에서는 **결의일자 기준**으로 기간 조회가 가능하다.

        .. note::
            **바뀐 이유** — 이 시험은 STEP 85 까지
            ``test_notice_says_unavailable_when_date_field_unset`` 이라는
            이름으로 *"기준일이 미설정이라 사용 불가"* 를 확인하고 있었습니다.
            2026-09-02 PM 확정(STEP 86)으로 연도 귀속 기준일이 **결의일자**로
            고정되어 기본값이 생겼으므로, 기본 상태의 기대값이 뒤집혔습니다.
            ⛔ "사용 불가" 확인은 지우지 않고 아래 시험으로 남겼습니다.
        """
        body = client.get("/dashboard/data-status").json()
        assert body["period_filter_available"] is True
        assert body["period_date_field"] == "resolution_date"
        assert body["period_notice"] == PERIOD_NOTICE_AVAILABLE

    def test_notice_says_unavailable_when_date_field_unset(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """설정을 **명시적으로 비우면** 사용 불가로 안내한다(원래 확인 그대로)."""
        from procurement.core.config import settings

        monkeypatch.setattr(settings, "PURCHASE_PERIOD_DATE_FIELD", None)
        body = TestClient(create_app(db_path)).get("/dashboard/data-status").json()
        assert body["period_filter_available"] is False
        assert body["period_date_field"] is None
        assert body["period_notice"] == PERIOD_NOTICE_UNAVAILABLE

    def test_year_is_echoed_back(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=2026").json()["requested_year"] == 2026

    def test_year_is_null_when_omitted(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").json()["requested_year"] is None

    def test_out_of_range_year_is_rejected(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=12").status_code == 422

    def test_non_numeric_year_is_rejected(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=abc").status_code == 422

    def test_data_mode_defaults_to_demo(self, db_path: Path) -> None:
        client = TestClient(create_app(db_path, data_mode="demo"))
        assert client.get("/dashboard/data-status").json()["data_mode"] == "demo"

    def test_data_mode_can_be_operational(self, db_path: Path) -> None:
        client = TestClient(create_app(db_path, data_mode="operational"))
        assert client.get("/dashboard/data-status").json()["data_mode"] == "operational"

    def test_year_does_not_change_counts(self, client: TestClient, db_path: Path) -> None:
        """기간 필터가 없으므로 연도를 바꿔도 결과가 같아야 한다(현재 단계 사실)."""
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2020, 1, 1),
                payment_date=date(2020, 1, 31),
                amount=Decimal("100"),
            )
        )
        a = client.get("/dashboard/data-status?year=2026").json()["purchase_count"]
        b = client.get("/dashboard/data-status?year=2020").json()["purchase_count"]
        assert a == b == 1


def _display_map(client: TestClient) -> dict[str, dict[str, str]]:
    """정책 코드 → 표시 정보 매핑으로 응답을 변환합니다."""
    items: list[dict[str, str]] = client.get("/dashboard/policy-display").json()["items"]
    return {item["policy_code"]: item for item in items}


class TestPolicyDisplayEndpoint:
    """``GET /dashboard/policy-display``."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/dashboard/policy-display").status_code == 200

    def test_covers_all_seed_policy_codes(self, client: TestClient) -> None:
        items = client.get("/dashboard/policy-display").json()["items"]
        codes = {item["policy_code"] for item in items}
        seed_codes = {
            policy["policy_code"] for policy in client.get("/policies").json()["policies"]
        }
        assert seed_codes <= codes

    def test_green_is_on_hold(self, client: TestClient) -> None:
        """D-3 — 녹색제품은 유지하되 계산 보류."""
        items = _display_map(client)
        assert items["GREEN"]["development_status"] == ON_HOLD

    def test_woman_is_on_hold(self, client: TestClient) -> None:
        """D-1 — 여성기업은 개발 중단(D-2 선행)."""
        items = _display_map(client)
        assert items["WOMAN"]["development_status"] == ON_HOLD

    def test_on_hold_policies_have_a_reason(self, client: TestClient) -> None:
        for item in client.get("/dashboard/policy-display").json()["items"]:
            if item["development_status"] == ON_HOLD:
                assert item["note"]

    def test_small_business_is_ready(self, client: TestClient) -> None:
        items = _display_map(client)
        assert items["SMALL_BUSINESS"]["development_status"] == READY


class TestPolicyDisplayMap:
    """표시 정보 매핑 자체."""

    def test_unknown_code_falls_back(self) -> None:
        info = get_display_info("NO_SUCH_POLICY")
        assert info.development_status == "UNKNOWN"

    def test_every_entry_has_a_label(self) -> None:
        for info in POLICY_DISPLAY.values():
            assert info.development_label


class TestExistingEndpointsUnchanged:
    """기존 API 가 그대로 동작한다."""

    def test_summary_requires_year(self, client: TestClient) -> None:
        """연도 미지정은 400 (D-27) — 전 기간을 임의로 합산하지 않는다."""
        assert client.get("/dashboard/summary").status_code == 400

    def test_summary_shape_unchanged(self, db_path: Path) -> None:
        """연도를 주면 응답 구조는 기존과 동일하다.

        .. note::
            변경 사유(STEP 59): 결의일자 공란 알림 필드가 추가되었습니다.
            비교를 느슨하게 하지 않고 **새 필드를 기대 집합에 함께 적습니다.**
            지급일 기준 조회이므로 이 필드는 "해당 없음" 으로 나갑니다.
        """
        client = TestClient(create_app(db_path, period_date_field="payment_date"))
        body = client.get("/dashboard/summary?year=2026").json()
        assert set(body) == {"total_purchase_amount", "policies", "missing_resolution_date"}
        assert body["missing_resolution_date"]["applies"] is False

    def test_summary_without_year_explains_why(self, client: TestClient) -> None:
        """연도 미지정 400 응답은 **사유를 본문에 담는다**(D-27).

        .. note::
            이 테스트는 원래 ``assert "requested_year" not in body`` 였습니다.
            D-27 도입 후 연도 없는 호출이 400 을 반환하면서 본문이
            ``{"detail": ...}`` 가 되었고, 그 결과 단언이 **항상 참**이 되어
            아무것도 검증하지 못했습니다. 통과 여부만 같을 뿐 의미가 없었으므로
            실제 의도(연도를 요구하며 그 이유를 알려준다)를 검증하도록 바꿉니다.

            계산 결과 기대값은 건드리지 않았습니다.
        """
        response = client.get("/dashboard/summary")

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "year" in detail
        assert "D-27" in detail

    def test_policies_still_returns_200(self, client: TestClient) -> None:
        assert client.get("/policies").status_code == 200

    def test_target_rate_write_is_still_protected(self, client: TestClient) -> None:
        """관리자 토큰 미설정 앱에서는 쓰기 API 가 503 이다(D-14)."""
        response = client.put("/policies/SMALL_BUSINESS/target-rate", json={"target_rate": "50"})
        assert response.status_code == 503


class TestThemeRegression:
    """테마 전환 시 차트 색상이 따라오는지 정적으로 검증합니다.

    **배경**: SVG 차트는 그리는 시점에 ``getComputedStyle`` 로 읽은 색을 속성값으로
    **구워 넣습니다.** 따라서 ``data-theme`` 만 바꾸고 다시 그리지 않으면, CSS 로
    칠해지는 카드·배경만 밝아지고 **차트는 이전 테마 색을 유지**합니다. 그 상태에서는
    라이트 모드에서 막대 위 숫자와 게이지 값이 배경에 묻혀 읽히지 않습니다.

    실제 토글 버튼은 ``redraw()`` 를 호출하므로 현재는 정상 동작합니다. 이 테스트는
    그 호출이 나중에 빠지는 것을 막습니다. 브라우저 없이 확인할 수 있도록 화면
    스크립트의 구조만 검사합니다.
    """

    @staticmethod
    def _script() -> str:
        from procurement.web.page import read_index_html

        return read_index_html()

    def test_theme_toggle_redraws_charts(self) -> None:
        """테마를 바꾸면 반드시 다시 그린다.

        ``applyTheme(next)`` 뒤에 ``redraw()`` 가 없으면 차트가 이전 테마 색으로
        남습니다.
        """
        html = self._script()
        start = html.index('el("theme-toggle").addEventListener')
        handler = html[start : start + 500]

        assert "applyTheme(next)" in handler
        assert "redraw()" in handler, (
            "테마 토글이 redraw() 를 호출하지 않으면 차트가 이전 테마 색으로 남습니다."
        )

    def test_charts_read_colors_from_theme_variables(self) -> None:
        """차트 색을 하드코딩하지 않고 테마 변수에서 읽는다."""
        html = self._script()

        for token in ("--text", "--muted", "--track", "--accent"):
            assert 'cssVar("' + token + '")' in html, f"{token} 을 테마 변수로 읽지 않습니다."

    def test_both_themes_define_every_chart_color(self) -> None:
        """라이트·다크 두 테마 모두 차트가 쓰는 색 토큰을 정의한다.

        한쪽에만 정의하면 다른 테마에서 빈 문자열이 되어 SVG 가 기본 검정으로
        칠해집니다.
        """
        html = self._script()
        dark = html[html.index(':root[data-theme="dark"]') :]
        dark = dark[: dark.index("}")]
        light = html[html.index(':root[data-theme="light"]') :]
        light = light[: light.index("}")]

        for token in ("--text", "--muted", "--track", "--accent"):
            assert token in dark, f"다크 테마에 {token} 정의가 없습니다."
            assert token in light, f"라이트 테마에 {token} 정의가 없습니다."

    def test_theme_choice_is_persisted(self) -> None:
        """선택한 테마를 저장해 다음 방문에 유지한다."""
        html = self._script()
        assert "localStorage.setItem(THEME_KEY" in html
        assert "localStorage.getItem(THEME_KEY" in html
