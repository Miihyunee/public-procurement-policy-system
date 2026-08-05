# Issue #17 — 정책 판정 Rule Engine 설계 (Strategy Pattern)

## 문서 정보

| 항목 | 내용 |
|------|------|
| Version | v1.0 (제안/Draft) |
| Status | PM 검토 대기 |
| Last Updated | 2026-08-04 |
| 성격 | **설계 전용 문서** — 코드/테스트/DB/설계문서 변경 없음 |
| 관련 | #14(evaluation_basis), #15(Calculator 판정), #16(자활용사촌 분석) |

> 본 문서는 Issue #17 의 산출물(분석 + 구조 제안 + 장단점 + 권장 구조 + 구현 순서)이다.
> **이 문서는 어떤 코드도 테스트도 DB도 변경하지 않는다.** 본문의 코드 조각은 모두 **설계 스케치(예시)** 이며 실제 구현이 아니다.

## 목적

정책이 계속 추가되어도 **Calculator 를 매번 수정하지 않는** 확장 구조를 설계한다.

| 구분 | evaluation_basis 값 |
|------|---------------------|
| 현재 지원 | `PAYMENT_DATE`, `CONTRACT_DATE` |
| 향후 예정 | `VENDOR_EXISTENCE`, `PRODUCT_MATCH`, `ITEM_COUNT`, `CUSTOM RULE` |

---

# Part 1. 현재 구조 분석

## 1.1 현재 판정 위치

#15 기준, 정책별 판정은 `ProcurementAchievementCalculator._sum_policy_purchase` 내부에 하드코딩되어 있다.

```python
# (현재 코드 — 설계 분석용 발췌)
basis_date = self._basis_date(purchase, policy.evaluation_basis)   # if/else 분기
if self._is_within_any(basis_date, validity_ranges[company_id]):
    total += purchase.amount

@staticmethod
def _basis_date(purchase, evaluation_basis):
    if evaluation_basis == "CONTRACT_DATE":
        return purchase.contract_date
    return purchase.payment_date            # 그 외 = PAYMENT_DATE
```

## 1.2 문제점 (확장성 관점)

| # | 문제 | 설명 |
|---|------|------|
| 1 | **OCP 위반** | 새 정책 유형 추가 = `_sum_policy_purchase`/`_basis_date` 를 **직접 수정**해야 함. "확장에는 열리고 변경에는 닫힘" 원칙 위배 |
| 2 | **if/elif 분기 증식** | `VENDOR_EXISTENCE`(기간 무시), `PRODUCT_MATCH`(품목 기준), `ITEM_COUNT`(갯수 집계), `CUSTOM`(가변 규칙)이 추가되면 조건 분기가 계속 늘어남 |
| 3 | **집계 형태 고정** | 현재는 "금액 합산" 단일 형태. `ITEM_COUNT` 는 갯수, `VENDOR_EXISTENCE` 는 거래유무+갯수 등 **집계 결과 구조가 규칙마다 다름** → 단일 메서드로 수용 어려움 |
| 4 | **테스트 부담 집중** | 모든 규칙이 한 메서드에 몰려 한 규칙 변경이 다른 규칙 테스트에 영향 |
| 5 | **책임 혼재** | Calculator 가 "무엇을 계산할지(오케스트레이션)"와 "각 정책을 어떻게 판정할지(규칙)"를 동시에 소유 |

---

# Part 2. Rule Engine 설계

## 2.1 개념

정책 판정 로직을 **정책 유형별 규칙 객체(Rule/Strategy)** 로 분리하고, Calculator 는 규칙을 **직접 알지 않고** 레지스트리에서 찾아 위임한다.

