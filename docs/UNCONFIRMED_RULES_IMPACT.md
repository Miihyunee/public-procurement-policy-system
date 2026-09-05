# 미확정 업무규칙 영향도

## 0. 이 문서가 하는 일

고객이 아직 답하지 않은 항목들이 **코드의 어디에 걸려 있고, 답이 오면 무엇을
고쳐야 하는지**를 파일·함수 단위로 적어 둔다.

> ⛔ **어느 것도 확정하지 않는다.** 여기 적힌 "현재 동작" 은 **지금 코드가
> 이렇게 되어 있다**는 사실일 뿐이며, **업무적으로 옳다는 뜻이 아니다.**

> ⛔ **현재 동작을 업무규칙으로 승격하지 않는다.** 특히 시험 환경에서 기간
> 기준일로 지급일을 주입한 것(STEP 73)은 **시험을 돌리기 위한 값**이다.

| 표시 | 뜻 |
|---|---|
| 🟢 **고객 확정** | 고객이 직접 답한 업무규칙 |
| 🟡 **시스템 설계 판단** | 우리가 고른 구현 — 고객 확인 대기 |
| 🔴 **미확정** | 답이 없는 것. 이 문서의 대상 |

### 0.1 ⚠️ 이름이 겹친다 — `Q-A` 가 둘이다

| 이름 | 어디에 있는가 | 무엇인가 |
|---|---|---|
| **Q-A** | `CUSTOMER_DATA_QUESTIONS.md` §Q-A | **W-1-2** — 인증 유효기간 판정 기준일 |
| **Q-A** | STEP 71 완료보고 | 실적 제외 시 **원본 행 보존** 여부 |

혼동을 막기 위해 이 문서와 앞으로의 보고에서는 STEP 71 쪽을
**`Q71-A` ~ `Q71-D`** 로 부른다. ⛔ 기존 `Q-A` ~ `Q-E` 는 그대로 둔다 — 고객이
이미 받아 본 이름이다.

---

## 1. 🔴 W-1-2 — 인증 유효기간 판정 기준일

### 1.1 ⚠️ 두 개의 날짜 축을 섞지 않는다

코드에는 날짜를 보는 곳이 **둘** 있고, 서로 다른 질문이다.

```
① 연도 귀속 축 (기간 필터)          ② 인증 유효기간 판정 축
   "이 구매는 몇 년치인가"              "이 구매가 인증기간 안인가"
   설정 PURCHASE_PERIOD_DATE_FIELD      정책별 evaluation_basis
   core/period.py                       calculators/rules/date_rules.py
   D-24 · 🔴 미확정                     W-1-2 · 🔴 미확정
```

⛔ **하나를 정해도 다른 하나가 따라 정해지지 않는다.**

### 1.2 현재 동작 — 축 ① 연도 귀속

| 항목 | 현재 |
|---|---|
| 설정 이름 | `settings.PURCHASE_PERIOD_DATE_FIELD` (`core/config/settings.py`) |
| 허용 값 | `payment_date` · `contract_date` · `resolution_date` (`core/period.py`) |
| 기본값 | **없음** |
| 미설정이면 | `GET /dashboard/summary` → **503** (`app.py` `_require_period`) |
| `year` 미지정이면 | **400** — 전 기간을 임의로 합산하지 않는다(D-27) |

⚠️ 미설정 상태에서는 **달성률 조회 자체가 불가능하다.** 시험은 지급일을
주입해서 돌리며, 그것은 확정이 아니다.

### 1.3 현재 동작 — 축 ② 인증 유효기간 판정

`policy.evaluation_basis` 가 정책마다 다르다(`database/bootstrap.py` 의
`MVP_POLICY_SEEDS`).

| 정책 | `evaluation_basis` | 보는 날짜 | 근거 |
|---|---|---|---|
| 중소기업 | `PAYMENT_DATE` | 지급일 | 🔴 W-1-2 대상 |
| 여성기업 | `PAYMENT_DATE` | 지급일 | 🔴 W-1-2 대상 |
| 장애인기업 | `PAYMENT_DATE` | 지급일 | 🔴 W-1-2 대상 |
| 창업기업 | `RESOLUTION_OR_CONTRACT_DATE` | 결의일자 **또는** 계약일자 | 🟢 2026-08-14 고객 확정 |
| 녹색제품 | `PAYMENT_DATE` | — | 비활성(§0.5.1) |

⛔ **일반 3정책이 지급일을 쓰는 것은 확정이 아니다.** 그것이 바로 W-1-2 다.
⛔ 창업기업의 OR 규칙은 **이미 고객이 확정**했으므로 W-1-2 답변과 무관하게
유지한다.

