# 고객 답변 → 구현 대응표

## 0. 이 문서가 하는 일

고객 답변이 들어왔을 때 **어디를 고쳐야 하는지** 미리 연결해 둔다. 답을 받고
나서 코드를 뒤지기 시작하면, 급한 마음에 영향 범위를 놓친 채 고치게 된다.

> ⛔ **내부 문서다.** 여기 적힌 파일·함수 이름을 **고객 질문 문장에 옮기지
> 않는다.** 고객이 읽는 것은 `CUSTOMER_DATA_QUESTIONS.md` 의
> 「📨 확인 요청서」 뿐이다.

> ⛔ **답변 전까지 어느 것도 구현하지 않는다.** 이 표는 계획이지 승인이 아니다.

| 표시 | 뜻 |
|---|---|
| 🟢 **고객 확정** | 고객이 직접 답한 업무규칙 |
| 🟡 **시스템 설계 판단** | 우리가 고른 구현 — **고객 확정이 아니다** |
| 🔴 **미회신** | 답이 없는 것 |

⛔ **"코드에 있다" 를 "고객이 확정했다" 로 바꾸지 않는다.** 🟡 은 시간이
지나도 저절로 🟢 이 되지 않는다.

---

## 1. 한눈에

| 질문 | 고객 답변에 따라 영향받는 영역 | 현재 상태 | 답변 전 변경 |
|---|---|---|---|
| **W-1-2** (Q-A) | 인증 유효기간 판정 | 🔴 미회신 | **금지** |
| **Q5-8** | 0원·음수 적재/계산 | 🔴 미회신 | **금지** |
| **Q5-9** | 예산과목 공란 처리 | 🔴 미회신 | **금지** |
| **Q71-A** | 실적 제외 원본 보존 | 🟡 설계 판단 | **금지** |
| **Q71-B** | 자동 제외 되돌리기 | 🟡 설계 판단 | **금지** |
| **Q71-C** | 금액 검색 | 🟡 기능 판단 | **금지** |
| **Q71-D** | 묶음 확인 | 🟡 기능 판단 | **금지** |
| **W-11** (Q-B) | 인증서 조회 기준일자 | 🔴 미회신 | **금지** |
| **W-12 · W-13** (Q-C) | 직접생산확인증명 사용 | 🔴 미회신 | **금지** |
| **W-14** (Q-D) | 시험용 사업자등록번호 | 🔴 미회신 | **금지** |
| **W-15** (Q-E) | 결의일자 공란 처리 | 🔴 미회신 | **금지** |
| **W-6** (Q5) | 구매유형 자동분류 | 🟢 원칙 부분 회신 · 🔴 자동분류 불가 | **금지** |

⚠️ **D-24**(연도 귀속 기준일 설정)는 고객 질문이 아니라 **설정값**이지만,
W-1-2 와 함께 정리되어야 한다 — `UNCONFIRMED_RULES_IMPACT.md` §1.1.

---

## 2. 질문별 구현 위치

⚠️ 상세 추적은 `UNCONFIRMED_RULES_IMPACT.md` 에 있다. 여기서는 **답이 오면
손댈 자리**만 짧게 적는다.

### 2.1 W-1-2 — 인증 유효기간 판정 기준일 🔴

| 항목 | 위치 |
|---|---|
| 정책별 기준 | `database/bootstrap.py` `MVP_POLICY_SEEDS` (`evaluation_basis`) |
| 기존 DB 갱신 | `database/bootstrap.py` `migrate_policy_evaluation_basis()` |
| 판정 규칙 | `calculators/rules/date_rules.py` |
| 규칙 등록 | `calculators/rules/registry.py` |
| 분자 계산 | `calculators/procurement_achievement.py` `calculate_policy_purchase()` |

