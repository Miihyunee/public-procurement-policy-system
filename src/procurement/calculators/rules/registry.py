"""
procurement.calculators.rules.registry

정책의 ``evaluation_basis`` 값을 :class:`PolicyRule` 로 매핑하는 레지스트리.

계산기는 정책 유형별 분기 대신 이 레지스트리에서 규칙을 조회해 호출합니다.
새 정책 유형(VENDOR_EXISTENCE, ITEM_COUNT, PRODUCT_MATCH 등)을 지원할 때는
계산기를 수정하지 않고 이 레지스트리에 규칙을 **등록만** 하면 됩니다
(개방-폐쇄 원칙).
"""

from __future__ import annotations

from procurement.calculators.rules.base import PolicyRule
from procurement.calculators.rules.date_rules import (
    CONTRACT_DATE,
    PAYMENT_DATE,
    PAYMENT_OR_CONTRACT_DATE,
    ContractDateRule,
    PaymentDateRule,
    PaymentOrContractDateRule,
)


class RuleRegistry:
    """``evaluation_basis`` → :class:`PolicyRule` 매핑을 관리합니다.

    조회 시 등록되지 않은 기준값에 대해서는 (설정된 경우) 기본 규칙을
    반환합니다. 이는 기존 계산기가 ``CONTRACT_DATE`` 이외의 모든 값을 지급일
    기준으로 처리하던 동작을 그대로 보존하기 위한 것입니다.
    """

    def __init__(self, default_rule: PolicyRule | None = None) -> None:
        """레지스트리를 초기화합니다.

        Args:
            default_rule: 등록되지 않은 기준값을 조회할 때 반환할 기본 규칙.
                ``None`` 이면 미등록 기준값 조회 시 :class:`KeyError` 를 발생시킵니다.
        """
        self._rules: dict[str, PolicyRule] = {}
        self._default_rule = default_rule

    def register(self, evaluation_basis: str, rule: PolicyRule) -> None:
        """기준값에 규칙을 등록합니다 (같은 값은 덮어씀).

        Args:
            evaluation_basis: 정책의 판정 기준 유형 (예: ``"PAYMENT_DATE"``).
            rule: 매핑할 :class:`PolicyRule` 구현체.
        """
        self._rules[evaluation_basis] = rule

    def get(self, evaluation_basis: str) -> PolicyRule:
        """기준값에 매핑된 규칙을 반환합니다.

        Args:
            evaluation_basis: 조회할 판정 기준 유형.

        Returns:
            매핑된 :class:`PolicyRule`. 미등록이면 기본 규칙(설정 시).

        Raises:
            KeyError: 미등록 기준값이고 기본 규칙도 설정되지 않은 경우.
        """
        rule = self._rules.get(evaluation_basis)
        if rule is not None:
            return rule
        if self._default_rule is not None:
            return self._default_rule
        raise KeyError(f"등록되지 않은 판정 기준입니다: {evaluation_basis}")


def build_default_registry() -> RuleRegistry:
    """기본 규칙이 등록된 :class:`RuleRegistry` 를 생성합니다.

    - ``PAYMENT_DATE`` → :class:`PaymentDateRule`
    - ``CONTRACT_DATE`` → :class:`ContractDateRule`
    - ``PAYMENT_OR_CONTRACT_DATE`` → :class:`PaymentOrContractDateRule` (창업기업)

    미등록 기준값은 지급일 기준(:class:`PaymentDateRule`)으로 처리하여 기존
    계산기 동작을 그대로 보존합니다.

    Returns:
        기본 규칙이 채워진 새 :class:`RuleRegistry`.
    """
    payment_date_rule = PaymentDateRule()
    registry = RuleRegistry(default_rule=payment_date_rule)
    registry.register(PAYMENT_DATE, payment_date_rule)
    registry.register(CONTRACT_DATE, ContractDateRule())
    registry.register(PAYMENT_OR_CONTRACT_DATE, PaymentOrContractDateRule())
    return registry