### 1.4 계산까지 이어지는 경로

```
설정 PURCHASE_PERIOD_DATE_FIELD           ← 축 ①
        ↓  app.py  _require_period()
PeriodFilter.for_year(year, date_field)   core/period.py
        ↓
find_for_calculation(period)              database/purchase_repository.py
        ↓  ── 여기까지가 분모 ──
policy.evaluation_basis                   ← 축 ②
        ↓  calculators/rules/registry.py  build_default_registry()
PaymentDateRule / ResolutionOrContractDateRule   calculators/rules/date_rules.py
        ↓  basis_date(purchase) 가 valid_from ~ valid_to 안인가 (경계 포함)
calculate_policy_purchase()               calculators/procurement_achievement.py
        ↓  ── 여기까지가 분자 ──
achievement_rate = (분자 ÷ 분모) ÷ 목표율 × 100
        ↓
GET /dashboard/summary → 화면 정책 카드
```

### 1.5 답이 오면 고칠 곳

| 답 | 고칠 파일 | 무엇을 |
|---|---|---|
| 축 ② 를 **결의일자**로 | `database/bootstrap.py` `MVP_POLICY_SEEDS` | 일반 3정책의 `evaluation_basis` |
| 〃 | `database/bootstrap.py` `migrate_policy_evaluation_basis()` | 기존 DB 행 갱신(이미 같은 방식의 전례가 있다) |
| 〃 | `calculators/rules/date_rules.py` | 결의일자 단독 규칙이 **없다** — 새로 필요할 수 있다 |
| 〃 | `calculators/rules/registry.py` | 새 규칙 등록 |
| 축 ① 을 확정 | `.env` / 배포 설정의 `PURCHASE_PERIOD_DATE_FIELD` | 코드 변경 없이 설정만 |

⚠️ **축 ② 를 결의일자로 바꾸면 달성률 숫자가 달라진다.** 그리고 결의일자가
비어 있는 행(W-15)이 있으면 그 건들의 판정이 함께 문제가 된다.

⚠️ 현재 `date_rules.py` 에는 `PaymentDateRule` · `ContractDateRule` ·
`ResolutionOrContractDateRule` 셋뿐이다. **결의일자만 보는 규칙은 없다** —
②를 택하면 만들어야 한다.

---

## 2. 🔴 Q5-8 — 0원 · 음수 금액 행

### 2.1 현재 흐름

```
원본 행 (금액 0 또는 음수)
    ↓
uploads/validation.py  _parse_amount()
    → severity="warning" 로 표시만 한다
    → ⛔ 오류로 단정하지 않는다 (주석: "확정되지 않은 규칙을 만드는 셈")
    ↓
importers/purchase_importer.py
    ↓
database/purchase_repository.py  _validate()
    → amount <= 0 이면 PurchaseValidationError
    → **적재되지 않는다**
    ↓
importers/rejection_trace.py
    "구매금액은 0 보다 커야 합니다" → REASON_NON_POSITIVE_AMOUNT
    ↓
models/import_rejection.py
    라벨: "금액이 0 이하 (처리 방식 확인 필요)"
    ↓
GET /imports/trace · /imports/trace.csv · /imports/rejections
    → 원본 행 번호 · 사유와 함께 **보존**된다
    ↓
검토 목록 (/reviews)      → 없다 (적재되지 않았으므로)
계산 대상                  → 없다 (분모·분자 어디에도 들어가지 않는다)
```

### 2.2 정리

| 질문 | 현재 |
|---|---|
| 0원 행이 어디서 빠지는가 | **저장소 제약** — `purchase_repository._validate()` |
| 음수 행이 어디서 빠지는가 | **같은 곳** — 0원과 구분하지 않는다 |
| 사유 코드 | `NON_POSITIVE_AMOUNT` |
| trace 에 어떻게 남는가 | 행 번호 · 사유 · 원본 금액(음수도 그대로) |
| 계산에 들어가는가 | **아니오** |

⛔ 이 상태를 **"실적 제외" · "무효" · "삭제" 로 부르지 않는다.** 현재 표현은
**미적재**뿐이며, 업무상 어떻게 처리할지는 정해지지 않았다.

⚠️ **음수 상계 규칙은 확정되어 있다**(§0.6.3). 그러나 저장소가 음수를 받지
않으므로 **상계 대상이 될 행 자체가 들어오지 못한다** — 이 어긋남이 Q5-8 의
실제 내용이다.

### 2.3 답이 오면 고칠 곳