⚠️ **결의일자만 보는 규칙이 없다.** 지금 있는 것은 `PaymentDateRule` ·
`ContractDateRule` · `ResolutionOrContractDateRule` 셋뿐이라, 일반 정책을
결의일자로 바꾸려면 **규칙을 새로 만들어야 한다.**

⚠️ 🟢 창업기업의 `RESOLUTION_OR_CONTRACT_DATE` 는 **이미 확정**이며 이 답변과
무관하게 유지한다.

### 2.2 Q5-8 — 0원·음수 🔴

| 항목 | 위치 |
|---|---|
| 저장 거부 | `database/purchase_repository.py` `_validate()` (`amount <= 0`) |
| 업로드 경고 | `uploads/validation.py` `_parse_amount()` |
| 미적재 사유 | `models/import_rejection.py` `REASON_NON_POSITIVE_AMOUNT` |
| 사유 매핑 | `importers/rejection_trace.py` |

⚠️ `NON_POSITIVE_AMOUNT` 는 **시스템의 미적재 사유 코드**이지 고객이 정한
업무 처리 결과가 아니다.

⚠️ 음수 상계 규칙은 🟢 확정(§0.6.3)인데 저장소가 음수를 받지 않아 **상계
대상이 될 행이 들어오지 못한다.** 답변이 이 어긋남을 푼다.

### 2.3 Q5-9 — 예산과목 공란 🔴

| 항목 | 위치 |
|---|---|
| 공란 허용 | `uploads/format.py` (`budget_account.required = False`) |
| 검증 | `uploads/validation.py` |
| 제외 판정 | `core/performance_exclusion.py` `is_excluded_budget_account()` |

⚠️ 지금은 공란이 **계산에 포함**된다. 이는 현재 구현이며 🟢 확정이 아니다.

### 2.4 Q71-A — 실적 제외 건의 원본 보존 🟡

| 항목 | 위치 |
|---|---|
| 상태·사유·확정자·시각 | `database/review_repository.py` · `models/review.py` |
| 계산에서 제외 | `database/purchase_repository.py` `find_for_calculation()` |
| 검토 화면 유지 | 〃 `find_for_review()` |
| 이력 | `ACTION_EXCLUDED` / `ACTION_INCLUDED` |

⚠️ 고객은 *"삭제"* 라고 답했고, **원본을 남기기로 한 것은 우리 판단**이다.
원본 삭제를 원하시면 **이력 보존 구조와 충돌**하므로 별도 설계가 필요하다.

### 2.5 Q71-B — 자동 제외 되돌리기 🟡

| 항목 | 위치 |
|---|---|
| 6종 판정 | `core/performance_exclusion.py` (정확히 같은 값) |
| 되돌리기 제한 | `reviews/response.py` `PerformanceResponseModel` (`can_reopen`) |
| 되돌리기 처리 | `database/review_repository.py` `include_in_performance()` |

⚠️ 🟢 **6종 자동 제외는 확정**이다. 🟡 **되돌리지 못하게 한 것은 우리
판단**이며, 고객이 그렇게 말한 적은 없다.

### 2.6 Q71-C — 금액 검색 🟡

| 항목 | 위치 |
|---|---|
| 검색 조건 | `reviews/review_service.py` `_matches()` (적요·거래처명·사업자등록번호) |
| 정렬 축 | `reviews/query.py` `SORT_KEYS` (`amount` 포함) |

⚠️ 고객이 *"금액을 비교하여 확인한다"* 고 한 것은 **업무 방식**이다. ⛔ 금액
검색 **기능 요구로 해석하지 않는다.**

### 2.7 Q71-D — 지출결의서 단위 묶음 🟡

| 항목 | 위치 |
|---|---|
| 검토 단위 | `reviews/review_service.py` (거래 건별) |
| 목록 응답 | `reviews/response.py` (묶음 필드 없음) |

⚠️ *"어느 방식이 편한지 모르겠다"* 는 **요청이 아니다.** ⛔ 자동 그룹핑 ·
결의번호 생성 · 임의 묶음 키를 만들지 않는다.