```text
Calculator (오케스트레이션)
   │  evaluation_basis 로 규칙 조회
   ▼
RuleRegistry ──► PolicyRule (전략 인터페이스)
                   ├─ PaymentDateRule      (PAYMENT_DATE)
                   ├─ ContractDateRule     (CONTRACT_DATE)
                   ├─ VendorExistenceRule  (VENDOR_EXISTENCE)   ← 향후
                   ├─ ProductMatchRule     (PRODUCT_MATCH)      ← 향후
                   ├─ ItemCountRule        (ITEM_COUNT)         ← 향후
                   └─ CustomRule           (CUSTOM)             ← 향후
```

## 2.2 규칙 인터페이스 후보 (집계 형태 3안)

집계 결과가 규칙마다 다르므로(금액/갯수/거래유무), 인터페이스 반환형이 핵심이다.

**후보 ① Predicate(구매 포함 여부만)**
```python
class PolicyRule(Protocol):
    def matches(self, purchase, context) -> bool: ...
```
- Calculator 가 True 인 구매의 `amount` 를 합산. 단순하지만 **갯수·제품 집계 불가**.

**후보 ② Contribution(구매별 기여값 반환)**
```python
@dataclass
class RuleContribution:
    amount: Decimal
    item_count: int = 0

class PolicyRule(Protocol):
    def evaluate(self, purchase, context) -> RuleContribution: ...   # 미포함이면 0
```
- 금액·갯수 등 **여러 지표를 동시에** 누적 가능. 구매 단위 반복은 Calculator 가 유지.

**후보 ③ Aggregation(정책 전체 집계를 규칙이 소유)**
```python
class PolicyRule(Protocol):
    def aggregate(self, purchases, certifications, policy) -> PolicyMetrics: ...
```
- 규칙이 **배치 전체**를 계산(거래유무·중복제거·제품매칭 등 자유도 최대). Calculator 는 완전 위임.

## 2.3 Context 객체

규칙마다 필요한 입력이 다르므로(날짜, 인증 유효기간, 품목, 제품), 공통 **RuleContext** 로 전달한다.

```python
@dataclass
class RuleContext:
    policy: Policy
    validity_ranges: dict[int, list[tuple[date, date]]]  # company_id -> 인증기간
    # 향후: items, products 등 규칙별 확장 데이터
```

## 2.4 레지스트리 / 팩토리

```python
RULE_REGISTRY: dict[str, PolicyRule] = {
    "PAYMENT_DATE": PaymentDateRule(),
    "CONTRACT_DATE": ContractDateRule(),
    # 향후 값은 여기에만 등록 → Calculator 무수정
}

def resolve_rule(evaluation_basis: str) -> PolicyRule:
    try:
        return RULE_REGISTRY[evaluation_basis]
    except KeyError:
        raise CalculatorValidationError(...)
```

Calculator 는 `resolve_rule(policy.evaluation_basis)` 만 호출 → **새 규칙 추가 시 레지스트리 등록만** 하면 됨.

---

# Part 3. Strategy Pattern 적용 가능성 검토

