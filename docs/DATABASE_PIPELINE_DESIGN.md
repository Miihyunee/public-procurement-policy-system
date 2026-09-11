# DB 파이프라인 설계 — DB-1 / DB-2 / DB-3

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-22 |
| 상태 | 📄 **설계만.** ⛔ 실제 스키마를 변경하지 않았습니다 |
| 상위 문서 | `NEW_ARCHITECTURE.md` |
| 원칙 | DB-1 은 불변. 자동 분석 ≠ 담당자 확정. DB-3 은 재현 가능 |

---

# 1. 세 DB 의 관계

```text
DB-1 (원본)          DB-2 (분석 + 확정)
purchase             purchase_review
  purchase_id ◄────────ᐧ purchase_id  (1 : 0..1)
  ⛔ 수정 없음           분석 결과 | 담당자 확정
      │                        │
      └────────┬───────────────┘
               ▼
        DB-3 (계산용 스냅샷)
        final_dataset / final_purchase
        ⛔ 생성 시 DB-1 을 수정하지 않는다
```

> **"DB" 는 논리적 구분입니다.** 파일을 세 개로 나누자는 뜻이 아니라, 같은
> SQLite 안에서 **역할이 다른 테이블 묶음** 으로 두는 것을 권장합니다.
> 파일을 나누면 조인이 불가능해지고 트랜잭션 경계가 깨집니다.

---

# 2. DB-1 — Raw Data (원본)

## 2.1 현재 상태 — **이미 존재합니다**

`purchase` 테이블이 그대로 DB-1 입니다. **새로 만들 필요가 없습니다.**

| 파일 | 역할 |
|---|---|
| `database/purchase_repository.py::PurchaseRepository` | 저장·조회 |
| `models/purchase.py::Purchase` | 도메인 모델 |

## 2.2 실제 컬럼 (코드 확인 결과)

지시서의 예상 목록과 **실제 코드**를 대조했습니다. ⛔ 임의로 컬럼을 추가하지
않았습니다.

| 지시서 예상 | 실제 컬럼 | 상태 |
|---|---|---|
| `purchase_id` | `purchase_id INTEGER PRIMARY KEY` | ✅ 있음 |
| `period` | — | ❌ **없음.** 기간은 `import_batch.period_start/end` 에 배치 단위로 존재 |
| `issue_date` | `issue_date DATE` | ✅ 있음 (2026-08-20 추가, 원본 `신고기준일`) |
| `description` | `description TEXT` | ✅ 있음 (원본 `적요`) — **현재 보관만 하고 아무 데도 쓰지 않음** |
| `company_name` | `company_name TEXT NOT NULL` | ✅ 있음 |
| `business_no` | `business_no TEXT NOT NULL` | ✅ 있음 (하이픈 제거 정규화) |
| `supply_amount` | — | ❌ **없음.** 공급가액은 받지 않음 (`계` 만 사용 — `DECISIONS` §0.6.4) |
| `tax_amount` | — | ❌ **없음.** 같은 이유 |
| `total_amount` | `amount NUMERIC NOT NULL` | ✅ 있음 (이름만 다름. VAT 포함 총액) |
| `resolution_date` | `resolution_date DATE` | ✅ 있음 (결의일자 — 연도 귀속 기준일) |
| `budget_account` | `budget_account TEXT` | ✅ 있음 (2026-08-20 추가) |
| `source_file` | — | ❌ **없음.** `import_batch.file_name` 에 배치 단위로 존재 |
| `source_row` | — | ❌ **없음** |
| `created_at` | `created_at DATETIME NOT NULL` | ✅ 있음 |

**실제 전체 컬럼** (`purchase`):

```sql
purchase_id INTEGER PRIMARY KEY
business_no TEXT NOT NULL
company_id INTEGER              -- Company 매칭 결과
company_name TEXT NOT NULL
contract_date DATE NOT NULL     -- 계약일자
payment_date DATE NOT NULL      -- 지급일
resolution_date DATE            -- 결의일자
issue_date DATE                 -- 신고기준일 (세금계산서 발행일자)
description TEXT                -- 적요
budget_account TEXT             -- 예산과목
amount NUMERIC NOT NULL         -- 계 (VAT 포함)
batch_id INTEGER                -- import_batch 참조
created_at DATETIME NOT NULL
updated_at DATETIME NOT NULL
```