### 2.8 W-11 ~ W-15 🔴

| 질문 | 무엇이 걸려 있는가 | 위치 |
|---|---|---|
| **W-11** (Q-B) | 인증서 조회 시 넣을 기준일자 | `collectors/client.py` · `collectors/sync_service.py` |
| **W-12 · W-13** (Q-C) | 직접생산확인증명 사용 여부 | `collectors/client.py` `SOURCE_DIRECT_PRODUCTION` — ⛔ 정책 코드에 넣지 않았다 |
| **W-14** (Q-D) | 시험용 사업자등록번호 | 없음 — 실제 번호를 저장소에 넣지 않는다 |
| **W-15** (Q-E) | 결의일자 공란 처리 | `dashboard/data_service.py` 조회 · `database/purchase_repository.py` `find_missing_resolution_date()` |

⚠️ **W-11 과 W-1-2 는 다른 질문이다.** 하나는 *계산할 때 보는 날짜*, 다른
하나는 *조회할 때 요청하는 시점*이다(`DECISIONS.md` §0.4).

⛔ W-11 확정 전까지 **운영 자동 조회를 구현하지 않는다.**

### 2.9 W-6 — 구매유형 자동분류 🟢 부분 회신 / 🔴 자동분류 불가

| 항목 | 위치 |
|---|---|
| 확정 3건 매핑 | `core/purchase_type.py` (도서인쇄비·소모성물품구입비 → 물품, 임차료 → 용역) |
| 검토 흐름 | `reviews/review_service.py` (PENDING → 담당자 확정) |

현재 흐름을 그대로 유지한다.

```
PENDING → 담당자가 지출결의서·세금계산서 등을 확인 → 담당자 확정
```

⛔ **자동분류를 만들지 않는다.** 고객이 원칙 5가지를 답했지만, 애매한 건마다
지출결의서를 열어 판정한다는 것도 함께 확인되었다(§0.9.5).

---

## 3. 답변이 오면 하는 일 (순서)

```
① 답변 원문을 CUSTOMER_DATA_QUESTIONS.md 에 그대로 기록
        ↓
② DECISIONS.md 에 🟢 로 확정 기록 — 고객이 말한 범위만
        ↓
③ 이 표에서 해당 줄의 위치를 열어 영향 범위 재확인
        ↓
④ 구현 — ⛔ 답변에 없는 것을 일반화하지 않는다
        ↓
⑤ 시험으로 잠금 — 확정된 사실만
        ↓
⑥ 기존 시험이 깨지면, 그것이 옛 동작을 기록한 것인지 먼저 확인
```

⚠️ **④가 가장 위험하다.** 답 하나를 받으면 비슷한 사례까지 규칙으로 만들고
싶어진다. 고객이 말한 것만 확정한다.

---

## 4. 우선순위

| 순위 | 질문 | 왜 먼저인가 |
|---|---|---|
| **1** | W-1-2 | 달성률 숫자를 **직접** 바꾼다. 미확정이면 기간 설정 자체가 임시값이다 |
| **2** | Q5-8 | 실데이터 **130행**이 걸려 있고, 확정된 상계 규칙과 현재 구현이 어긋나 있다 |
| **3** | Q5-9 | 실데이터 **129행**. 계산에 포함할지가 정해지지 않았다 |
| **4** | Q71-A ~ Q71-D | 숫자보다 **화면·운영 방식**에 관한 것이다 |
| **5** | W-11 ~ W-15 · W-6 | 인증 수집 자동화·구매유형 자동화의 전제 |

⛔ 순위는 **작업 순서**이지, 답이 오지 않은 것을 건너뛰어도 된다는 뜻이
아니다.

---

## 5. 답변 하나가 다른 규칙을 자동 확정하지 않게 하는 4가지 원칙

