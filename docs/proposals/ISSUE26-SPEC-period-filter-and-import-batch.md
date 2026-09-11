> # ⚠️ 이 문서는 **역사적 설계 문서**입니다
>
> **2026-08-11 시점의 설계안**이며, 그 뒤 구현이 진행되어 **현재 코드와 다른
> 부분이 있습니다.** 현재 확정된 구현 상태는 이 문서가 아니라
> **`docs/DECISIONS.md`** 를 정본으로 봅니다.
>
> ## 이 문서를 읽을 때 주의할 점
>
> | 문서 내 표현 | 현재 실제 상태 |
> |---|---|
> | "코드를 작성하지 않았다 · 승인 후 별도 PR" | ✅ **구현 완료** (PR #65) |
> | D-27 "미구현" · "하위 호환이 깨진다" · "별도 승인 후 적용" | ✅ **구현 완료.** `year` 미지정 = 400 |
> | "8.2 하위 호환 보장 목록" 의 `GET /dashboard/summary` 🔴 표시 | 실제로 적용됨. 기존 테스트 37건을 조정해 반영 |
> | 13장 "구현 순서 (승인 후)" | 1~4 · 6~8 · 10 구현 완료 |
> | 14장 D-28 ~ D-30 | 🟡 **PM 승인 이력 없는 번호.** 확정 결정으로 취급하지 않음 |
>
> **문서 본문은 당시 내용 그대로 보존합니다.** 설계 판단의 근거(특히 1~3장의
> 코드 실측 기록과 12장 "D-24 미확정 상태에서의 처리")를 남기기 위함이며,
> 사후에 고쳐 쓰지 않습니다.
>
> ---
> *보존 경위: PR #63 에만 존재하던 문서를 PR #65 로 옮겨 `main` 에서 유실되지
> 않도록 했습니다 (PM 승인, 2026-08-13).*

# Issue #26 — 기간 필터 · Import Batch 구현 명세 (Spec)

## 문서 정보

| 항목 | 값 |
|---|---|
| 문서 종류 | 구현 명세 (Spec) — **구현 전 승인용** |
| 작성일 | 2026-08-11 |
| 기준 커밋 | `main = afb3fc8` |
| 근거 | PM 결정 **D-23 · D-25 · D-27** 확정 |
| 구현 여부 | ❌ **코드를 작성하지 않았다.** 승인 후 별도 PR |

## 확정된 전제 (PM 결정)

| ID | 결정 |
|---|---|
| **D-23** | 회계연도 = **1/1 ~ 12/31**. 별도 회계연도 도입 안 함 |
| **D-25** | 동월 재업로드 = **대체(Replace)**. 행 단위 중복 제거 방식 사용 안 함 |
| **D-27** | 기간 미지정 API 요청 = **400 오류** |
| **D-24** | ⛔ **미확정** — 연도 귀속 날짜를 **코드에 박지 않는다** |

## 해결하려는 문제

| # | 문제 | 결과 |
|---|---|---|
| 1 | 기간 필터 부재 | **다음 연도부터 달성률이 조용히 틀린다** |
| 2 | 중복 적재 미방지 | 재업로드 시 실적이 그대로 2배가 된다 |

---

# 1. 현재 `purchase` 저장 구조 (실측)

```sql
CREATE TABLE IF NOT EXISTS purchase (
    purchase_id   INTEGER PRIMARY KEY,
    business_no   TEXT NOT NULL,
    company_id    INTEGER,           -- 매칭 후 채움. NULL 허용
    company_name  TEXT NOT NULL,
    contract_date DATE NOT NULL,
    payment_date  DATE NOT NULL,
    amount        NUMERIC NOT NULL,
    created_at    DATETIME NOT NULL,
    updated_at    DATETIME NOT NULL
)
```

| 관찰 | 내용 |
|---|---|
| **UNIQUE 제약** | ❌ 없음 |
| **인덱스** | ❌ PK 외 없음 |
| **Foreign Key** | ❌ 없음 (`company_id` 는 논리 참조) |
| 날짜 저장 | `DATE` (ISO 문자열) |
| 금액 저장 | `NUMERIC` — 저장 시 문자열, 조회 시 `Decimal` 복원 |
| 배치·기간 식별 | ❌ **없음** |

---

# 2. 현재 `PurchaseRepository` 조회 구조 (실측)

| 메서드 | SQL | 기간 조건 |
|---|---|---|
| `find_by_id(purchase_id)` | `WHERE purchase_id = ?` | — |
| `find_by_business_no(business_no)` | `WHERE business_no = ?` | — |
| **`find_all()`** | `SELECT * FROM purchase ORDER BY purchase_id` | ❌ **없음** |
| `find_unmatched()` | `WHERE company_id IS NULL` | ❌ 없음 |
| `update_company_id(...)` | `UPDATE ... WHERE purchase_id = ?` | — |
| `count()` | `SELECT COUNT(*)` | ❌ 없음 |
| `insert(purchase)` | `INSERT` | — |

## 2.1 `find_all()` 호출부 — **2곳뿐**

| 위치 | 용도 |
|---|---|
| `procurement_achievement.py:90` | `calculate_total_purchase()` — **분모** |
| `procurement_achievement.py:228` | `_sum_policy_purchase()` — **분자** |

> 🟢 **분모와 분자가 같은 메서드를 쓴다.** 여기에 기간 조건을 넣으면
> **두 값이 자동으로 같은 기간을 보게 된다.** 한쪽만 필터링되는 사고가 구조적으로 막힌다.

---

# 3. 현재 Import 흐름 (실측)

```text
호출자가 행(dict) 준비
   ↓
PurchaseImporter.import_rows(rows)
   ↓ 행마다
   ① 사업자번호 정규화
   ② 날짜 파싱 (contract_date · payment_date)
   ③ 금액 파싱
   ④ Validation
   ⑤ Company 조회 → company_id
   ⑥ PurchaseRepository.insert()
   ↓
ImportReport (IMPORTED / WARNING / FAILED, 매칭률)
```

| 관찰 | 내용 |
|---|---|
| 배치 개념 | ❌ 없음 |
| 재실행 보호 | ❌ 없음 — 같은 행을 다시 넣으면 그대로 또 저장 |
| 되돌리기 | ❌ 없음 |
| 업로드 이력 | ❌ 없음 |
| `rematch()` | ✅ 있음 (멱등) |

---

# 4. 기간 필터를 어느 계층에 둘 것인가

## 4.1 결론 — **Repository 조회 단계** (PM 지시 준수)

| 후보 계층 | 판정 |
|---|---|
| **Repository** | ✅ **채택** — SQL `WHERE` 로 처리. 계산 로직 무변경 |
| Calculator 내부 필터링 | ❌ 전 행을 읽고 파이썬에서 거름 — 누적 데이터에서 비효율 |
| DashboardDataService | ❌ 계산 후 필터링은 분모/분자가 어긋날 위험 |

## 4.2 신규 조회 메서드 (제안)

```python
def find_by_period(
    self,
    start: date,
    end: date,
    *,
    date_field: str,
) -> list[Purchase]:
    """지정 기간의 구매를 조회합니다.

    Args:
        start: 시작일(포함).
        end: 종료일(포함).
        date_field: 기간 판정에 사용할 날짜 컬럼.
            ``"payment_date"`` 또는 ``"contract_date"``.

            ⚠️ 어느 날짜로 연도를 귀속할지는 **아직 확정되지 않았다(D-24)**.
            그래서 기본값을 두지 않고 **호출자가 반드시 명시**하게 한다.
    """
```

| 설계 판단 | 이유 |
|---|---|
| `date_field` 를 **인자로** 받는다 | **D-24 를 코드에 박지 않기 위함** (12장) |
| **기본값 없음** (키워드 필수) | 무심코 한쪽으로 굳어지는 것을 막는다 |
| 허용값을 **화이트리스트**로 검증 | SQL injection 방지 (컬럼명은 바인딩 불가) |
| `find_all()` **유지** | 기존 호출부·테스트 무변경 |
| 경계 **포함** (`>= start AND <= end`) | 12/31·1/1 이 누락되지 않도록 |

> ⚠️ **컬럼명은 SQL 바인딩 파라미터로 넣을 수 없다.** 반드시 허용값 목록과 대조한 뒤
> 문자열로 조립하고, 목록 밖 값은 예외로 거부한다.

## 4.3 Calculator 확장 — **계산 공식 무변경**

PM 지시: 계산 공식 · Rule Engine · 정책별 판정 로직을 변경하지 않는다.

```text
현재:  calculate_total_purchase()                → find_all()
       _sum_policy_purchase(policy_id)           → find_all()

제안:  calculate_total_purchase(period=None)     → period 있으면 find_by_period()
       _sum_policy_purchase(policy_id, period)   → 〃
```

| 변경 내용 | 범위 |
|---|---|
| **조회 호출 한 줄**씩 교체 (2곳) | `find_all()` → 조건부 분기 |
| 합산 로직 | ❌ **무변경** |
| Rule Engine 호출 | ❌ **무변경** |
| 인증 유효기간 판정 | ❌ **무변경** |
| `_build_result` · 달성률 공식 | ❌ **무변경** |

> **`period=None` 이면 `find_all()` 을 그대로 호출한다** → 기존 동작·기존 테스트 그대로.

## 4.4 기간 표현 — 값 객체 제안

```python
@dataclass(frozen=True, kw_only=True)
class PeriodFilter:
    start: date
    end: date
    date_field: str          # D-24 미확정이므로 호출자가 지정
```

| 이유 |
|---|
| 인자 3개를 계층마다 넘기지 않고 **하나로 전달** |
| **연도 → 기간 변환을 한 곳**에서만 수행 (D-23: 1/1~12/31) |
| 향후 분기·월 조회로 확장 시 이 객체만 확장 |

```python
# D-23 확정 반영 — 회계연도 = 역년
PeriodFilter.for_year(2026, date_field=...)   # 2026-01-01 ~ 2026-12-31
```

---

# 5. `import_batch` 테이블 설계

## 5.1 스키마 (제안)

```sql
CREATE TABLE IF NOT EXISTS import_batch (
    batch_id       INTEGER PRIMARY KEY,
    file_name      TEXT NOT NULL,      -- 원본 파일명
    file_hash      TEXT,               -- 원본 파일 식별(내용 해시). NULL 허용
    period_start   DATE NOT NULL,      -- 대상 기간 시작
    period_end     DATE NOT NULL,      -- 대상 기간 종료
    uploaded_at    DATETIME NOT NULL,  -- 업로드 시각
    row_count      INTEGER NOT NULL,   -- 적재된 행 수
    total_amount   NUMERIC NOT NULL,   -- 금액 합계
    status         TEXT NOT NULL,      -- ACTIVE / SUPERSEDED
    superseded_by  INTEGER,            -- 대체한 배치 ID (status=SUPERSEDED 일 때)
    created_at     DATETIME NOT NULL,
    updated_at     DATETIME NOT NULL
)
```

| 컬럼 | PM 요구 항목 | 비고 |
|---|---|---|
| `batch_id` | ✅ batch_id | |
| `file_name` · `file_hash` | ✅ 원본 파일 식별 정보 | 해시는 **같은 파일 재업로드 감지**에 사용 |
| `period_start` · `period_end` | ✅ 대상 기간 | |
| `uploaded_at` | ✅ 업로드 시각 | |
| `row_count` | ✅ 행 수 | |
| `total_amount` | ✅ 금액 합계 | 정합성 검증용 |
| `status` | ✅ batch 상태 | |
| `superseded_by` | — | 대체 이력 추적 |

## 5.2 `status` 값 (제안)

| 값 | 의미 |
|---|---|
| `ACTIVE` | **계산에 사용되는 배치** |
| `SUPERSEDED` | 같은 기간의 새 배치로 대체됨. **계산에서 제외** |

> **2개로 한정한다.** `FAILED`·`PARTIAL` 등을 지금 만들면 쓰이지 않는 상태가 남는다.
> 필요해지면 그때 추가한다.

## 5.3 대상 기간(`period_start`/`period_end`)을 어떻게 정하는가 — ⚠️ **결정 필요**

| 안 | 내용 | 장단 |
|---|---|---|
| **가** | **업로드 시 사용자가 명시** (예: `2026-07`) | 명확·단순. 파일 내용과 어긋날 수 있음 |
| 나 | 파일 안 날짜의 최소~최대에서 **자동 도출** | 편리 / **어느 날짜 컬럼으로?** → **D-24 종속** |
| 다 | 가 + 나 대조 후 불일치 시 경고 | 안전 / 구현 복잡, D-24 종속 |

> **가안을 권장한다.** D-24 미확정 상태에서 **자동 도출은 불가능**하다
> (어느 날짜로 기간을 잡을지 정해지지 않았기 때문).
> → **D-28 (신규 결정 필요)**

---

# 6. `purchase` ↔ `import_batch` 관계

## 6.1 연결 방식

```sql
ALTER TABLE purchase ADD COLUMN batch_id INTEGER;
```

| 항목 | 방침 |
|---|---|
| 관계 | `import_batch` 1 : N `purchase` |
| **NULL 허용** | ✅ **필수** — 기존 데이터 호환 (8장) |
| Foreign Key | ❌ 걸지 않음 — **기존 설계 관행 유지**(`company_id` 도 논리 참조) |
| 인덱스 | `purchase(batch_id)` — 배치 단위 조회·대체에 필요 |

## 6.2 계산 대상 판정

```text
계산에 포함되는 purchase
  = batch_id 가 NULL 이거나
    batch_id 가 status='ACTIVE' 인 배치를 가리키는 행
```

> **`batch_id IS NULL` 을 포함하는 이유**: 배치 도입 이전에 적재된 데이터를
> 계산에서 갑자기 사라지게 만들지 않기 위함이다(8장).

---

# 7. 동월 재업로드 처리 — **D-25 대체(Replace)**

## 7.1 흐름

```text
새 파일 업로드 (대상 기간 = P)
   ↓
① 기간 P 의 ACTIVE 배치 조회
   ↓
② 있으면 → 기존 배치 정보를 먼저 보고 (건수·금액)
   ↓
③ 새 배치 생성 (status=ACTIVE) + 행 적재
   ↓
④ 기존 배치 → status=SUPERSEDED, superseded_by=새 batch_id
   ↓
⑤ 결과 리포트 (대체 전/후 건수·금액 비교)
```

## 7.2 원칙

| 원칙 | 이유 |
|---|---|
| **행을 물리 삭제하지 않는다** | 대체된 배치의 행도 남긴다. 추적·복구 가능 |
| **배치 이력을 지우지 않는다** | 언제 무엇이 대체되었는지 남아야 한다 |
| **대체 전 건수·금액을 보고**한다 | 조용히 덮어쓰지 않는다 |
| **③ 성공 후 ④ 를 수행**한다 | 새 적재가 실패하면 기존 배치가 그대로 유지된다 |

## 7.3 ⚠️ 트랜잭션 경계 — 확인 필요

현재 `BaseRepository` 는 **호출 단위로 커넥션을 열고 닫는다.**
배치 생성 → 행 적재 → 기존 배치 무효화가 **하나의 트랜잭션으로 묶이지 않는다.**

| 위험 | 중간에 실패하면 **새 배치와 기존 배치가 동시에 ACTIVE** 가 될 수 있다 |
|---|---|

| 대응 후보 | 내용 |
|---|---|
| 가 | 배치 무효화를 **마지막 단일 UPDATE** 로 수행 (실패해도 기존 배치가 살아 있음) |
| 나 | 트랜잭션을 여러 호출에 걸쳐 유지하도록 `BaseRepository` 확장 |

> **가안을 권장한다.** 나안은 기존 Repository 구조를 바꿔야 해 영향 범위가 크다.
> 가안이면 **최악의 경우에도 "기존 배치가 남는 것"** 이고, 이는 새 배치가 유실되는 것보다 안전하다.
> 다만 **같은 기간에 ACTIVE 배치가 2개 있는 상태를 검출**하는 점검이 필요하다.

## 7.4 같은 파일을 그대로 재업로드한 경우

`file_hash` 가 기존 ACTIVE 배치와 동일하면 **경고 후 사용자 확인**을 권장한다
(내용이 같은 파일을 다시 올리는 것은 대개 실수).

> ⚠️ 이 동작도 **D-28 과 함께 결정**이 필요하다.

---

# 8. 기존 데이터와의 호환

## 8.1 이미 적재된 `purchase` 행

| 상황 | 처리 |
|---|---|
| `batch_id` 가 NULL 인 행 | **계산에 계속 포함**한다 (6.2) |
| 소급해서 배치를 만들 것인가 | ❌ **하지 않는다** — 어느 기간·파일인지 알 수 없다 |

> **현재 운영 DB 에는 실제 데이터가 없다**(개발·테스트 데이터만).
> 그래도 규칙을 명확히 해 두는 이유는, 테스트 DB 나 기존 시나리오가 깨지지 않게 하기 위함이다.

## 8.2 하위 호환 보장 목록

| 대상 | 보장 |
|---|---|
| `find_all()` | ✅ 시그니처·동작 무변경 |
| `Purchase` 모델 | 🟡 `batch_id` 필드 추가 (**기본값 `None`**) |
| `PurchaseRepository.insert()` | 🟡 `batch_id` 를 함께 저장 (없으면 NULL) |
| `PurchaseImporter.import_rows()` | 🟡 배치 인자 **선택적** — 없으면 현재와 동일 동작 |
| `Calculator` 공개 메서드 | 🟡 `period` 인자 **선택적** — 기본값 `None` = 현재 동작 |
| `DashboardDataService` | 🟡 `period` 전달만 |
| `GET /dashboard/summary` | 🔴 **동작 변경** — 9장 참조 |

> **API 만 하위 호환이 깨진다.** D-27(기간 미지정 = 400) 결정에 따른 의도된 변경이다.

## 8.3 ⚠️ 하위 호환 영향 보고 (PM 지시)

> PM 지시: "기존 호출부와 하위 호환에 영향을 주는 경우 임의로 깨뜨리지 말고 보고"

**깨지는 것은 1건입니다.**

| 대상 | 현재 | 변경 후 | 근거 |
|---|---|---|---|
| `GET /dashboard/summary` (파라미터 없음) | **200** + 전 기간 합산 | **400** | **D-27 확정** |

- 영향받는 기존 테스트: `test_app.py` · `test_dashboard_api.py` · `test_e2e_scenarios.py` ·
  `test_end_to_end_chain.py` 의 `GET /dashboard/summary` 호출 (10장)
- **이는 PM 이 확정한 D-27 의 직접적 결과**이며, 임의 변경이 아닙니다
- 다른 모든 계층은 **기본값으로 기존 동작을 유지**합니다

---

# 9. Dashboard API 기간 파라미터 설계

## 9.1 요청

```http
GET /dashboard/summary?year=2026
```

| 항목 | 값 |
|---|---|
| 파라미터명 | `year` |
| 타입 | 정수 |
| 필수 여부 | **필수** (D-27) |
| 기간 변환 | `2026-01-01 ~ 2026-12-31` (**D-23**) |

## 9.2 응답

| 항목 | 방침 |
|---|---|
| **대상 기간 명시** | ✅ **필수** — 응답에 `year`(또는 기간)를 포함해, 어느 기간의 숫자인지 화면에서 알 수 있게 한다 |
| 기존 필드 | 무변경 (`total_purchase_amount` · `policies` · 직렬화 규약) |

## 9.3 오류

| 상황 | 응답 |
|---|---|
| `year` 미지정 | **400** |
| `year` 가 정수가 아님 | 422 (FastAPI 기본 검증) |
| `year` 범위 이상 (예: 1800, 3000) | ⚠️ **D-29 — 검증 범위 결정 필요** |

> **400 vs 422**: FastAPI 는 필수 쿼리 파라미터 누락을 기본적으로 **422** 로 반환한다.
> **D-27 은 "400 오류"로 확정**되었으므로, `year` 를 선택 파라미터로 선언하고
> **엔드포인트 내부에서 누락을 400 으로 변환**한다.
> (기존 전역 예외 처리 방식은 변경하지 않는다 — 엔드포인트 내부에서만 처리)

## 9.4 `date_field` 를 API 로 노출할 것인가 — ⚠️ **D-30**

D-24 가 미확정이므로 **어느 날짜로 연도를 판정할지** 정해지지 않았다.

| 안 | 내용 | 평가 |
|---|---|---|
| **가** | API 에 노출하지 않고 **설정값**으로 둔다 | 사용자가 실수할 여지 없음. **D-24 확정 시 설정만 변경** |
| 나 | API 파라미터로 받는다 | 유연하나 **호출자마다 다른 값을 쓰면 숫자가 달라진다** |

> **가안을 권장한다.** 나안은 같은 화면에서 다른 기준의 숫자가 나올 수 있다.

---

# 10. 기존 테스트 영향

## 10.1 영향받는 테스트

| 파일 | 영향 | 조치 |
|---|---|---|
| `test_app.py` | `GET /dashboard/summary` 호출 | `?year=` 추가 |
| `test_dashboard_api.py` | 서비스 계층 — `period=None` 이면 무변경 | ✅ 대부분 그대로 |
| `test_e2e_scenarios.py` | API 호출 | `?year=` 추가 |
| `test_end_to_end_chain.py` | API 호출 | `?year=` 추가 |
| `test_procurement_achievement.py` | Calculator — 기본값 `None` | ✅ 그대로 |
| `test_purchase_repository.py` | `find_all()` 무변경 | ✅ 그대로 |
| `test_purchase_importer.py` | 배치 인자 선택적 | ✅ 그대로 |
| `test_bootstrap.py` | `_REQUIRED_SCHEMA` 에 `import_batch` 추가 | 기대값 갱신 |

> **수정이 필요한 것은 "API 를 직접 호출하는 테스트"뿐**이며,
> 그것도 **쿼리 파라미터 추가**로 끝난다. 검증 내용(기대 금액·상태)은 바뀌지 않는다.

## 10.2 신규 테스트

| 영역 | 항목 |
|---|---|
| 기간 조회 | 연도 경계(12/31·1/1 포함), 기간 밖 제외, 빈 기간, `date_field` 별 결과 차이 |
| `date_field` 검증 | 허용값 외 입력 시 예외 |
| 계산 | **같은 데이터라도 연도별로 다른 달성률**이 나오는지 |
| 분모·분자 일관성 | 두 값이 **같은 기간**을 보는지 |
| 배치 | 생성·조회·상태 전이 |
| 재업로드 | 같은 기간 재업로드 시 기존 배치 `SUPERSEDED`, 계산에서 제외 |
| 정합성 | ACTIVE 배치 합 = 계산 대상 합 |
| 호환 | `batch_id IS NULL` 행이 계산에 포함되는지 |
| 회귀 | 기존 516건 (API 테스트는 파라미터 추가 후) |

---

# 11. 마이그레이션 / 기존 DB 대응

## 11.1 현재 방식 확인

프로젝트는 마이그레이션 프레임워크를 쓰지 않는다.

| 현재 방식 | 내용 |
|---|---|
| 테이블 생성 | `CREATE TABLE IF NOT EXISTS` (멱등) |
| 스키마 검증 | `bootstrap.verify_bootstrap()` 이 `_REQUIRED_SCHEMA` 로 **컬럼까지 확인** |
| 구 스키마 감지 | ✅ 이미 있음 — 컬럼이 없으면 Health Check 실패 |

> ⚠️ **`CREATE TABLE IF NOT EXISTS` 는 기존 테이블에 컬럼을 추가하지 않는다.**
> `purchase.batch_id` 추가에는 **별도 처리가 필요**하다.

## 11.2 제안

| # | 작업 |
|---|---|
| 1 | `import_batch` — `CREATE TABLE IF NOT EXISTS` (신규 테이블이라 문제 없음) |
| 2 | `purchase.batch_id` — **컬럼 존재 확인 후 없으면 `ALTER TABLE ADD COLUMN`** |
| 3 | `_REQUIRED_SCHEMA` 에 `import_batch` 와 `purchase.batch_id` 추가 |
| 4 | `init` 명령이 **기존 DB 에서도 안전하게 재실행**되는지 확인 |

> `ALTER TABLE ADD COLUMN` 은 SQLite 에서 지원되며, **NULL 허용 컬럼 추가는 안전**하다.
> 기존 행은 자동으로 `NULL` 이 되고, 이는 6.2 규칙상 **계산에 계속 포함**된다.

## 11.3 인덱스

| 인덱스 | 목적 |
|---|---|
| `purchase(payment_date)` | 기간 조회 |
| `purchase(contract_date)` | 〃 |
| `purchase(batch_id)` | 배치 단위 조회·대체 |
| `import_batch(period_start, period_end, status)` | 동기간 ACTIVE 배치 조회 |

> **누적 데이터이므로 인덱스가 필요해진다.** 다만 실제 규모를 모르므로
> **위 4개만** 두고, 추가는 실제 데이터 확인 후 판단한다.

---

# 12. D-24 미확정 상태에서의 처리 — **가장 중요한 설계 판단**

> PM 지시: "필요한 날짜를 인자로 받을 수 있는 구조로 설계하되,
> 실제 귀속 기준을 코드에 임의로 박아 넣지 않는다"

## 12.1 어떻게 지키는가

| 계층 | 처리 |
|---|---|
| **Repository** | `date_field` 를 **키워드 필수 인자**로 받는다. **기본값을 두지 않는다** |
| **Calculator** | `PeriodFilter` 안에 담긴 `date_field` 를 그대로 전달만 한다 |
| **DataService** | 그대로 전달만 |
| **API** | 노출하지 않고 **설정값**에서 읽는다 (9.4 가안) |
| **설정** | `PURCHASE_PERIOD_DATE_FIELD` — **기본값을 두지 않고, 미설정 시 오류** |

## 12.2 핵심 — **기본값을 두지 않는다**

```text
기본값을 두면:
  개발·테스트에서 그 값이 계속 쓰이고
  → 사실상 확정된 것처럼 굳어지고
  → D-24 결정 시 이미 그 값 기준으로 코드·테스트가 쌓여 있다
```

| 방침 |
|---|
| 설정이 없으면 **명확한 오류**를 내고 동작하지 않는다 |
| 오류 메시지에 **"D-24 미확정 — 운영 기준 확인 필요"** 를 명시한다 |
| 테스트는 fixture 에서 **명시적으로 주입**한다 |

> 이렇게 하면 **"어느 날짜로 연도를 나누는지 아무도 결정하지 않은 채 숫자가 나오는 상황"**
> 자체가 발생하지 않는다.

## 12.3 D-24 확정 시 변경 범위

| 확정되면 | 변경할 곳 |
|---|---|
| "모든 정책이 지급일 기준" | **설정값 1개** |
| "정책별 판정 기준일로 각각" | 🔴 Calculator 가 **정책마다 다른 기간 조회**를 해야 함 → **구조 변경 필요** |

> ⚠️ **후자면 설계가 달라진다.** 정책마다 분모가 달라지므로
> `calculate_total_purchase()` 를 정책별로 호출해야 한다.
> 지금은 **전자를 전제로 설계하되, 후자로 갈 경우의 영향을 명시**해 둔다.

---

# 13. 구현 순서 (승인 후)

| # | 작업 | 선행 |
|---|---|---|
| 1 | `PeriodFilter` 값 객체 | — |
| 2 | `PurchaseRepository.find_by_period()` + `date_field` 검증 | 1 |
| 3 | Calculator `period` 인자 (기본값 `None`) | 2 |
| 4 | `DashboardDataService` 전달 | 3 |
| 5 | API `year` 파라미터 + 400 처리 | 4 · **D-29 · D-30** |
| 6 | `import_batch` 테이블 + Repository | — |
| 7 | `purchase.batch_id` + 마이그레이션 | 6 |
| 8 | `PurchaseImporter` 배치 연결 | 7 |
| 9 | 재업로드 대체 처리 | 8 · **D-28** |
| 10 | Bootstrap 스키마 검증 갱신 | 7 |

> **1~4 는 추가 결정 없이 착수 가능**하다(D-24 는 인자로 회피).
> 5·9 는 아래 신규 결정이 필요하다.

---

# 14. 신규 PM 결정 필요

| ID | 결정 사항 | 선택지 | 권장 |
|---|---|---|---|
| **D-28** | 배치의 **대상 기간을 어떻게 정하는가** | 가: 업로드 시 명시 / 나: 파일에서 자동 도출 / 다: 둘 대조 | **가** (나·다는 **D-24 종속**이라 지금 불가) |
| **D-29** | `year` **허용 범위 검증** | 검증 안 함 / 범위 지정(예: 2020~현재+1) | 범위 지정 |
| **D-30** | `date_field` 를 **API 로 노출**할 것인가 | 가: 설정값 / 나: API 파라미터 | **가** |
| **추가** | 같은 파일(해시 동일) 재업로드 시 동작 | 경고 후 진행 / 거부 | 경고 후 진행 |

---

# 15. 이번 Spec 에서 하지 않은 것

| 항목 | 이유 |
|---|---|
| **코드 작성** | 승인 후 |
| **D-24 확정** | ⛔ 인자로 회피, 기본값 없음 |
| 실제 고객 데이터 추측 | — |
| 가상 파일 구조 확정 | — |
| Parser 세부 규칙 | 실제 파일 확보 후 |
| Collector · 외부 API | — |
| 녹색제품 · 여성기업 · 장애인표준사업장 · Group C | 보류 유지 |
| Issue #49 (음수/0 금액) | W-4 대기 |
| 목표율 입력 | 공식 근거 대기 |
| Calculator 계산 공식 · Rule Engine · 정책 판정 로직 | **PM 지시 — 변경하지 않음** |