## 2.3 🟡 검토가 필요한 차이 3건

| # | 항목 | 현재 | 논점 |
|---|---|---|---|
| ① | `source_row` (원본 엑셀 행 번호) | **없음** | 담당자가 검토 화면에서 "원본 몇 번째 행" 을 확인하려면 필요할 수 있습니다. `import_batch` 로 파일은 알 수 있으나 행은 모릅니다 |
| ② | `공급가액` · `세액` | **받지 않음** | 원본 Excel 에는 있습니다. "원본 보존" 을 엄격히 하려면 담아야 하지만, 현재는 `계` 만 확정 사항입니다 |
| ③ | `period` | 배치 단위로만 | 행 단위 기간 컬럼은 중복이므로 **추가 불필요** 판단 |

> 🟡 **결정 대기** — ①②는 "원본 보존" 의 범위를 어디까지로 볼지에 대한 PM
> 결정 사항입니다. ⛔ 임의로 추가하지 않았습니다.

## 2.4 DB-1 불변 규칙

| 규칙 | 강제 방법 |
|---|---|
| 담당자 수정이 DB-1 에 쓰이지 않는다 | 검토 API 는 DB-2 Repository 만 주입받는다 |
| 재처리해도 원본이 바뀌지 않는다 | 재업로드는 **새 배치 + 기존 배치 SUPERSEDED** (이미 구현) |
| 삭제하지 않는다 | 물리 삭제 경로 없음 (PM-012, 이미 구현) |
| 유일한 예외 | `company_id` 채우기(`CompanyMatcher`) — 원본 값이 아니라 **매칭 결과** |

---

# 3. DB-2 — Review / Classification

## 3.1 목적

**자동 분석 결과**와 **담당자 확정 결과**를 한 행에 담되, **서로 다른 컬럼**에
둡니다. 분석을 다시 돌려도 담당자 확정값은 덮이지 않습니다.

## 3.2 제안 스키마 (신규 · 미적용)

```sql
CREATE TABLE IF NOT EXISTS purchase_review (
    review_id        INTEGER PRIMARY KEY,
    purchase_id      INTEGER NOT NULL,      -- DB-1 참조 (1:1)

    -- ── 자동 분석 결과 (분석기가 씀 · 담당자는 쓰지 않음) ──
    analysis_status  TEXT,                  -- NOT_ANALYZED | ANALYZED | FAILED
    analyzer_name    TEXT,                  -- 어떤 방법으로 분석했는가
    analyzer_version TEXT,                  -- 재현성을 위해 버전을 남긴다
    analyzed_at      DATETIME,
    candidates_json  TEXT,                  -- [{"type":"CONSTRUCTION","score":0.97}, ...]
    top_type         TEXT,                  -- 1순위 후보 (표시·정렬용)
    top_score        NUMERIC,
    is_ambiguous     INTEGER,               -- 이중 매칭 여부 (0/1)

    -- ── 담당자 확정 결과 (담당자만 씀 · 분석기는 쓰지 않음) ──
    review_status    TEXT NOT NULL,         -- PENDING | CONFIRMED | REOPENED
    final_purchase_type TEXT,               -- CONSTRUCTION | SERVICE | GOODS | NULL
    reviewed_by      TEXT,
    reviewed_at      DATETIME,
    review_note      TEXT,

    created_at       DATETIME NOT NULL,
    updated_at       DATETIME NOT NULL
)
```

### 설계 근거