답이 하나 오면 비슷한 것까지 함께 정리하고 싶어진다. 그렇게 정한 규칙은
**고객이 말한 적 없는 규칙**이고, 그것이 실적 숫자가 된다.

### 원칙 1 — 답변에 적힌 범위만 확정한다

> *"일반 3정책은 결의일자를 기준으로 해 주세요."*

이 답으로 확정되는 것은 **하나**다.

```
🟢 일반 3정책의 인증 유효기간 판정 기준 = 결의일자
```

⛔ 함께 확정되지 **않는** 것.

| 항목 | 왜 아닌가 |
|---|---|
| 연도 귀속 기준(축 ①) | 다른 축이다 — §2.1 · `UNCONFIRMED_RULES_IMPACT.md` §1.1 |
| 창업기업 기준 | 이미 🟢 확정된 별개 규칙(결의일자 **또는** 계약일자) |
| 인증서 조회 시점(W-11) | 조회 요청에 넣는 값이며 계산 기준이 아니다 — `DECISIONS.md` §0.4 |
| 다른 정책 기준 | 답변이 지목한 정책만 |

### 원칙 2 — 질문 하나의 답으로 다른 질문을 닫지 않는다

⛔ 특히 아래는 **서로 다른 질문**이며, 하나의 답으로 나머지가 정해지지 않는다.

```
W-1-2 (계산할 때 보는 날짜)   ↮   W-11 (조회할 때 요청하는 시점)
Q5-8  (0원·음수)              ↮   Q5-9 (예산과목 공란)
Q71-B (6종 자동 제외 되돌리기) ↮   6종 자동 제외 자체 (🟢 이미 확정)
Q71-C (금액 검색)             ↮   Q71-D (지출결의서 묶음)
```

⚠️ **Q5-8 과 Q5-9 는 같은 "빈 값·이상값" 처럼 보이지만 다르다.** 금액은
저장 자체가 거부되고, 예산과목 공란은 저장되어 계산에 들어간다.

### 원칙 3 — 코드 변경을 요구하지 않는 답이면 코드를 바꾸지 않는다

답이 **현행 유지**라면 하는 일은 기록뿐이다.

```
CUSTOMER_DATA_QUESTIONS.md 에 답변 원문
        ↓
DECISIONS.md 에 🟢 로 기록 (🟡 였다면 이때 🟢 이 된다)
        ↓
코드 변경 없음
```

⛔ "이왕 손대는 김에" 를 붙이지 않는다.

### 원칙 4 — 시험이 깨지면 원인을 먼저 가른다

셋 중 어느 것인지 확인한 **다음에** 손댄다.

| 무엇인가 | 어떻게 하는가 |
|---|---|
| ① 옛 업무규칙을 기록하던 시험 | 기대값을 새 규칙으로 **갱신**하고, 왜 바뀌었는지 시험에 적는다 |
| ② 답변으로 업무규칙이 실제로 바뀜 | 위와 같다 |
| ③ 구현 버그 | **시험이 옳다.** 코드를 고친다 |

⛔ **통과시키려고 assertion 을 지우지 않는다.** ⛔ `skip` 을 붙이지 않는다.

⚠️ 전례가 있다 — STEP 74 에서 `test_end_to_end_chain.py` 5건이 깨졌고, 그
시험들은 *"정규화 미구현"* 이라는 **옛 결함을 기록**하고 있었다(①). 기대값을
갱신하면서 검증은 오히려 늘렸다.

---

## 6. 반영 준비 상태 (2026-08-31 · STEP 78 점검)

| 확인 항목 | 상태 |
|---|---|
| 고객 확인 요청 9문항 | 🔴 전부 미회신 |
| 미확정 항목 13개 | 🔴 전부 유지 |
| 이 표가 가리키는 소스 파일 | **전부 실제로 존재** (30건 대조) |
| 결의일자 단독 판정 규칙 | **없음** — 답변이 그쪽이면 새로 만들어야 한다 |
| 연도 귀속 기준 기본값 | **없음** — 기본값을 두면 그것이 곧 확정이 된다 |
| 실제 고객 데이터 | **없음** — `database/procurement.db` 0 bytes · 테이블 0개 |

