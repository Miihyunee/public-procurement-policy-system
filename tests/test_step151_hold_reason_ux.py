"""
STEP 151 — 「계산 보류」가 무엇을 하라는 말인지 화면에서 알 수 있는가.

무엇이 문제였나
===============
계산이 멈추는 이유는 셋인데, 화면은 셋을 「계산 보류」 한 마디로만 말했다.
해야 할 일은 서로 다르다.

    구매유형 확인 중   확인을 끝내면 계산된다        → 구매실적 검토
    목표비율 미설정    목표를 넣으면 계산된다        → 목표비율 관리
    산출 기준 미정     지금 자료로는 산출할 수 없다  → 갈 곳이 없다

게다가 「할 일」 영역은 앞의 둘을 한 묶음으로 두고 **둘 다 목표비율 관리로**
보냈다. 여성기업은 목표비율(공사 3 · 용역 5 · 물품 5)이 이미 들어 있어서
그 화면에 가도 **할 일이 없었다.** 실제로 해야 할 일은 구매실적 검토에서
구매유형을 확정하는 것이었다(STEP 150 실사용 검증에서 발견).

무엇을 바꿨나
=============
멈춘 이유를 화면에서 셋으로 갈라, 각각 다른 문구·다른 다음 행동으로 보낸다.
남은 건수도 함께 적는다.

⛔ 계산은 건드리지 않았다. 확인이 덜 끝난 동안 «계산 보류» 로 두는 STEP 140
   규칙도, 분모·분자도, 목표율도, 자동판정 규칙도 그대로다.
⛔ 「몇 % 확정되면 계산」 같은 임계값을 만들지 않았다 — 고객 결정사항이다.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.web.policy_display import ON_HOLD, READY, get_display_info

#: ⛔ 실제 고객 자료가 아니다. 검증자릿수를 맞춘 합성 사업자번호.
WOMAN_NO = "1000000009"
OTHER_NO = "1000000014"

YEAR = 2026

PAGE = Path("src/procurement/web/static/index.html").read_text(encoding="utf-8")


# ======================================================================
# 화면 원본을 읽기 위한 도구
# ======================================================================
def _block(name: str) -> str:
    """``function <name>(`` 부터 그 함수의 닫는 중괄호까지."""
    start = PAGE.index("function " + name + "(")
    depth = 0
    opened = False
    for index in range(start, len(PAGE)):
        char = PAGE[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return PAGE[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


def _object(name: str) -> str:
    """``var <name> = { ... };`` 의 본문."""
    start = PAGE.index("var " + name + " = {")
    return PAGE[start : PAGE.index("};", start) + 2]


# ======================================================================
# 실제 API — 계산은 그대로인가
# ======================================================================
@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


#: ⛔ 실제 배포 토큰이 아니다. 설정 변경 API 를 열어 두기 위한 시험용 값.
ADMIN_TOKEN = "step151-test-token"


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, admin_token=ADMIN_TOKEN))


def _purchases(tmp_path: Path) -> str:
    book = Workbook()
    sheet = book.active
    sheet.append(
        [
            "결의일자",
            "계약일자",
            "지급일",
            "기업명",
            "사업자등록번호",
            "계",
            "신고기준일",
            "적요",
            "예산과목",
        ]
    )
    sheet.append(["2026-03-01", None, None, "합성1기업", WOMAN_NO, 1000000, "2026-03-01", "", ""])
    sheet.append(["2026-03-02", None, None, "합성2기업", OTHER_NO, 9000000, "2026-03-02", "", ""])
    path = tmp_path / "purchases.xlsx"
    book.save(path)
    return str(path)


def _listing(tmp_path: Path) -> str:
    book = Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    sheet.append([WOMAN_NO, "합성1기업", "", "2026-01-01", "2026-12-31"])
    path = tmp_path / "listing.xlsx"
    book.save(path)
    return str(path)


@pytest.fixture
def loaded(client: TestClient, db: Path, tmp_path: Path) -> TestClient:
    """구매 2건 · 인증 1건 · 목표비율 등록까지 마친 상태."""
    upload = client.post(
        "/uploads/purchases", json={"file_path": _purchases(tmp_path), "year": YEAR}
    )
    assert upload.status_code == 200, upload.text
    for policy_code in ("WOMAN", "SMALL_BUSINESS"):
        listing = client.post(
            "/companies/upload",
            json={"file_path": _listing(tmp_path), "policy_code": policy_code},
        )
        assert listing.status_code == 200, listing.text
    client.post("/purchases/rematch")
    main(["targets", "--year", str(YEAR), "--db", str(db)])
    return client


def _summary(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get("/dashboard/summary", params={"year": YEAR}).json()
    rows = body.get("items") or body.get("policies") or []
    return {str(row["policy_code"]): dict(row) for row in rows}


def _scoped(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """구매유형별 결과를 ``{분모 기준: 한 줄}`` 로."""
    return {row["scope"]: row for row in item["scoped_achievements"]}


def _ids(client: TestClient) -> dict[str, int]:
    body = client.get("/reviews", params={"page": 1, "page_size": 50}).json()
    return {item["source"]["business_no"]: item["source"]["purchase_id"] for item in body["items"]}


def _confirm(client: TestClient, purchase_id: int, purchase_type: str) -> None:
    response = client.put(
        f"/reviews/{purchase_id}",
        json={"final_purchase_type": purchase_type, "reviewed_by": "시험"},
    )
    assert response.status_code == 200, response.text


class TestTheCalculationDidNotChange:
    """§작업 8-1 · 8-5 — 계산은 STEP 140 그대로다."""

    def test_partial_confirmation_is_still_on_hold(self, loaded: TestClient) -> None:
        """확정이 덜 끝나면 유형별 상태는 여전히 ``CALCULATION_ON_HOLD`` 다."""
        _confirm(loaded, _ids(loaded)[WOMAN_NO], "CONSTRUCTION")

        woman = _summary(loaded)["WOMAN"]
        scoped = _scoped(woman)
        for scope in ("CONSTRUCTION", "SERVICE", "GOODS"):
            assert scoped[scope]["status"] == "CALCULATION_ON_HOLD", scope
            assert scoped[scope]["achievement_rate"] is None, scope

    def test_full_confirmation_calculates_as_before(self, loaded: TestClient) -> None:
        """확정이 다 끝나면 예전처럼 달성률이 나온다."""
        ids = _ids(loaded)
        _confirm(loaded, ids[WOMAN_NO], "CONSTRUCTION")
        _confirm(loaded, ids[OTHER_NO], "CONSTRUCTION")

        woman = _summary(loaded)["WOMAN"]
        scoped = _scoped(woman)
        construction = scoped["CONSTRUCTION"]
        assert construction["status"] != "CALCULATION_ON_HOLD"
        assert construction["achievement_rate"] is not None
        # 분모는 확정된 두 건 전부(10,000,000), 분자는 여성기업 한 건(1,000,000).
        assert Decimal(str(construction["total_purchase_amount"])) == Decimal("10000000")
        assert Decimal(str(construction["purchase_amount"])) == Decimal("1000000")

    def test_other_policies_are_untouched(self, loaded: TestClient) -> None:
        """§작업 8-9 — 유형별 목표가 없는 정책은 그대로 계산된다."""
        small = _summary(loaded)["SMALL_BUSINESS"]
        assert small["achievement_rate"] is not None
        assert small["scoped_achievements"] == []


class TestTheRemainingCountComesFromTheData:
    """§작업 3 — 남은 건수는 하드코딩이 아니라 서버 값의 차이다."""

    def test_the_coverage_counts_the_real_population(self, loaded: TestClient) -> None:
        woman = _summary(loaded)["WOMAN"]
        coverage = woman["purchase_type_coverage"]
        assert coverage is not None
        assert coverage["complete"] is False
        assert coverage["confirmed_count"] == 0
        assert coverage["total_count"] == 2

        _confirm(loaded, _ids(loaded)[WOMAN_NO], "CONSTRUCTION")

        coverage = _summary(loaded)["WOMAN"]["purchase_type_coverage"]
        assert coverage["confirmed_count"] == 1
        assert coverage["total_count"] == 2
        # 화면이 적는 「남은 건수」 = 전체 − 확인 완료.
        assert coverage["total_count"] - coverage["confirmed_count"] == 1

    def test_no_count_is_hardcoded_in_the_page(self) -> None:
        """⛔ 특정 현장의 건수를 화면에 박아 넣지 않는다."""
        for literal in ("3,288", "3288", "3,412", "3412"):
            assert literal not in PAGE, literal


class TestTheVillageIsNotAPurchaseTypeProblem:
    """§작업 4 · 8-7 — 자활용사촌은 여성기업과 원인이 다르다."""

    def test_the_village_has_no_purchase_type_coverage(self, loaded: TestClient) -> None:
        """확인 진행률 자체가 없다 → 화면이 「구매유형 확인 중」으로 갈 수 없다."""
        village = _summary(loaded).get("SELF_SUPPORT_VILLAGE")
        if village is None:
            pytest.skip("이 seed 에는 자활용사촌이 없다")
        assert village["purchase_type_coverage"] is None
        assert village["scoped_achievements"] == []

    def test_the_village_display_is_on_hold_but_woman_is_not(self) -> None:
        """표시 정보에서도 둘은 갈라져 있다."""
        assert get_display_info("SELF_SUPPORT_VILLAGE").development_status == ON_HOLD
        assert get_display_info("WOMAN").development_status == READY


class TestTheExistingWritePathsStillWork:
    """§작업 8-10 · 8-11 — 목표비율과 검토 기능이 깨지지 않았다."""

    def test_target_rate_save_and_read(self, loaded: TestClient) -> None:
        saved = loaded.put(
            f"/policy-targets/{YEAR}/SMALL_BUSINESS/TOTAL",
            json={"target_rate": "50"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert saved.status_code == 200, saved.text
        rows = loaded.get("/policy-targets", params={"year": YEAR}).json()["items"]
        row = next(row for row in rows if row["policy_code"] == "SMALL_BUSINESS")
        assert Decimal(str(row["target_rate"])) == Decimal("50")

    def test_review_confirm_and_reopen(self, loaded: TestClient) -> None:
        purchase_id = _ids(loaded)[WOMAN_NO]
        _confirm(loaded, purchase_id, "SERVICE")
        assert _summary(loaded)["WOMAN"]["purchase_type_coverage"]["confirmed_count"] == 1

        reopened = loaded.post(
            f"/reviews/{purchase_id}/reopen",
            json={"reopened_by": "시험", "note": "되돌림"},
        )
        assert reopened.status_code == 200, reopened.text
        # ⛔ 되돌려도 담당자가 골랐던 유형은 지우지 않는다(기존 동작).
        assert reopened.json()["review"]["status"] == "REOPENED"
        assert reopened.json()["review"]["final_purchase_type"] == "SERVICE"


# ======================================================================
# 화면 — 무엇이라고 적고 어디로 보내는가
# ======================================================================
class TestThePageTellsTheThreeCausesApart:
    """§작업 1 — 원인마다 다른 문구."""

    def test_each_cause_has_its_own_label(self) -> None:
        assert 'label: "구매유형 확인 중"' in _object("HOLD_REVIEW")
        assert 'label: "목표비율 미설정"' in _object("HOLD_TARGET")
        assert 'label: "산출 기준 미정"' in _object("HOLD_BASIS")

    def test_the_label_is_what_the_chip_shows(self) -> None:
        """상태 알약이 「계산 보류」로 굳어 있지 않다."""
        assert 'stateChip("HOLD", hold.label)' in _block("achievementRows")
        assert "text(state, hold.label)" in _block("policyCard")

    def test_the_cause_is_read_from_the_server_answer(self) -> None:
        """⛔ 상태를 새로 판정하지 않는다."""
        body = _block("holdCause")
        assert "pendingTypeCount(item)" in body
        assert 'item.status === "CALCULATION_ON_HOLD"' in body
        assert 'item.status === "TARGET_RATE_NOT_SET"' in body

    def test_no_internal_state_name_is_shown_to_the_user(self) -> None:
        """§작업 7 — 화면 문구에 내부 용어를 쓰지 않는다."""
        for label in ('label: "', 'what: "', 'how: "'):
            for name in ("CALCULATION_ON_HOLD", "Coverage", "분모"):
                assert label + name not in PAGE, label + name
        # ⛔ 포괄 문구로 다시 묶지 않는다. 주석에는 남아 있어도 되지만
        #    **화면 문자열**로는 없어야 하므로 따옴표까지 함께 본다.
        assert '"계산 조건이 갖춰지지 않았습니다"' not in PAGE


class TestTheNextStepGoesWhereTheWorkIs:
    """§작업 2 · 5 — 원인마다 다른 곳으로 보낸다."""

    def test_every_cause_has_its_own_line_in_the_todo_list(self) -> None:
        """⛔ 셋 중 하나라도 빠지면 그 정책은 「할 일」에서 사라진다.

        빠진 이유는 아무 줄에도 걸리지 않는다 — 앞의 두 묶음은 «멈추지 않은»
        정책만 세기 때문이다. 화면에서 조용히 없어지는 쪽이 잘못 안내하는
        것보다 낫다고 볼 수 없다.
        """
        groups = PAGE[PAGE.index("var TODO_GROUPS = [") : PAGE.index("function renderAttention(")]
        for cause in ("HOLD_REVIEW", "HOLD_TARGET", "HOLD_BASIS"):
            assert f"cause: {cause}" in groups, cause
            assert f"key: {cause}.key" in groups, cause

    def test_purchase_type_goes_to_the_review_card(self) -> None:
        review = _object("HOLD_REVIEW")
        assert 'go: "구매실적 검토"' in review
        assert 'tab: "data"' in review
        assert 'focus: "review-card"' in review

    def test_the_review_card_actually_exists(self) -> None:
        assert 'id="review-card"' in PAGE
        assert 'id="panel-data"' in PAGE

    def test_a_missing_target_still_goes_to_target_management(self) -> None:
        """§작업 8-6 — 목표율 미설정은 예전 그대로."""
        target = _object("HOLD_TARGET")
        assert 'go: "목표비율 관리"' in target
        assert 'focus: "target-card"' in target

    def test_the_village_is_not_sent_anywhere(self) -> None:
        """§작업 8-8 — 갈 곳이 없으면 단추를 만들지 않는다."""
        basis = _object("HOLD_BASIS")
        assert "go: null" in basis
        assert "tab: null" in basis
        assert "if (destination.go) {" in _block("renderAttention")

    def test_the_other_two_destinations_did_not_move(self) -> None:
        """§작업 8-9 — 부족·기업정보 미등록의 동선은 그대로다."""
        groups = PAGE[PAGE.index("var TODO_GROUPS = [") : PAGE.index("function renderAttention(")]
        assert 'focus: "policy-detail-card"' in groups
        assert 'focus: "company-card"' in groups


class TestTheRemainingCountIsOnTheScreen:
    """§작업 3 — 남은 건수를 화면에서 바로 읽을 수 있다."""

    def test_the_helper_subtracts_the_two_server_numbers(self) -> None:
        body = _block("pendingTypeCount")
        assert "cov.total_count - cov.confirmed_count" in body
        assert "cov.complete" in body

    def test_the_todo_line_says_how_many_are_left(self) -> None:
        body = _block("renderAttention")
        assert "pendingTypeCount(item)" in body
        assert "건의 구매유형 확인이 남아 있습니다." in body

    def test_the_card_note_says_it_too(self) -> None:
        body = _block("coverageNote")
        assert "coverage.total_count - coverage.confirmed_count" in body
        assert "건의 구매유형 확인이 남아 있습니다." in body


class TestNoThresholdWasInvented:
    """§작업 8 · 절대규칙 5 — 임계값을 만들지 않았다."""

    def test_the_cause_never_compares_a_ratio(self) -> None:
        body = _block("holdCause") + _block("pendingTypeCount")
        assert "%" not in body
        assert not re.search(r"0\.\d|\b(?:80|90|95)\b", body)

    def test_completeness_is_the_only_gate(self) -> None:
        """확인이 «끝났는가» 만 묻는다 — 얼마나 끝났는지로 갈리지 않는다."""
        assert "cov.complete" in _block("pendingTypeCount")