| 결정 | 이유 |
|---|---|
| **`candidates_json` 을 JSON 으로** | 지시서 예시는 후보 3개 고정(`candidate_type_1~3`)이지만, 분석 방법(BM25/RAG/FUSE)에 따라 후보 수가 다릅니다. 컬럼을 고정하면 방법을 바꿀 때 스키마가 흔들립니다. `top_type`/`top_score` 는 조회 성능을 위해 **중복 보관** |
| **`analyzer_name` · `analyzer_version`** | "이 확정값이 어느 분석기의 어느 버전을 보고 내려졌는가" 를 남깁니다. 방법을 비교하려면 필수입니다 |
| **`final_purchase_type` 이 `NULL` 허용** | 담당자가 아직 안 골랐거나 "판단 불가" 로 남길 수 있어야 합니다. ⛔ 기본값을 채워 넣지 않습니다 |
| **`is_ambiguous`** | 이중 매칭(7장) 표시. 분석기가 계산하며 **확정을 대신하지 않습니다** |
| `purchase_id` 1:1 | 구매 한 건에 검토 한 건. 이력은 별도 테이블(3.3) |

⛔ **`final_purchase_type` 은 `core/purchase_type.py` 의 3값만 씁니다.** 새 분류
체계를 만들지 않습니다.

## 3.3 이력 테이블 (지시 9번)

`purchase_review` 는 **현재 상태**만 담습니다. 변경 이력은 append-only 로 따로
쌓습니다.

```sql
CREATE TABLE IF NOT EXISTS purchase_review_history (
    history_id     INTEGER PRIMARY KEY,
    purchase_id    INTEGER NOT NULL,
    changed_at     DATETIME NOT NULL,
    changed_by     TEXT,
    action         TEXT NOT NULL,   -- ANALYZED | CONFIRMED | REOPENED | NOTE
    before_type    TEXT,
    after_type     TEXT,
    note           TEXT,
    snapshot_json  TEXT             -- 그 시점 분석 후보
)
```

이 두 테이블로 지시 9번의 요구가 충족됩니다.

| 요구 | 어디서 |
|---|---|
| 원본 적요 "시설물 유지관리" | DB-1 `purchase.description` (⛔ 불변) |
| AI 분석: 용역 72% / 공사 68% | DB-2 `candidates_json` |
| 담당자 결정: 공사 | DB-2 `final_purchase_type` |
| 확정일 2026-08-22 | DB-2 `reviewed_at` |
| 확정자 | DB-2 `reviewed_by` |
| 변경 이력 | `purchase_review_history` |

## 3.4 상계 검토 확장 자리 (지시 10번)

향후 상계 후보도 같은 방식으로 담을 수 있습니다.

```sql
-- 개념만. 이번에 만들지 않음.
CREATE TABLE IF NOT EXISTS offset_review (
    offset_review_id  INTEGER PRIMARY KEY,
    negative_purchase_id INTEGER NOT NULL,
    candidate_ids_json   TEXT,     -- offsetting 이 준 후보들
    review_reason        TEXT,     -- MULTIPLE_CANDIDATES | CONTESTED_CANDIDATE
    chosen_purchase_id   INTEGER,  -- 담당자가 고른 양수 (NULL = 미확정)
    reviewed_by          TEXT,
    reviewed_at          DATETIME
)
```

⛔ **이번 작업에서 만들지 않습니다.** `core/offsetting.py` 도 수정하지 않습니다.
결정 ③·④ 확정 후 별도 승인 대상입니다.

---

# 4. DB-3 — Final Calculation Dataset

## 4.1 목적

DB-1(원본) + DB-2(확정) 를 결합한 **계산 전용 스냅샷**입니다. 계산기는 여기만
읽습니다.

## 4.2 제안 스키마 (신규 · 미적용)

