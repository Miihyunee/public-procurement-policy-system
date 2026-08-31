"""
STEP 73 — 운영 검수를 **실제로 한 번 돌린 기록**을 지킵니다.

두 가지를 봅니다.

1. `docs/OPERATIONS_CHECK_RESULT.md` 가 A~L 전 단계의 결과를 담고 있고,
   미확정 업무규칙을 **확정처럼 적지 않았는지**.
2. 검수에서 **실제로 발견되어 고친 것** — 사업자등록번호를 인쇄된 형태
   (`123-45-67890`)로 검색해도 찾히는지.

.. warning::
    ⛔ 2번은 **업무규칙이 아닙니다.** 무엇을 실적에 넣고 빼는지는 그대로이며,
    "종이에 적힌 대로 넣어도 같은 거래를 찾는다" 는 조회 동작만 잠급니다.

.. note::
    합성 데이터만 씁니다. 문서에 적힌 숫자도 합성 24행에서 나온 것이며 실제
    고객 실적이 아닙니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.period import PAYMENT_DATE
from procurement.database.bootstrap import bootstrap
from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers.business_no import business_no_search_key, normalize_business_no
from procurement.models import Purchase

_RESULT = Path(__file__).resolve().parents[1] / "docs" / "OPERATIONS_CHECK_RESULT.md"

#: 합성 사업자등록번호 — 인쇄 표기와 저장 표기.
_PRINTED = "119-81-02316"
_STORED = "1198102316"

_DAY = date(2026, 3, 1)


@pytest.fixture(scope="module")
def text() -> str:
    return _RESULT.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """``heading`` 으로 시작하는 절의 본문. 없으면 빈 문자열."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(heading):
            level = len(line) - len(line.lstrip("#"))
            rest = lines[index + 1 :]
            for offset, following in enumerate(rest):
                stripped = following.lstrip("#")
                if following.startswith("#") and len(following) - len(stripped) <= level:
                    return "\n".join(rest[:offset])
            return "\n".join(rest)
    return ""


# ======================================================================
# 1. 검수 기록 문서
# ======================================================================
class TestTheResultDocument:
    """검수를 했다는 기록이 남아 있는가."""

    def test_the_result_exists(self) -> None:
        assert _RESULT.exists()

    def test_it_says_the_data_was_synthetic(self, text: str) -> None:
        """⛔ 합성 숫자를 고객 실적처럼 남기지 않는다."""
        assert "합성 데이터" in text
        assert "고객 실적이 아니" in text

    @pytest.mark.parametrize(
        "stage",
        [
            "| A |",
            "| B |",
            "| C |",
            "| D |",
            "| E |",
            "| F |",
            "| G |",
            "| H |",
            "| H.5 |",
            "| I |",
            "| J |",
            "| K |",
            "| L |",
        ],
    )
    def test_every_stage_has_a_row(self, text: str, stage: str) -> None:
        section = _section(text, "## 2. 단계별 결과")
        assert section
        assert stage in section

    @pytest.mark.parametrize(
        "status", ["PASS", "PASS_WITH_MANUAL_CHECK", "BLOCKED", "NOT_APPLICABLE"]
    )
    def test_all_four_statuses_are_defined(self, text: str, status: str) -> None:
        assert status in _section(text, "## 1. 최종 결과")

    def test_the_verdict_is_recorded(self, text: str) -> None:
        section = _section(text, "## 7. 최종 판정")
        assert "운영 검수 가능" in section
        assert "고객 확인 후 재검수 필요" in section

    def test_the_manual_checks_name_the_documents(self, text: str) -> None:
        section = _section(text, "## 3. 수동 확인이 필요한 항목")
        for document in ("사업부서 품의서", "지출결의서", "세금계산서"):
            assert document in section

    def test_the_numerator_check_is_recorded(self, text: str) -> None:
        """⭐ 분모만 줄어드는 동작이 없었다는 것이 기록되어야 한다."""
        assert "분모에서만 빼는 동작은 없었다" in text
        assert "분모·분자 모두 감소" in text

    def test_the_reupload_number_is_recorded(self, text: str) -> None:
        assert "9,000" in text
        assert "중복 합산 없음" in text


class TestUnconfirmedRulesStayUnconfirmed:
    """⛔ 검수했다는 이유로 미확정 항목이 확정되면 안 된다."""

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8", "Q5-9", "W-11 ~ W-15", "구매유형 자동분류"])
    def test_each_is_still_listed_as_open(self, text: str, item: str) -> None:
        assert item in _section(text, "## 6. 미확정 사항")

    @pytest.mark.parametrize(
        "phrase",
        [
            "0원은 실적 제외",
            "음수는 실적 제외",
            "0원·음수 행은 실적에서 제외",
            "예산과목 공란은 제외",
            "구매유형 자동 확정",
            # ⚠️ "W-1-2 확정 전까지는" 처럼 **열려 있다**는 뜻으로 쓰는 문장은
            #    정상이므로, 확정했다고 선언하는 형태만 막는다.
            "W-1-2 확정됨",
            "W-1-2 를 확정",
            "인증 유효기간 판정 기준일은 결의일자",
        ],
    )
    def test_no_open_rule_was_settled(self, text: str, phrase: str) -> None:
        assert phrase not in text

    def test_the_rejected_rows_keep_the_neutral_wording(self, text: str) -> None:
        """0원·음수 행은 **미적재**로만 기록되었다."""
        assert "미적재" in text
        assert '"무효" · "삭제" · "실적 제외" · "부적합" 으로 판정하지 않았다' in text

    def test_the_injected_date_field_is_not_a_decision(self, text: str) -> None:
        """지급일 주입은 시험을 돌리기 위한 것이다 — W-1-2 확정이 아니다."""
        assert "확정한 것이 아니다" in text
        assert "D-24" in text

    def test_improvement_ideas_are_not_customer_requirements(self, text: str) -> None:
        section = _section(text, "### ③ 개선 제안")
        assert section
        assert "고객이 요청한 적이 없다" in section