| 검토 항목 | 결과 |
|-----------|------|
| 적용 가능성 | **높음.** 정책 유형별로 "판정 알고리즘"이 교체되는 전형적 Strategy 케이스 |
| Python 구현 방식 | `typing.Protocol` (덕타이핑, mypy strict 호환) 또는 `abc.ABC`. 프로젝트는 dataclass·타입힌트 위주이므로 **Protocol 권장** |
| 레지스트리 | dict 기반 정적 등록(권장) 또는 데코레이터 자동 등록 |
| 기존 코드 영향 | Calculator 의 판정부만 위임으로 교체. `evaluation_basis`(#14)·유효기간 데이터는 그대로 재사용 |
| mypy strict | Protocol + 명시적 반환 dataclass 로 충족 가능 |
| 리스크 | 초기 추상화 비용(구조 신설). 규칙이 2개뿐인 현재는 과설계 위험 → **후속 규칙 도입 시점에 도입 권장** |

---

# Part 4. Policy Rule 구조 제안

## 4.1 패키지 배치 (제안)

```text
src/procurement/calculators/
├── procurement_achievement.py     # Calculator (오케스트레이션만)
├── achievement_result.py
└── rules/                          # ← 신규 (규칙 계층)
    ├── __init__.py                 # RULE_REGISTRY / resolve_rule
    ├── base.py                     # PolicyRule(Protocol), RuleContext, RuleContribution
    ├── payment_date_rule.py
    ├── contract_date_rule.py
    └── (향후) vendor_existence_rule.py / product_match_rule.py / item_count_rule.py / custom_rule.py
```

## 4.2 규칙 계약(권장 = 후보 ②)

- 입력: `purchase`, `RuleContext`
- 출력: `RuleContribution(amount, item_count)` — 미해당이면 `amount=0, item_count=0`
- Calculator 는 정책 대상 구매를 순회하며 기여값을 누적 → `AchievementResult` 구성.
- 근거: ①은 갯수·제품 확장 불가, ③은 자유도는 크나 규칙이 Repository 접근까지 떠안아 책임이 과함. **②가 확장성과 단순성의 균형점.**

## 4.3 규칙별 판정 요약 (설계 의도)

| evaluation_basis | 규칙 동작 (설계) | 필요 추가 데이터 |
|------------------|------------------|------------------|
| PAYMENT_DATE | 지급일이 유효기간 내 → amount | 없음 (현행) |
| CONTRACT_DATE | 계약일이 유효기간 내 → amount | 없음 (현행) |
| VENDOR_EXISTENCE | 기간 무시, 거래 존재 → amount + item_count | Purchase item_count (#19) |
| PRODUCT_MATCH | 품목/제품 인증 매칭 → amount | 품목/제품 데이터 |
| ITEM_COUNT | 갯수 중심 집계 | Purchase item_count |
| CUSTOM | Policy 에 저장된 규칙 파라미터로 동작 | Policy rule_config (아래) |

---

# Part 5. 장단점 비교

| 기준 | 현행 if/elif (Calculator 내장) | Rule Engine + Strategy |
|------|-------------------------------|------------------------|
| 새 정책 추가 시 Calculator 수정 | **필요** | **불필요** (레지스트리 등록만) |
| OCP 준수 | ✗ | ✅ |
| 집계 형태 다양성(금액/갯수/제품) | 어려움 | 용이 (RuleContribution 확장) |
| 규칙별 단위 테스트 | 어려움(한 메서드 집중) | 용이(규칙 파일별) |
| 초기 구현 비용 | 낮음 | 중간(구조 신설) |
| 현재(규칙 2개) 적정성 | 충분 | 다소 과함 → 규칙 3개째부터 이득 |
| 가독성 | 분기 증가 시 저하 | 규칙 분리로 유지 |

---

# Part 6. DATABASE_DESIGN 영향 분석

| 대상 | 영향 |
|------|------|
| `Policy.evaluation_basis` | **키로 그대로 사용.** 허용값에 `VENDOR_EXISTENCE`/`PRODUCT_MATCH`/`ITEM_COUNT`/`CUSTOM` 을 순차 추가(별도 Issue). Rule Engine 자체는 스키마 변경 불요 |
| `Purchase` | Rule Engine 도입만으로는 변경 없음. `ITEM_COUNT`/`VENDOR_EXISTENCE` 규칙 구현 시 `item_count`(#16의 #19) 필요 |
| `Policy.rule_config` (신규 검토) | `CUSTOM` 규칙은 정책마다 파라미터가 달라 **규칙 설정을 데이터로 저장**할 컬럼(JSON/TEXT)이 필요할 수 있음. Rule Engine 도입 시점엔 불요, `CUSTOM` 도입 Issue 에서 검토 |
| 제품/품목 테이블 | `PRODUCT_MATCH` 는 품목/제품 모델(#16 안 B PurchaseItem) 선행 필요 |

> 결론: **Rule Engine 골격 자체는 DB 변경이 필요 없다.** 각 새 규칙이 요구하는 데이터가 생길 때 해당 Issue 에서 스키마를 확장한다.

---

# Part 7. 권장 구조 & 구현 순서

## 7.1 권장 구조

- **Strategy Pattern + 정적 레지스트리**, 규칙 계약은 **후보 ②(RuleContribution 반환)**.
- `calculators/rules/` 패키지 신설, Calculator 는 오케스트레이션만 담당.
- `PolicyRule`(Protocol) + `RuleContext` + `RuleContribution` + `RULE_REGISTRY`.
- 기존 2개 규칙(PaymentDate/ContractDate)을 먼저 전략으로 이관(동작 동일, 리팩터링) → 회귀 테스트로 안전 확인.

## 7.2 도입 판단(트레이드오프)

- 규칙이 **2개뿐인 지금 당장 도입은 과설계 위험**. 그러나 #16 로드맵(VENDOR_EXISTENCE 등 4종 예정)이 확정적이므로, **자활용사촌 구현(#18~) 직전에 Rule Engine 을 먼저 도입**하는 것이 이후 규칙들을 무수정 Calculator 로 흡수하는 최적 시점.

## 7.3 구현 순서 (제안 Issue)

| 제안 Issue | 제목 | 내용 | DB 변경 |
|-----------|------|------|---------|
| **#R1** | Rule Engine 골격 도입(리팩터링) | `rules/` 패키지 + PolicyRule/Context/Contribution/Registry 신설, 기존 PAYMENT_DATE·CONTRACT_DATE 를 전략으로 이관. **동작·결과 불변**, 회귀 테스트 통과 | 없음 |
| **#R2** | Calculator 위임 전환 | `_sum_policy_purchase` 를 `resolve_rule().evaluate()` 위임 구조로 교체 | 없음 |
| **#R3** | `AchievementResult` 다지표 확장 | `item_count` 등 규칙별 지표 수용 (필요 시) | 없음 |
| **#R4~** | 개별 규칙 추가 | VENDOR_EXISTENCE → PRODUCT_MATCH → ITEM_COUNT → CUSTOM 순, 각 규칙 파일 + 레지스트리 등록 + 테스트. 데이터가 필요한 규칙은 선행 스키마 Issue 연계(#16 로드맵) | 규칙별 상이 |

> #16(자활용사촌)과의 연계: #16 의 #18(evaluation_basis 값)·#19(품목 데이터)·#21(Calculator 분기)은 Rule Engine 도입 후에는 **"VENDOR_EXISTENCE 규칙 1개 추가"** 로 단순화된다.

---

# Deliverable 체크

- ✅ 분석 보고서 (Part 1)
- ✅ 구조 제안 (Part 2·4)
- ✅ 장단점 비교 (Part 5)
- ✅ 권장 구조 (Part 7.1)
- ✅ 구현 순서 (Part 7.3)
- ✅ 코드/테스트 미작성, 문서만 작성

# 요약

- 현재 Calculator 는 `evaluation_basis` if/elif 내장 → 정책 추가 시 매번 수정 필요(OCP 위반).
- **Strategy Pattern + 레지스트리**로 정책 판정을 규칙 객체로 분리하면, 새 정책은 **규칙 파일 추가 + 레지스트리 등록**만으로 Calculator 무수정 확장 가능.
- 규칙 계약은 **RuleContribution(amount, item_count) 반환**을 권장(금액·갯수·제품 등 다지표 수용).
- Rule Engine 골격 자체는 **DB 변경 불요**. 각 규칙이 요구하는 데이터가 생길 때 해당 Issue 에서 스키마 확장.
- 도입 시점: 규칙 2개인 현재는 과설계 위험 → **자활용사촌 등 후속 규칙 도입 직전(#R1)** 에 골격을 먼저 세우는 것을 권장.
- 본 Issue(#17) 에서는 **어떤 코드·DB·설계문서도 변경하지 않았다.**
