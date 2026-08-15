"""
procurement.calculators.rules

정책 판정 규칙(Rule Engine) 패키지.

정책별 "이 구매를 실적으로 인정할지" 판단을 계산기 본체에서 분리하여,
``evaluation_basis`` 에 따라 교체 가능한 규칙(:class:`PolicyRule`)으로
관리합니다. 새 정책 유형은 계산기 수정 없이 규칙 등록만으로 지원할 수
있습니다 (개방-폐쇄 원칙)::

    from procurement.calculators.rules import build_default_registry

    registry = build_default_registry()
    rule = registry.get("PAYMENT_DATE")
    if rule.matches(context):
        ...
"""

from procurement.calculators.rules.base import PolicyRule, RuleContext
from procurement.calculators.rules.date_rules import (
    CONTRACT_DATE,
    PAYMENT_DATE,
    PAYMENT_OR_CONTRACT_DATE,
    ContractDateRule,
    DateBasisRule,
    PaymentDateRule,
    PaymentOrContractDateRule,
)
from procurement.calculators.rules.registry import RuleRegistry, build_default_registry

__all__ = [
    "CONTRACT_DATE",
    "PAYMENT_DATE",
    "PAYMENT_OR_CONTRACT_DATE",
    "ContractDateRule",
    "DateBasisRule",
    "PaymentDateRule",
    "PaymentOrContractDateRule",
    "PolicyRule",
    "RuleContext",
    "RuleRegistry",
    "build_default_registry",
]