⚠️ **축 ① 은 설정, 축 ② 는 코드다.** 같은 "결의일자" 라는 답이 와도 드는
품이 다르다 — 축 ① 은 설정값 하나, 축 ② 는 규칙 신설이다.

---

## 7. 답변이 오면 **깨질 것으로 예상되는 시험**

⚠️ 아래 시험들은 **현재 동작을 기록**하고 있다. 답변으로 업무규칙이 바뀌면
**깨지는 것이 정상**이며, 그 깨짐이 "숫자가 달라진다" 는 알림이다(§5 원칙 4 ①).

⛔ 깨졌다고 지우지 않는다. 기대값을 갱신하고 **왜 바뀌었는지 시험에 적는다.**

| 답변 | 먼저 깨질 시험(파수꾼) | 무엇을 지키고 있었나 |
|---|---|---|
| **W-1-2** 축 ② 변경 | `test_achievement_boundaries.py::TestGeneralPolicyBasisIsCurrentBehaviour` | 일반 3정책이 지급일을 본다는 **현재 동작**(확정 아님) |
| 〃 | `test_achievement_boundaries.py::TestUnconfirmedRulesAreNotImplemented` | 미확정 규칙이 구현되지 않았다는 사실 |
| 〃 | `test_unconfirmed_rules_impact.py::TestTheDocumentMatchesTheCode` | 문서가 적은 현재 동작과 코드의 일치 |
| 〃 | `test_customer_answer_readiness.py::TestTheMapMatchesTheCode` | 결의일자 단독 규칙이 **없다**는 사실 |
| **Q5-8** 저장 허용 | `test_performance_exclusion.py::test_q5_8_zero_and_negative_still_rejected_at_import` | 0원·음수가 적재되지 않는다는 현재 동작 |
| 〃 | `test_import_trace.py` · `test_import_trace_export.py` | 미적재 행 수·사유·CSV 내용 |
| 〃 | `test_end_to_end_import_calculation.py` | 원본 = 적재 + 미적재 항등식의 건수 |
| **Q5-9** 공란 제외 | `test_unconfirmed_rules_impact.py::test_a_blank_budget_account_is_not_excluded` | 공란이 제외되지 않는다는 현재 동작 |
| 〃 | `test_performance_exclusion.py::test_blank_account_is_not_excluded` | 〃 (계산 대상 포함) |
| **Q71-B** 되돌리기 허용 | `test_performance_exclusion.py::TestBudgetAccountRule` (`can_reopen` · 되돌려도 유지) | 규칙 건은 되돌릴 수 없다는 현재 제한 |
| **Q71-A** 원본 삭제 | `test_performance_exclusion.py::TestReversalAndHistory` · `test_the_row_is_never_deleted` | 원본이 지워지지 않는다는 현재 구조 |
| **Q71-C** 금액 검색 | 없음 — 추가 기능이라 기존 시험이 깨지지 않는다 | — |
| **Q71-D** 묶음 | `test_customer_answer_scope.py::TestNoExpenseDocumentGrouping` | 묶음 축·필드가 없다는 사실 |
| **W-11** 조회 기준일 | `test_collector_sync_service.py` | 조회 파라미터 구성 |

⚠️ 각 답변마다 **문구 시험**도 함께 깨질 수 있다 —
`test_operations_checklist.py` · `test_operations_check_result.py` ·
`test_customer_rule_implementation_map.py` 는 "아직 확정되지 않았다" 는 **문장**을
지킨다. 확정되면 그 문장을 먼저 고치고 시험을 맞춘다.

⛔ **답변이 "현행 유지" 면 위 시험 중 어느 것도 건드리지 않는다**(§5 원칙 3).