```sql
CREATE TABLE IF NOT EXISTS final_dataset (
    dataset_id    INTEGER PRIMARY KEY,
    label         TEXT,                  -- "2026년 상반기 확정" 등
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    status        TEXT NOT NULL,         -- ACTIVE | SUPERSEDED
    built_at      DATETIME NOT NULL,
    built_by      TEXT,
    source_note   TEXT,                  -- 어떤 배치·검토 상태로 만들었는가
    row_count     INTEGER NOT NULL,
    total_amount  NUMERIC NOT NULL,
    superseded_by INTEGER,
    created_at    DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS final_purchase (
    final_id        INTEGER PRIMARY KEY,
    dataset_id      INTEGER NOT NULL,
    purchase_id     INTEGER NOT NULL,    -- DB-1 추적용

    -- DB-1 에서 복사 (스냅샷)
    business_no     TEXT NOT NULL,
    company_id      INTEGER,
    company_name    TEXT NOT NULL,
    contract_date   DATE NOT NULL,
    payment_date    DATE NOT NULL,
    resolution_date DATE,
    issue_date      DATE,
    description     TEXT,
    budget_account  TEXT,
    amount          NUMERIC NOT NULL,

    -- DB-2 에서 가져온 확정값
    purchase_type   TEXT,                -- 담당자 확정 (NULL = 미분류)
    type_source     TEXT,                -- REVIEWED | BUDGET_ACCOUNT | UNCLASSIFIED
    review_status   TEXT,

    -- 상계 반영 결과
    offset_status   TEXT,                -- NONE | OFFSET | NEEDS_REVIEW | UNMATCHED

    created_at      DATETIME NOT NULL
)
```

### 설계 근거

| 결정 | 이유 |
|---|---|
| **값을 복사(스냅샷)한다** | 뷰(VIEW)로 만들면 DB-1·DB-2 가 바뀔 때 **과거 계산 결과가 조용히 바뀝니다.** 보고서를 낸 뒤 숫자가 달라지면 안 됩니다 |
| `purchase_id` 를 남긴다 | 원본 추적. "이 계산에 쓰인 행이 원본 어디인가" |
| **`type_source`** | 확정값이 **담당자 판단**인지 **예산과목 자동 분류**인지 구분. 섞이면 신뢰도를 알 수 없습니다 |
| `offset_status` | 상계 결과를 값으로 남겨 계산기가 다시 판정하지 않게 합니다 |
| `status` = ACTIVE / SUPERSEDED | 기존 `import_batch` 와 **같은 패턴** — 물리 삭제 없이 대체 |

⛔ **DB-3 생성이 DB-1 을 수정하지 않습니다.** 읽기만 합니다.

## 4.3 지시서 요구 항목 대응

| 요구 | DB-3 컬럼 |
|---|---|
| 사업자번호 | `business_no` |
| 거래처명 | `company_name` |
| 금액 | `amount` |
| 적요 | `description` |
| 결의일자 | `resolution_date` |
| 예산과목 | `budget_account` |
| 구매유형 | `purchase_type` + `type_source` |
| 정책 관련 판정값 | ⚠️ **담지 않습니다** — 아래 참조 |
| 검토 상태 | `review_status` |

> ⚠️ **정책 판정값을 DB-3 에 넣지 않는 이유**: 정책 판정은 인증 유효기간과
> 기준일을 비교하는 것이라 **인증 데이터가 바뀌면 결과가 바뀝니다.** DB-3 에
> 굳혀 두면 인증을 갱신해도 반영되지 않습니다. 판정은 계산 시점에
> Rule Engine 이 하도록 **현재 구조를 유지**합니다.
>
> 또한 최종 정책 목록이 미확정(🔴 결정 ①)이라 지금 컬럼을 만들면 확정 후
> 다시 바꿔야 합니다.

---

# 5. DB-3 생성 시점 — 질문 1~4 검토 (지시 11번)

## 5.1 질문별 선택지 비교

### 질문 1 · 2 — 매번 새로 생성 vs 기존 갱신

| 방식 | 장점 | 단점 |
|---|---|---|
| **매번 새로 생성** | 과거 결과 재현 가능 · 감사 추적 · 롤백 쉬움 | 저장 공간 증가 |
| 기존 갱신 (UPSERT) | 공간 절약 | 🔴 **과거 계산 결과가 조용히 바뀐다.** 보고서를 낸 뒤 숫자가 달라짐 |

**→ 매번 새로 생성 권장.** 2,292행 × 20컬럼은 수 MB 수준이라 공간이 문제되지
않습니다. 이미 `import_batch` 에서 같은 판단(SUPERSEDED)을 내린 전례가 있습니다.

