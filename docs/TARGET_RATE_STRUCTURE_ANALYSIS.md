# 목표비율 구조 분석 — 기업 × 연도 × 정책 (2026-09-02 · STEP 92)

> ⛔ **이 문서는 분석이다. 구현이 아니다.** 이번 STEP 에서 소스·DB·API·UI 를
> 변경하지 않았다. 아래는 **현재 코드가 실제로 어떻게 되어 있는가**와, 요구사항을
> 만족하려면 **무엇을 바꿔야 하는가**의 정리다.

---

## 1. 지금 `target_rate` 가 어디 있고 어떻게 쓰이는가

### 1.1 저장 위치 — `policy` 테이블 한 컬럼

`database/policy_repository.py` `CREATE_TABLE_SQL`:

```sql
CREATE TABLE IF NOT EXISTS policy (
    policy_id        INTEGER PRIMARY KEY,
    policy_code      TEXT UNIQUE NOT NULL,   -- ← 정책당 행이 하나뿐이다
    policy_name      TEXT NOT NULL,
    description      TEXT,
    is_active        BOOLEAN NOT NULL,
    evaluation_basis TEXT NOT NULL,
    target_rate      TEXT,                   -- ← 목표율. NULL 허용
    created_at       DATETIME NOT NULL,
    updated_at       DATETIME NOT NULL
)
```

| 사실 | 근거 |
|---|---|
| 목표율은 **정책 1건당 값 1개**다 | `policy_code TEXT UNIQUE` |
| **연도 축이 없다** | 테이블에 연도 컬럼이 없다 |
| **기업/기관 축이 없다** | 테이블에 대상 식별 컬럼이 없다 |
| 값은 0 초과 100 이하 | `validate_target_rate` · `TARGET_RATE_MAX = Decimal("100")` |
| `NULL` = **미설정**이며 0% 가 아니다 | `models.py` `TARGET_RATE_NOT_SET` · 화면 문구 "0% 아님" |
| 현재 실제 값 | **5개 정책 전부 `NULL`** (`target_rate=None  # D-004`) |

`TARGET_RATE_MAX = 100` 의 근거는 임의값이 아니라 **분모 구조에서 나온 상한**이다 —
구매비율을 `정책 인정액 ÷ 전체 구매액 × 100` 으로 정의하므로 100 을 넘는 목표율은
구조적으로 달성 불가다.

### 1.2 적용 위치 — ⭐ **계산기는 `target_rate` 를 읽지 않는다**

이번 분석에서 가장 중요한 사실이다.

```python
# calculators/procurement_achievement.py
def calculate_all(self, target_rates: dict[int, Decimal], period=None) -> list[...]
def calculate_achievement(self, policy_id, target_rate: Decimal, period=None) -> ...
```

계산기는 목표율을 **인자로 받는다.** `Policy.target_rate` 를 조회하지 않는다.
목표율을 DB 에서 꺼내 계산기에 넘기는 일은 **한 군데**에서만 일어난다.

```python
# dashboard/data_service.py  build_summary_from_registered_targets()
target_rates = {
    policy.policy_id: policy.target_rate
    for policy in policies
    if policy.policy_id is not None and policy.target_rate is not None
}
```

⭐ **따라서 목표율의 저장 구조를 바꿔도 계산기·판정 규칙·기간 필터는 손대지 않는다.**
바뀌는 곳은 "목표율을 어디서 읽어 오는가" 뿐이다.

### 1.3 전체 경로

```
policy.target_rate (DB)
   ↓ PolicyRepository.find_active() / find_active_with_target_rate()
   ↓ DashboardDataService.build_summary_from_registered_targets()
   ↓ {policy_id: Decimal}
   ↓ ProcurementAchievementCalculator.calculate_all(target_rates, period)
   ↓ AchievementResult.achievement_rate
   → 화면
```

### 1.4 현재 기업 식별 방식

| 개념 | 코드의 표현 | 비고 |
|---|---|---|
| 거래처(공급업체) | `Company` — `business_no` / `company_name` / `representative_name` | 인증을 붙이는 대상 |
| 구매 1건 | `Purchase` — `company_id` 로 `Company` 참조 | |
| **발주기관** | **없다** | 모델·테이블·컬럼 어디에도 없다 |

⚠️ 달성률의 분모는 `calculate_total_purchase()` = **DB 안의 모든 구매 합계**다.
즉 시스템은 **기관 하나**를 암묵적으로 전제하고 있다. `achievement_result.py` 의
주석도 이 값을 "기관 전체 구매금액" 이라 부른다.

### 1.5 현재 연도 식별 방식

```python
# app.py  _require_period(year, date_field)
PeriodFilter.for_year(year, date_field)   # date_field = "resolution_date"
```

연도는 **요청 파라미터**로 들어오고, `resolution_date` 로 기간을 자른다. 연도를
저장하는 곳은 없다 — 조회할 때마다 계산한다.