class TestConfirmedRulesAreRecordedExactly:
    """고객 확정사항이 검수 기록에서도 흐려지지 않았는가."""

    def test_the_vehicle_rule_has_no_day_threshold(self, text: str) -> None:
        assert "기간 임계값 규칙이 없다는 것이 실제로 확인되었다" in text
        assert "품의서" in text

    def test_the_budget_rule_ignores_the_description(self, text: str) -> None:
        assert "적요가 일반 구매여도 제외" in text
        assert "적요가 무관해도 제외" in text

    def test_similar_accounts_survived(self, text: str) -> None:
        assert "교육훈련비지원" in text
        assert "특별교육훈련비" in text

    def test_words_alone_excluded_nothing(self, text: str) -> None:
        assert "낱말만으로는 안 빠짐" in text

    def test_the_named_rows_were_not_swept(self, text: str) -> None:
        assert "적요만으로 일괄 제외 안 함" in text


# ======================================================================
# 2. 검수에서 고친 것 — 인쇄된 표기로도 찾힌다
# ======================================================================
@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "check_result.db"
    bootstrap(path)
    repository = PurchaseRepository(path)
    for description, amount in (("사무용품 구매", "1000"), ("청소 용역", "2000")):
        repository.insert(
            Purchase(
                business_no=_STORED,
                company_name="합성기업 나",
                contract_date=_DAY,
                payment_date=_DAY,
                resolution_date=_DAY,
                description=description,
                amount=Decimal(amount),
            )
        )
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, period_date_field=PAYMENT_DATE))


class TestTheSearchKeyHelper:
    """구분자만 지우는 **비교용** 키."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("119-81-02316", "1198102316"),
            ("119 81 02316", "1198102316"),
            ("1198102316", "1198102316"),
            ("119-81", "11981"),  # 앞 몇 자리만 넣는 검색도 흔하다
            (None, ""),
            ("합성기업", "합성기업"),
        ],
    )
    def test_separators_are_removed(self, value: object, expected: str) -> None:
        assert business_no_search_key(value) == expected

    def test_it_is_not_the_join_key_normalizer(self) -> None:
        """⛔ 결합키 정규화와 **다르다** — 자릿수·체크섬을 따지지 않는다.

        검색은 앞 몇 자리만 넣는 일이 흔하므로 여기서 10자리를 요구하면 아무것도
        찾지 못한다. 반대로 이 키를 저장·매칭에 쓰면 잘못된 기업과 연결된다.
        """
        assert business_no_search_key("119-81") == "11981"
        assert normalize_business_no("119-81").value is None


class TestPrintedBusinessNumberFindsTheRows:
    """⭐ 종이(지출결의서·세금계산서)에 적힌 그대로 넣어도 같은 거래가 나온다."""

    def _ids(self, client: TestClient, search: str) -> list[int]:
        body: Any = client.get(f"/reviews?page=1&page_size=50&search={search}").json()
        return [item["source"]["purchase_id"] for item in body["items"]]

    def test_the_printed_form_matches(self, client: TestClient) -> None:
        assert len(self._ids(client, _PRINTED)) == 2

    def test_it_finds_the_same_rows_as_the_stored_form(self, client: TestClient) -> None:
        """⛔ 표기가 달라도 **같은 집합**이어야 한다 — 하나라도 다르면 오해가 된다."""
        assert self._ids(client, _PRINTED) == self._ids(client, _STORED)

    def test_a_partial_printed_form_still_matches(self, client: TestClient) -> None:
        assert len(self._ids(client, "119-81")) == 2

    def test_a_different_number_still_finds_nothing(self, client: TestClient) -> None:
        """⛔ 넓히기만 한 것이 아니다 — 다른 번호는 여전히 안 나온다."""
        assert self._ids(client, "220-81-62517") == []

    def test_description_and_company_search_are_unchanged(self, client: TestClient) -> None:
        assert len(self._ids(client, "사무용품")) == 1
        assert len(self._ids(client, "합성기업 나")) == 2

    def test_the_unmatched_screen_agrees(self, client: TestClient) -> None:
        """미매칭 기업 조회도 같은 표기 문제를 겪고 있었다."""
        printed: Any = client.get(f"/dashboard/unmatched-companies?search={_PRINTED}").json()
        stored: Any = client.get(f"/dashboard/unmatched-companies?search={_STORED}").json()
        assert [item["business_no"] for item in printed["items"]] == [_STORED]
        assert printed["items"] == stored["items"]

    def test_the_unmatched_screen_still_rejects_other_numbers(self, client: TestClient) -> None:
        other: Any = client.get("/dashboard/unmatched-companies?search=220-81-62517").json()
        assert other["items"] == []