### 질문 3 — "최종 확정" 버튼 시점 생성

| 방식 | 장점 | 단점 |
|---|---|---|
| **명시적 버튼** | 담당자가 "지금 이 상태로 확정" 을 통제 · 검토 중간 상태가 계산에 새지 않음 | 버튼을 안 누르면 낡은 데이터로 계산 |
| 자동 생성 (검토 변경 시마다) | 항상 최신 | 🔴 검토 중간 상태가 계산에 반영 · 부하 |

**→ 명시적 버튼 권장.** 다만 대시보드에 **"확정 이후 N건이 변경됨"** 경고를
띄워 "버튼을 안 눌러서 낡은 데이터" 문제를 막습니다.

### 질문 4 — 버전 개념

**→ 필요합니다.** 근거:

| 상황 | 버전이 없으면 |
|---|---|
| 분기 보고 후 담당자가 분류를 수정 | 지난 분기 보고 숫자를 재현할 수 없음 |
| 분석 방법을 BM25 → RAG 로 교체 | 방법 간 결과 비교 불가 |
| 잘못 확정한 것을 되돌림 | 롤백 불가 |

## 5.2 🟡 권장안 (PM 결정 대기)

```text
담당자 검토 (DB-2 갱신)
        │
        ▼
  [최종 확정] 버튼
        │
        ▼
  DB-3 새 dataset 생성 (status=ACTIVE)
  이전 ACTIVE → SUPERSEDED
        │
        ▼
  Calculator 는 ACTIVE dataset 만 읽음
```

| 항목 | 권장 |
|---|---|
| 생성 시점 | **명시적 "최종 확정" 버튼** |
| 생성 방식 | **매번 새 dataset** (append-only) |
| 기존 dataset | **삭제하지 않고 SUPERSEDED** |
| 계산 대상 | `status = 'ACTIVE'` 인 dataset 하나 |
| 미확정 행 처리 | 🔴 **결정 대기** — 아래 |

> 이 패턴은 `import_batch` 의 ACTIVE/SUPERSEDED 와 **동일**합니다. 새 개념을
> 도입하지 않고 이미 검증된 방식을 재사용합니다.

## 5.3 🔴 결정 대기 — 미확정 행을 DB-3 에 넣을 것인가

담당자가 아직 확정하지 않은 행(`review_status = PENDING`)을 어떻게 할지에 따라
**달성률이 달라집니다.**

| 선택지 | 결과 |
|---|---|
| **(가) 포함 · `purchase_type = NULL`** | 분모에 들어감. 구매유형별 집계에서만 빠짐 |
| (나) 제외 | 🔴 분모가 줄어 **달성률이 왜곡됨** |
| (다) 미확정이 있으면 DB-3 생성 자체를 막음 | 안전하지만 2,292행을 전부 검토해야 계산 가능 |

> 🟡 **분석자 의견(결정 아님)**: **(가)** 를 권장합니다. 구매유형은 여성기업
> 이원화에만 필요하고, 나머지 정책은 유형과 무관합니다. 미확정을 빼면 관계없는
> 정책의 분모까지 흔들립니다. (다) 는 초기 도입에 현실적이지 않습니다.

---

# 6. 마이그레이션 전략 — 기존 데이터 훼손 없음

| 단계 | 내용 | 기존 영향 |
|---|---|---|
| 1 | `purchase_review` · `purchase_review_history` 테이블 **추가** | 없음 (`CREATE TABLE IF NOT EXISTS`) |
| 2 | `final_dataset` · `final_purchase` 테이블 **추가** | 없음 |
| 3 | 기존 `purchase` 행에 대한 `purchase_review` 를 `PENDING` 으로 생성 | DB-1 무변경 |
| 4 | Calculator 입력을 DB-3 으로 교체 | 🔴 여기서만 숫자가 바뀜 |