### 1.6 현재 API

| 메서드 | 경로 | 축 |
|---|---|---|
| GET | `/policies` | 정책 |
| PUT | `/policies/{policy_code}/target-rate` | 정책 **하나뿐** |

`PUT` 은 `ADMIN_API_TOKEN` 이 있어야 하며, 미설정이면 503 이다.

### 1.7 현재 UI

**목표율을 입력하는 화면이 없다.** `index.html` 의 `PUT` 요청 2건은 모두
`/reviews/...` (구매유형 확정 · 실적 제외)이며 목표율과 무관하다. 화면은 목표율을
**보여주기만** 한다 — "미설정", "목표율이 등록되지 않아 달성률을 계산하지
않았습니다(0% 아님)".

---

## 2. 요구사항과의 격차

요구사항: 목표비율을 **기업 × 연도 × 정책** 단위로 관리하고 사용자가 입력·수정한다.

### A. `Policy.target_rate` 만으로 가능한가 — ❌ **불가능**

세 가지 이유이며, 각각 독립적으로 치명적이다.

1. **연도를 담을 자리가 없다.** `policy_code` 가 UNIQUE 라 정책당 행이 하나다.
   2025년 40% 와 2026년 50% 를 동시에 가질 수 없다.
2. **덮어쓰면 과거가 바뀐다.** 2026년 목표를 50% 로 고치는 순간 2025년 달성률도
   50% 기준으로 다시 계산된다. 이미 보고한 숫자가 소급 변경된다.
3. **대상(기업/기관)을 담을 자리가 없다.** 컬럼 자체가 없다.

### B. 별도 테이블이 필요한가 — ⭕ **필요하다**

`policy` 는 "정책이 무엇인가"(코드·이름·판정기준일)를 담는 테이블이다. 목표율은
"누가, 언제, 그 정책을 얼마나 채워야 하는가"이며 **성질이 다르다.** 정책 정의와
목표 수치는 수명주기도 다르다 — 정책 정의는 거의 안 바뀌고 목표율은 해마다 바뀐다.

### C. 어떤 키가 필요한가

최소 구성:

| 컬럼 | 필요성 |
|---|---|
| 대상 식별자 | ⚠️ **무엇인지 미확정** — 아래 D |
| `year` | 필수 — 연도별 관리 요구사항 |
| `policy_id` | 필수 — 정책별 관리 요구사항 |
| `target_rate` | 필수 — 값 |

UNIQUE 제약: `(대상, year, policy_id)`.

### D. ⚠️ **"기업" 이 무엇인지 코드로 결정할 수 없다 — PM 확정 필요**

지시서 예시표는 "A기업 · B기업" 이라 적혀 있다. 그런데 현재 코드에서 `Company` 는
**거래처(공급업체)** 다 — 우리가 물건을 사 주는 쪽이다.

목표비율은 업무적으로 **"우리 기관이 중소기업 제품을 전체의 몇 % 사야 하는가"** 이므로
대상은 **사는 쪽(발주기관)** 이다. 그런데 그 발주기관을 나타내는 것이 코드에 없다.

⛔ **임의로 결정하지 않았다.** 두 해석에 따라 작업량이 자릿수 단위로 달라진다.

| 해석 | 뜻 | 필요한 작업 | 규모 |
|---|---|---|---|
| **(가) 기관 1곳** | 이 시스템은 한 기관 전용. 축은 **연도 × 정책** 뿐 | 테이블 1개 + 조회 1곳 수정 + API 1개 + 화면 1개 | **소** |
| **(나) 기관 여러 곳** | 여러 발주기관의 실적을 한 DB 에서 관리 | 위 + `institution` 테이블 + `purchase.institution_id` + 분모 분리 + 업로드·매칭·대시보드 전면 수정 | **대** |

⚠️ (나)는 목표비율만의 문제가 아니다. **분모**(`calculate_total_purchase`)가 기관별로
갈라져야 하므로 계산 구조 자체가 바뀐다.

⭐ 현재 적재된 실데이터(STEP 87, 2,161건)는 **기관 구분 컬럼이 없는 단일 기관 지출
원본**이다. 사실관계만 보면 (가)에 부합한다. ⛔ 그렇다고 (가)로 정하지 않는다 —
PM 확정 사항이다.

### E. 연도 연결

```
resolution_date.year  →  target_rate.year
```

**적절하다.** 이미 `_require_period(year, "resolution_date")` 로 연도가 들어오고
있으므로, 그 `year` 를 그대로 목표율 조회 키로 쓰면 된다. 새 개념을 만들 필요가 없다.

⚠️ 한 가지만 주의: 목표율이 **없는 연도**를 조회하면 그 정책은 "미설정" 이어야 한다.
⛔ 다른 연도 값을 끌어다 쓰거나 0 으로 대체하면 안 된다 — 현재 `NULL` 처리 원칙과
같다("0% 아님").