| 답 | 고칠 파일 |
|---|---|
| 음수를 받아 상계한다 | `database/purchase_repository.py` `_validate()` 제약 + 상계 처리 위치 |
| 0원을 적재한다 | 〃 |
| 지금처럼 미적재로 둔다 | **변경 없음** — 문구만 확정 표현으로 바꿀 수 있다 |

---

## 3. 🔴 Q5-9 — 예산과목 공란

### 3.1 현재 동작

```
예산과목 공란
→ 현재 시스템에서는 그대로 업로드·적재되고, 검토 목록에 보이며,
  계산 대상에 포함된다. 실적 제외 규칙에 걸리지 않는다.
→ 고객 업무규칙은 미확정
→ 이번 STEP 에서는 변경하지 않음
```

| 단계 | 현재 |
|---|---|
| 업로드 | 가능 — `uploads/format.py` 에서 `budget_account.required = False` |
| validation | 오류 아님. 주석: *"(−) 세금계산서는 실제 지출이 발생하지 않아 공란인 경우가 많다"* |
| DB 저장 | 저장됨 (`NULL` 또는 빈 문자열) |
| 검토 화면 | 보임 |
| 실적 제외 | **걸리지 않음** — `is_excluded_budget_account(None)` → `False` |
| 계산 대상 | **포함됨** |

⛔ **공란을 제외·미적재·오류 어느 것으로도 취급하지 않는다.**

### 3.2 답이 오면 고칠 곳

| 답 | 고칠 파일 |
|---|---|
| 공란도 실적에서 뺀다 | `core/performance_exclusion.py` — ⛔ 지금은 **6종 정확 일치**만 본다 |
| 공란은 적재하지 않는다 | `uploads/format.py` · `uploads/validation.py` |
| 지금처럼 둔다 | **변경 없음** |

---

## 4. 🟡 Q71-A — 실적 제외 건의 원본 행 보존

> 고객은 *"삭제한다"* 고 답했고, **원본을 남기기로 한 것은 우리 판단**이다
> (`DECISIONS.md` §0.10.3 · §0.10.8 ①).

### 4.1 현재 구조

```
purchase (원본)                 ← 지워지지 않는다
    ↓ purchase_id
purchase_review
    ├─ performance_status   INCLUDED / EXCLUDED
    ├─ exclusion_reason     EDUCATION_FEE · LECTURER_FEE
    │                       · SHORT_TERM_VEHICLE_LEASE · OTHER
    ├─ excluded_by          누가
    └─ excluded_at          언제
    ↓
purchase_review_history     ACTION_EXCLUDED / ACTION_INCLUDED
```

| 확인 항목 | 현재 |
|---|---|
| 원본 `purchase` 행 삭제 | **하지 않는다** |
| 계산에서 제외 | 된다 — `find_for_calculation()` (분모·분자 모두) |
| 검토 목록에서 확인 | 된다 — `find_for_review()` 는 제외 건도 보여 준다 |
| 사유·확정자·확정 시각 | 남는다 |
| 이력 | 남는다 |
| 되돌리기 | 담당자 확정 건은 가능. 예산과목 규칙 건은 불가(§5) |

⚠️ **고객이 말한 것은 "삭제" 이고, 시스템이 한 것은 "보존 + 계산 제외" 다.**
결과(실적에서 빠진다)는 같지만 같은 말이 아니다.

---

## 5. 🟡 Q71-B — 예산과목 6종 자동 제외 건의 되돌리기

### 5.1 현재 동작

| 항목 | 현재 |
|---|---|
| 대상 | 교육훈련비 · 사업추진경비 · 의료비 · 수도광열비 · 기타운영비 · 복리후생비 |
| 판정 | `core/performance_exclusion.py` `is_excluded_budget_account()` |
| 비교 방식 | **정확히 같은 값** — 앞뒤 공백만 제거 |
| 부분 문자열 | ⛔ 적용하지 않는다 (`교육훈련비지원` · `특별교육훈련비` 는 빠지지 않는다) |
| 적요 | 무관 — 내용과 관계없이 빠진다 |
| 되돌리기 | **불가** — 응답의 `can_reopen = false` |
| 이력 | 보존 |

⚠️ 🟢 **6종 자동 제외는 고객 확정**이다. 🟡 **되돌리지 못하게 한 것은 우리
판단**이다 — 고객이 "되돌릴 수 없어야 한다" 고 말한 적은 없다
(`DECISIONS.md` §0.10.8 ③).

⛔ 이번 STEP 에서 되돌리기 정책을 바꾸지 않았다.

---

## 6. 🟡 Q71-C — 금액 검색

### 6.1 현재 검색 대상

한 칸에서 셋을 함께 찾는다(`reviews/review_service.py` `_matches()`).