⛔ **1~3 단계는 기존 계산 결과를 전혀 바꾸지 않습니다.** `purchase` 테이블에
컬럼을 추가하지도, 값을 고치지도 않습니다.

`database/bootstrap.py` 의 `init_db()` 에 새 Repository 의 `create_table()` 을
추가하고, `_REQUIRED_SCHEMA` 에 새 테이블을 등록하면 됩니다 — 기존 패턴 그대로.

---

# 7. 테스트 계획 (지시 15번 · 설계만)

## 7.1 DB-1

| # | 항목 | 기대 |
|---|---|---|
| 1 | 원본 데이터 저장 | 9컬럼 값이 변형 없이 저장 |
| 2 | 원본 값 보존 | 검토 API 호출 후에도 `purchase` 행이 동일 |
| 3 | 재처리 시 원본 훼손 방지 | 재업로드 → 새 배치 + SUPERSEDED. 기존 행 삭제 없음 |
| 4 | DB-3 생성이 DB-1 을 수정하지 않음 | 생성 전후 `purchase` 전체 해시 동일 |

## 7.2 분석

| # | 항목 | 기대 |
|---|---|---|
| 5 | 단일 후보 | 후보 1개 · `is_ambiguous = False` |
| 6 | 복수 후보 (이중 매칭) | 후보 2개 이상 · `is_ambiguous = True` · **자동 확정 안 함** |
| 7 | 낮은 유사도 | 후보는 만들되 점수가 낮게 · 확정하지 않음 |
| 8 | 미분류 | 후보 0개 → `top_type = NULL` |
| 9 | 동일·유사 적요 | 같은 입력 → 같은 결과 (결정적) |
| 10 | 재분석 | 담당자 확정값(`final_purchase_type`)이 **덮이지 않음** |

## 7.3 담당자 검토

| # | 항목 | 기대 |
|---|---|---|
| 11 | 후보 선택 | `final_purchase_type` 이 선택값으로 |
| 12 | 최종 확정 | `review_status = CONFIRMED` · `reviewed_by/at` 기록 |
| 13 | 수정 | 이전 값이 `purchase_review_history` 에 남음 |
| 14 | 재검토 | `REOPENED` 후 다시 확정 가능 |
| 15 | 검토 이력 | 변경 순서대로 조회 가능 |
| 16 | ⛔ 원본 수정 불가 | 검토 API 가 `purchase` 를 쓰지 않음 (AST 검사) |

## 7.4 DB-3

| # | 항목 | 기대 |
|---|---|---|
| 17 | DB-1 + DB-2 결합 | 행 수 일치 · 값 일치 |
| 18 | 원본과 확정값 구분 | `description` 은 원본 · `purchase_type` 은 확정 · `type_source` 로 출처 구분 |
| 19 | 미확정 데이터 처리 | 🔴 결정 5.3 확정 후 |
| 20 | 재생성 | 새 `dataset_id` · 이전은 SUPERSEDED |
| 21 | 버전 관리 | 과거 dataset 으로 과거 결과 재현 |

## 7.5 계산

| # | 항목 | 기대 |
|---|---|---|
| 22 | DB-3 만 사용 | 계산기가 `purchase` 를 직접 읽지 않음 (AST 검사) |
| 23 | DB-2 변경 반영 | 검토 수정 → 재확정 → 계산 결과 변화 |
| 24 | DB-1 변경 반영 | 재업로드 → 재확정 → 계산 결과 변화 |
| 25 | 확정 전 변경은 미반영 | 버튼을 누르기 전에는 기존 결과 유지 |

---

# 8. 이번 작업에서 하지 않은 것

| 금지 항목 | 준수 |
|---|---|
| DB 스키마 실제 변경 | ✅ 하지 않음 |
| Calculator · Repository · 상계 로직 수정 | ✅ 하지 않음 |
| 기존 테스트 삭제·수정 | ✅ 하지 않음 |
| 컬럼 임의 추가 | ✅ 실제 코드 확인 후 차이만 §2.3 에 **결정 대기**로 표시 |
| 고객 미확정 사항 결정 | ✅ 🔴 로 표시 |