### F. UI 위치

현재 화면 구성:

```
1. 요약  2. 정책 달성 현황(표시 전용)  3. 데이터 상태
4. 구매실적 업로드   4-2. 기업정보 확인 방식   4-3. 업로드 이력
5. 구매유형 검토     6. 미매칭 거래처
```

제안: **「정책 달성 현황」 카드 아래에 "목표비율 관리" 카드**를 둔다. 이유는
목표율이 없어서 달성률이 안 나온다는 사실을 사용자가 **바로 그 자리에서** 보기
때문이다 — 문제와 해결 지점이 붙어 있어야 한다.

카드 구성(제안): 연도 선택 + 정책별 입력칸 + 저장.
⛔ 기존 대규모 화면 개편을 하지 않는다 — 기업정보 카드와 같은 방식으로 최소 추가.

### G. API

현행 `PUT /policies/{policy_code}/target-rate` 는 **부족하다.** 연도 축이 없어
호출할 때마다 이전 연도 값을 덮어쓴다.

제안 형태(⛔ 구현하지 않음):

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| GET | `/policy-targets?year=2026` | 그 연도의 정책별 목표율 |
| PUT | `/policy-targets/{year}/{policy_code}` | 설정·해제(`null` = 해제) |

기존 `PUT /policies/{code}/target-rate` 는 **지우지 않고 남기는 쪽**을 권한다 —
기존 테스트 다수가 이 경로를 검증하고 있고, 남겨 두어도 새 경로와 충돌하지 않는다.
(어느 쪽을 정본으로 삼을지는 구현 STEP 에서 결정.)

---

## 3. 구현 시 변경 범위 (⛔ 이번 STEP 에서 실행하지 않음)

(가) 해석 기준의 최소 변경 범위다.

| # | 무엇 | 파일 | 신규/수정 |
|---|---|---|---|
| 1 | 목표율 테이블 | `database/policy_target_repository.py` | **신규** |
| 2 | 모델 | `models/policy_target.py` | **신규** |
| 3 | 스키마 생성·마이그레이션 | `database/bootstrap.py` | 수정 |
| 4 | 목표율 조회 → 계산기 전달 | `dashboard/data_service.py` **1곳** | 수정 |
| 5 | API | `app.py` | **신규 2개** |
| 6 | 화면 | `web/static/index.html` | 카드 1개 추가 |
| 7 | **계산기** | `calculators/` | ✅ **변경 없음** |
| 8 | **판정 규칙** | `calculators/rules/` | ✅ **변경 없음** |
| 9 | **기간 필터** | `core/period.py` | ✅ **변경 없음** |
| 10 | **인증 처리** | `importers/` · `uploads/` | ✅ **변경 없음** |

⭐ **7~10 이 변경 없음인 이유**가 §1.2 다 — 계산기가 목표율을 인자로 받기 때문이다.

### 기존 테스트 영향

`target_rate` 를 언급하는 테스트 파일은 **30개**다. 다만 **`Policy.target_rate` 를
제거하지 않고 남기면** 대부분 영향이 없다. 영향이 가는 것은 `data_service` 가 목표율을
어디서 읽는지 검증하는 테스트뿐이다.

⛔ 구현 STEP 에서 기존 테스트를 지우거나 범위를 줄이지 않는다. 기대값을 바꿔야 하면
사유(①옛 규칙 기록 ②규칙 변경 ③구현 버그)를 분류해 docstring 에 적는다.

---

## 4. ⛔ 목표비율과 달성률 표시 구간을 섞지 않는다

이 둘은 **다른 것**이며, 코드도 이미 분리해 두었다.

| | 목표비율 | 달성률 표시 구간 |
|---|---|---|
| 무엇 | "전체 구매의 몇 % 를 이 정책으로 채워야 하는가" | 달성률을 화면에 어떤 색·라벨로 보일 것인가 |
| 값 | 고객이 정한다. **임의의 값** (예: 37%) | 20 / 40 / 60 / 80 / 100 |
| 어디 | `policy.target_rate` (→ 새 테이블) | `web/achievement_display.py` |
| 계산 관여 | ⭕ 분모·분자 비율의 기준 | ❌ **표시 전용** |

⛔ **목표비율을 20/40/60/80/100 중 하나로 제한하지 않는다.** 현재 코드도 그렇게
제한하고 있지 않다 — `validate_target_rate` 는 `0 < x <= 100` 만 본다.

---

## 5. 확정 필요 사항 (⚠️ 딱 하나)

> ⚠️ **목표비율의 대상이 무엇인가** — §2-D 의 (가) 기관 1곳 / (나) 기관 여러 곳.

이 하나가 정해지면 나머지는 코드 사실관계로 전부 결정된다. ⛔ 고객에게 다시 물을
사항이 아니라 **PM 이 정할 범위**다(시스템을 몇 개 기관이 쓰는가).
