"""
tests.test_resolution_date_rule

일반 3개 정책(중소기업 · 여성기업 · 장애인기업)의 인증 유효기간 판정 기준일 —
**2026-08-31 고객 최종 회신**(``DECISIONS.md`` §0.12.1 · §0.12.2).

    중소기업 — 결의일자 / 여성기업 — 결의일자 / 장애인기업 — 결의일자

    인증서에 유효기간이 적혀 있고, 그 기간 안에 결의일자가 포함되어 있으면 돼.

STEP 84 에서 구현했습니다. 그전까지 이 세 정책은 ``PAYMENT_DATE``(지급일)를
보고 있었으며, 그것은 **고객이 확정한 규칙이 아니라 당시 동작**이었습니다.

무엇을 지키는가
===============

1. 결의일자가 유효기간 **안**이면 인정, **밖**이면 불인정 (경계 포함).
2. **지급일·계약일은 판정에 섞이지 않는다.**
3. 결의일자가 **공란**이면 다른 날짜로 대체하지 않는다 (🟢 W-15 · §0.12.8).
4. ⛔ **창업기업 규칙은 그대로다** — 결의일자 **또는** 계약일자(§0.6.2).

.. note::
    합성 데이터만 씁니다. 실제 사업자등록번호·거래처명을 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from procurement.calculators.rules import (
    RESOLUTION_DATE,
    RESOLUTION_OR_CONTRACT_DATE,
    PaymentDateRule,
    ResolutionDateRule,
    ResolutionOrContractDateRule,
    RuleContext,
    build_default_registry,
)
from procurement.database.bootstrap import (
    _UPDATED_EVALUATION_BASIS,
    MVP_POLICY_SEEDS,
)
from procurement.database.policy_repository import ALLOWED_EVALUATION_BASIS
from procurement.models import Purchase

#: 인증 유효기간.
VALID_FROM = date(2026, 1, 1)
VALID_TO = date(2026, 12, 31)
VALID = [(VALID_FROM, VALID_TO)]

#: ⛔ 판정에 쓰이면 안 되는 날짜들. 유효기간 **밖**에 두어, 섞이는 순간
#: 결과가 달라지게 한다.
OUTSIDE = date(2027, 5, 5)


def _purchase(resolution: date | None) -> Purchase:
    """결의일자만 다른 합성 구매 한 건."""
    return Purchase(
        business_no="1000000001",
        company_name="합성기업 가",
        contract_date=OUTSIDE,
        payment_date=OUTSIDE,
        resolution_date=resolution,
        amount=Decimal("100000"),
    )


def _matches(resolution: date | None) -> bool:
    context = RuleContext(purchase=_purchase(resolution), validity_ranges=VALID)
    return ResolutionDateRule().matches(context)


class TestTheResolutionDateDecides:
    """결의일자가 유효기간 안인가 밖인가."""

    @pytest.mark.parametrize(
        "day",
        [VALID_FROM, date(2026, 6, 15), VALID_TO],
    )
    def test_inside_the_validity_period_is_accepted(self, day: date) -> None:
        """시작일 · 중간 · 종료일 — 모두 인정(경계 포함)."""
        assert _matches(day) is True

    @pytest.mark.parametrize(
        "day",
        [date(2025, 12, 31), date(2027, 1, 1)],
    )
    def test_outside_the_validity_period_is_rejected(self, day: date) -> None:
        """시작일 하루 앞 · 종료일 하루 뒤 — 불인정."""
        assert _matches(day) is False

    def test_the_payment_and_contract_dates_do_not_decide(self) -> None:
        """⭐ 지급일·계약일이 유효기간 **안**이어도 판정을 뒤집지 못한다."""
        purchase = Purchase(
            business_no="1000000001",
            company_name="합성기업 가",
            contract_date=VALID_FROM,  # 안
            payment_date=VALID_TO,  # 안
            resolution_date=OUTSIDE,  # 밖
            amount=Decimal("100000"),
        )
        context = RuleContext(purchase=purchase, validity_ranges=VALID)
        assert ResolutionDateRule().matches(context) is False
        # 대조 — 지급일 규칙이었다면 인정되었을 건이다.
        assert PaymentDateRule().matches(context) is True

    def test_several_validity_ranges_are_or_conditions(self) -> None:
        """인증이 여러 건이면 **하나라도** 들면 인정한다."""
        context = RuleContext(
            purchase=_purchase(date(2028, 3, 1)),
            validity_ranges=[VALID[0], (date(2028, 1, 1), date(2028, 12, 31))],
        )
        assert ResolutionDateRule().matches(context) is True


class TestABlankResolutionDate:
    """🟢 W-15 — *"원본 데이터는 보존하고 별도 확인 대상으로 처리"*."""

    def test_a_blank_resolution_date_is_not_accepted(self) -> None:
        """⛔ 값이 없으면 인정하지 않는다."""
        assert _matches(None) is False

    def test_a_blank_resolution_date_is_not_substituted(self) -> None:
        """⭐ ⛔ **다른 날짜로 대신하지 않는다.**

        지급일·계약일이 유효기간 **안**이어도 결의일자가 없으면 인정하지
        않습니다. 대체하면 담당자가 확인하지 않은 판정이 실적 숫자로 굳습니다.
        """
        purchase = Purchase(
            business_no="1000000001",
            company_name="합성기업 가",
            contract_date=VALID_FROM,
            payment_date=VALID_TO,
            resolution_date=None,
            amount=Decimal("100000"),
        )
        context = RuleContext(purchase=purchase, validity_ranges=VALID)
        assert ResolutionDateRule().matches(context) is False

    def test_the_row_is_not_touched(self) -> None:
        """⛔ 규칙은 **판정만** 한다 — 구매 행의 값을 바꾸지 않는다."""
        purchase = _purchase(None)
        ResolutionDateRule().matches(RuleContext(purchase=purchase, validity_ranges=VALID))
        assert purchase.resolution_date is None
        assert purchase.payment_date == OUTSIDE
        assert purchase.contract_date == OUTSIDE


class TestWiring:
    """규칙이 실제로 일반 3개 정책에 연결되어 있는가."""

    def test_the_registry_resolves_the_new_basis(self) -> None:
        assert isinstance(build_default_registry().get(RESOLUTION_DATE), ResolutionDateRule)

    def test_the_repository_allows_the_new_basis(self) -> None:
        assert RESOLUTION_DATE in ALLOWED_EVALUATION_BASIS

    def test_the_old_basis_is_still_allowed(self) -> None:
        """⛔ 옛 값을 허용 목록에서 빼지 않는다 — 갱신 전 DB 도 읽어야 한다."""
        assert "PAYMENT_DATE" in ALLOWED_EVALUATION_BASIS

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_the_general_seeds_use_the_resolution_date(self, code: str) -> None:
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == code)
        assert seed.evaluation_basis == RESOLUTION_DATE

    def test_the_legacy_rows_are_migrated(self) -> None:
        """기존 DB 의 세 정책이 갱신 대상에 들어 있는가."""
        planned = {(code, old, new) for code, old, new in _UPDATED_EVALUATION_BASIS}
        for code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
            assert (code, "PAYMENT_DATE", RESOLUTION_DATE) in planned


class TestTheStartupRuleIsUnchanged:
    """⛔ 창업기업은 이 변경과 **무관**하다 (🟢 §0.6.2 그대로)."""

    def test_the_startup_seed_keeps_the_or_rule(self) -> None:
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")
        assert seed.evaluation_basis == RESOLUTION_OR_CONTRACT_DATE

    def test_the_startup_rule_still_accepts_a_contract_only_match(self) -> None:
        """창업기업은 계약일만 유효기간 안이어도 인정한다 — 바뀌지 않았다."""
        purchase = Purchase(
            business_no="1000000001",
            company_name="합성기업 가",
            contract_date=VALID_FROM,  # 안
            payment_date=OUTSIDE,
            resolution_date=OUTSIDE,  # 밖
            amount=Decimal("100000"),
        )
        context = RuleContext(purchase=purchase, validity_ranges=VALID)
        assert ResolutionOrContractDateRule().matches(context) is True
        # ⭐ 같은 건이 일반 3개 정책 규칙에서는 **불인정**이다 — 두 규칙은 다르다.
        assert ResolutionDateRule().matches(context) is False

    def test_the_green_policy_is_untouched(self) -> None:
        """⛔ 녹색제품은 이번 답변에 없다 — 기준을 바꾸지 않았다."""
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "GREEN")
        assert seed.evaluation_basis == "PAYMENT_DATE"
        assert seed.is_active is False


class TestNoNewPoliciesWereRegistered:
    """⛔ 확정 범위를 넘는 정책을 만들지 않았다.

    ⚠️ **기대값이 바뀐 이유** — 원래 이 시험은 *"사회적기업 · 사회적협동조합 ·
    장애인표준사업장을 **만들지 않았다**"* 를 지켰다. 고객은 §0.12.1 에서 그
    3종의 **기준일**만 말했을 뿐이고, 정책을 등록하라고 한 것이 아니었기
    때문이다. 2026-09-03 PM 이 최종 정책 범위를 **8종**으로 확정하면서
    (DECISIONS §0.22 · STEP 97) 그 3종에 자활용사촌까지 더해 네 정책이
    등록되었다.

    ⛔ 지키려던 것은 그대로 지킨다 — **기준일 언급만 보고 만든 것이 아니라
    확정을 받고 만들었다.** 그래서 아래 시험은 "없다" 를 "확정된 코드와 정확히
    일치한다" 로 바꿔 적었다. 확정 범위 밖의 정책이 하나라도 늘면 여전히
    실패한다.
    """

    #: 2026-09-03 PM 확정(STEP 97 §2)이 지정한 코드. ⛔ 임의로 정한 것이 아니다.
    CONFIRMED_NEW_CODES = (
        "SOCIAL_ENTERPRISE",
        "SOCIAL_COOPERATIVE",
        "DISABLED_STANDARD_WORKPLACE",
        "SELF_SUPPORT_VILLAGE",
    )

    @pytest.mark.parametrize(
        "code",
        ["SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE", "DISABLED_STANDARD_WORKPLACE"],
    )
    def test_the_policy_is_registered_with_the_confirmed_code(self, code: str) -> None:
        assert code in {seed.policy_code for seed in MVP_POLICY_SEEDS}

    def test_the_policy_set_matches_the_confirmed_scope(self) -> None:
        codes = {seed.policy_code for seed in MVP_POLICY_SEEDS}

        assert codes == {
            "SMALL_BUSINESS",
            "WOMAN",
            "DISABLED",
            "STARTUP",
            "GREEN",
            *self.CONFIRMED_NEW_CODES,
        }
        assert len(MVP_POLICY_SEEDS) == 9

    def test_the_new_policies_did_not_invent_a_date_basis(self) -> None:
        """⛔ 새 판정 유형을 만들지 않았다 — 일반 규칙(결의일자)을 그대로 쓴다."""
        for seed in MVP_POLICY_SEEDS:
            if seed.policy_code in self.CONFIRMED_NEW_CODES:
                assert seed.evaluation_basis == RESOLUTION_DATE, seed.policy_code