| 대상 | 방식 |
|---|---|
| 적요 | 공백 무시 부분 일치 |
| 거래처명 | 〃 |
| 사업자등록번호 | 〃 **+ 구분자 무시**(STEP 73) — `119-81-02316` 으로도 찾힌다 |

### 6.2 금액

| 항목 | 현재 |
|---|---|
| 금액 **검색** | **없다** |
| 금액 표시 | 목록에 그대로 보인다 |
| 금액 정렬 | 된다 (`SORT_KEYS` 의 `amount`) |
| 결의일자 정렬 | 된다 (오름·내림차순) |

⚠️ 고객은 *"적요 + 업체명 또는 사업자등록번호 + **금액**을 비교하여 확인한다"*
고 말했다. ⛔ 그것은 **업무 방식**을 말한 것이지 **금액 검색 기능을 요구한
것이 아니다.** 둘을 같게 취급하지 않는다.

⛔ 이번 STEP 에서 금액 검색을 추가하지 않았다.

---

## 7. 🟡 Q71-D — 지출결의서 단위 묶음

### 7.1 현재 동작

| 항목 | 현재 |
|---|---|
| 검토 단위 | **거래 건별** |
| 결의번호 컬럼 | **없다** — 🟢 고객이 "그런 번호가 없다" 고 답했다 |
| 자동 그룹핑 | **없다** |
| 같은 적요 여러 건 | 각각 별개 행으로 보인다 |
| 응답의 묶음 필드 | 없다 (`group` · `voucher_id` 등 부재) |
| 정렬 축에 묶음 | 없다 |
| 대신 제공하는 것 | 적요·거래처명·사업자등록번호 검색 + 금액 표시·정렬 + 결의일자 정렬 |

⚠️ 고객은 묶음 단위에 대해 *"내용 정리만 잘 된다면 어느 방식이 편한지
모르겠다"* 고 답했다. ⛔ **선호를 밝히지 않은 것은 요청이 아니다.**

⚠️ *"나중에는 지출 시간 순서대로 정리해야 한다"* 는 발언도 **별도 요구사항으로
확대하지 않는다** — 현재는 결의일자 정렬로 그 순서를 볼 수 있다.

---

## 8. 한눈에 — 무엇이 걸려 있는가

| 항목 | 현재 동작 | 답이 오면 바뀌는 것 | 달성률 숫자에 영향 |
|---|---|---|---|
| 🔴 **W-1-2** | 일반 3정책 = 지급일 | `evaluation_basis` · 새 규칙 | **예 — 직접** |
| 🔴 **D-24** (축 ①) | 설정 없으면 503 | 설정값 | **예 — 분모·분자 모두** |
| 🔴 **Q5-8** | 0·음수 미적재 | 저장소 제약 · 상계 경로 | 예 (적재하게 되면) |
| 🔴 **Q5-9** | 공란은 계산에 포함 | 제외 규칙 또는 적재 규칙 | 예 (빼게 되면) |
| 🟡 **Q71-A** | 원본 보존 + 계산 제외 | 원본 삭제 설계 | 아니오 (결과는 동일) |
| 🟡 **Q71-B** | 규칙 건은 되돌릴 수 없음 | 되돌리기 허용 | 예 (되돌린 건만큼) |
| 🟡 **Q71-C** | 금액 검색 없음 | 검색 기능 추가 | 아니오 |
| 🟡 **Q71-D** | 거래 건별 | 묶음 화면 | 아니오 |

**가장 먼저 받아야 하는 것은 W-1-2 다.** 달성률 숫자를 직접 바꾸고, 그 답이
없으면 기간 설정 자체가 임시값이기 때문이다.

---

## 9. 고객 확인 상태

⛔ **새 질문을 만들지 않았다.** 이미 있는 질문의 위치만 적는다.

| 항목 | 질문 문서 위치 | 상태 |
|---|---|---|
| W-1-2 | `CUSTOMER_DATA_QUESTIONS.md` **§Q-A** | 🔴 미회신 |
| Q5-8 | 〃 **§Q5-8** | 🔴 미회신 |
| Q5-9 | 〃 **§Q5-9** | 🔴 미회신 |
| Q71-A ~ Q71-D | 〃 **§Q71-A ~ §Q71-D** (STEP 76 에서 정리) | 🔴 미회신 |

⚠️ Q71-A ~ Q71-D 는 STEP 71 완료보고에만 있고 고객 질문 문서에는 **질문
형태로 없었다.** 이번에 그 자리를 만들었다 — 내용은 STEP 71 에서 이미 정리한
것이며 **새로 만든 질문이 아니다.**
