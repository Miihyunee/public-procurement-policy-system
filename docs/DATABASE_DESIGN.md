# Database Design

## Document Information

| Item | Value |
|------|------|
| Version | v1.2 |
| Status | Draft |
| Last Updated | 2026-08-12 |

---

# Purpose

본 문서는 Public Procurement Policy System에서 사용하는 데이터베이스의 논리적 구조(Logical Database Design)를 정의한다.

데이터베이스는 기업정보, 인증정보, 구매내역, 정책정보 및 시스템 운영 데이터를 저장하며, 모든 데이터는 SQLite를 기준으로 설계한다.

---

# Database Overview

시스템은 다음과 같은 핵심 데이터를 관리한다.

- 기업 기본정보
- 정책 인증정보
- 기관 구매내역
- 정책 정보
- 데이터셋 관리
- 계산 근거(Audit)

---

# Database Tables

| Table | Description |
|--------|-------------|
| Company | 기업 기본정보 |
| Certification | 기업 인증정보 |
| Purchase | 기관 구매내역 |
| Policy | 우선구매 정책 정보 |
| Dataset | 수집 데이터셋 관리 |
| AuditLog | 계산 및 변경 이력 |

---

# Table Design

## Company

### Purpose

기업의 기본 정보를 관리한다.

모든 인증정보와 구매내역은 Company를 기준으로 연결된다.

### Primary Key

- company_id

### Unique Key

- business_no

### Related Tables

- Certification
- Purchase

### Columns

| Column | Type | Required | Description |
|---------|------|----------|-------------|
| company_id | INTEGER | Yes | 내부 고유 ID (Primary Key) |
| business_no | TEXT | Yes | 사업자등록번호 (Unique) |
| company_name | TEXT | Yes | 기업명 |
| representative_name | TEXT | Yes | 대표자명 |
| created_at | DATETIME | Yes | 데이터 생성일시 |
| updated_at | DATETIME | Yes | 데이터 최종 수정일시 |

---

## Certification

### Purpose

기업이 보유한 정책 인증 정보를 관리한다.

하나의 기업은 여러 개의 인증을 보유할 수 있다.

### Primary Key

- certification_id

### Related Tables

- Company
- Policy

### Columns

| Column | Type | Required | Description |
|---------|------|----------|-------------|
| certification_id | INTEGER | Yes | 내부 고유 ID (Primary Key) |
| company_id | INTEGER | Yes | Company 테이블 참조 |
| policy_id | INTEGER | Yes | Policy 테이블 참조 |
| certificate_number | TEXT | No | 인증서 번호 |
| valid_from | DATE | Yes | 인증 시작일 |
| valid_to | DATE | Yes | 인증 종료일 |
| issuing_agency | TEXT | No | 발급기관 |
| created_at | DATETIME | Yes | 데이터 생성일시 |
| updated_at | DATETIME | Yes | 데이터 최종 수정일시 |

---

## Purchase

### Purpose

기관의 구매실적을 저장한다.

구매내역은 기업과 연결되어 정책별 실적 계산에 사용된다.

### Primary Key

- purchase_id

### Related Tables

- Company
- ImportBatch

### Columns

| Column | Type | Required | Description |
|---------|------|----------|-------------|
| purchase_id | INTEGER | Yes | 내부 고유 ID (Primary Key) |
| business_no | TEXT | Yes | 사업자등록번호 |
| company_id | INTEGER | No | Company 테이블 참조 (매칭 후 저장) |
| company_name | TEXT | Yes | 공급업체명 |
| contract_date | DATE | Yes | 계약일 (창업기업 판정 기준일) |
| payment_date | DATE | Yes | 대금 지급일(지출완료) (일반 정책 판정 기준일) |
| amount | NUMERIC | Yes | 구매금액 |
| batch_id | INTEGER | No | ImportBatch 테이블 참조 (업로드 단위) |
| created_at | DATETIME | Yes | 데이터 생성일시 |
| updated_at | DATETIME | Yes | 데이터 최종 수정일시 |

> `batch_id`는 월별 누적 적재를 위해 v1.2 에서 추가하였다. **NULL 을 허용**하며,
> NULL 인 행(배치 도입 이전 데이터)은 **계산에 계속 포함**된다.
> 인덱스: `idx_purchase_batch (batch_id)`

> `purchase_date`(단일 구매일)는 판정 기준 이원화를 위해 제거하고 `payment_date`로 대체하였다.
> `contract_date`는 창업기업(계약일 기준) 판정을 위해 신규 추가하였다. (Issue #12)
> 품목 수(`item_count`)는 자활용사촌 등 향후 정책에서 사용하므로 MVP 범위에 포함하지 않는다.

---

## ImportBatch

### Purpose

**한 번의 업로드 단위**를 저장한다.

매월 데이터를 누적으로 올리는 운영 방식에서, 같은 기간을 다시 올렸을 때
이전 업로드를 **대체**하기 위해 사용한다(D-25). 행을 물리적으로 삭제하지 않고
상태로만 구분하므로, 무엇이 언제 대체되었는지 추적할 수 있다.

### Primary Key

- batch_id

### Related Tables

- Purchase (1 : N, 논리 참조)

### Columns

| Column | Type | Required | Description |
|---------|------|----------|-------------|
| batch_id | INTEGER | Yes | 내부 고유 ID (Primary Key) |
| file_name | TEXT | Yes | 원본 파일명 |
| file_hash | TEXT | No | 원본 파일 내용 해시 (같은 파일 재업로드 감지용) |
| period_start | DATE | Yes | 대상 기간 시작일 |
| period_end | DATE | Yes | 대상 기간 종료일 |
| uploaded_at | DATETIME | Yes | 업로드 시각 |
| row_count | INTEGER | Yes | 적재된 행 수 |
| total_amount | NUMERIC | Yes | 적재된 금액 합계 |
| status | TEXT | Yes | `ACTIVE` / `SUPERSEDED` |
| superseded_by | INTEGER | No | 이 배치를 대체한 배치 ID |
| created_at | DATETIME | Yes | 데이터 생성일시 |
| updated_at | DATETIME | Yes | 데이터 최종 수정일시 |

인덱스: `idx_import_batch_period (period_start, period_end, status)`

### 계산 대상 판정

```text
계산에 포함되는 purchase
  = batch_id 가 NULL 이거나
    batch_id 가 status='ACTIVE' 인 배치를 가리키는 행
```

> `status` 값은 **2개로 한정**한다. `FAILED`·`PARTIAL` 등은 실제로 필요해질 때 추가한다.
>
> `period_start` / `period_end` 는 **호출자가 지정**한다. 파일 내용에서 자동으로
> 유추하지 않는다 — 어느 날짜 컬럼으로 기간을 잡을지가 **D-24 (미확정)** 에
> 종속되기 때문이다.

---

## Policy

### Purpose

시스템에서 지원하는 우선구매 정책 정보를 관리한다.

예)

- 중소기업
- 여성기업
- 장애인기업
- 창업기업
- 녹색제품

### Primary Key

- policy_id

### Unique Key

- policy_code

### Related Tables

- Certification

### Columns

| Column | Type | Required | Description |
|---------|------|----------|-------------|
| policy_id | INTEGER | Yes | 내부 고유 ID (Primary Key) |
| policy_code | TEXT | Yes | 정책 코드 (Unique) |
| policy_name | TEXT | Yes | 정책명 |
| description | TEXT | No | 정책 설명 |
| is_active | BOOLEAN | Yes | 사용 여부 |
| evaluation_basis | TEXT | Yes | 판정 기준일 유형 (PAYMENT_DATE / CONTRACT_DATE) |
| target_rate | TEXT | No | 목표 구매비율(%) . 미설정 시 NULL. 값이 있으면 0 보다 커야 함 |
| created_at | DATETIME | Yes | 데이터 생성일시 |
| updated_at | DATETIME | Yes | 데이터 최종 수정일시 |

### target_rate (목표율)

정책별 목표 구매비율(%)을 시스템에서 관리하기 위한 컬럼이다. (명세: `docs/DECISIONS.md`)

- **NULL 허용**: 정책 등록 후 목표율을 나중에 보완할 수 있도록 선택 항목으로 둔다.
- **저장 형식**: Decimal 정밀도 보존을 위해 문자열(TEXT)로 저장한다(금액 저장 규약과 동일).
- **제약**: 값이 있으면 0 보다 커야 한다(Calculator 의 목표율 > 0 규칙과 정합).
- **미설정 정책 처리**: Dashboard 계산에서는 목표율이 없는 정책을 대상에서 제외한다(향후 조회 기능, #20-2).
- **연도별 이력**: MVP 범위에서는 다루지 않으며, 필요 시 별도 History 테이블로 확장한다.

### evaluation_basis 허용 값

정책별 판정 기준일 유형을 데이터로 관리하여, 계산 로직(Calculator)이 정책 코드를
하드코딩하지 않고 이 값에 따라 분기하도록 한다.

| 값 | 의미 | 적용 정책 (MVP) |
|-----|------|-----------------|
| PAYMENT_DATE | 대금 지급일이 인증 유효기간 내 | 중소기업, 여성기업, 장애인기업, 녹색제품 |
| CONTRACT_DATE | 계약일이 인증 유효기간 내 | 창업기업 |

> MVP에서는 위 두 값만 사용한다.
> 자활용사촌(기간 무관·거래 유무·품목 기준)을 위한 `VENDOR_EXISTENCE` 유형은 향후 정책 확장 시 정의한다.

---

## Dataset

### Purpose

정부기관에서 수집한 원본 데이터셋의 정보를 관리한다.

수집일, 버전, 출처 등을 기록한다.

### Primary Key

- dataset_id

---

## AuditLog

### Purpose

정책 계산 결과 및 데이터 변경 이력을 기록한다.

감사 대응 및 추적성을 확보하기 위한 테이블이다.

### Primary Key

- audit_id

---

# Entity Relationship

```text
Company
   │
   ├──────────────┐
   │              │
   ▼              ▼
Certification   Purchase
      │
      ▼
    Policy

Dataset

AuditLog
```

---

# Design Principles

- 사업자등록번호를 기준으로 기업을 식별한다.
- 하나의 기업은 여러 개의 인증을 가질 수 있다.
- 하나의 기업은 여러 건의 구매내역을 가질 수 있다.
- 인증은 정책과 연결된다.
- 계산 결과는 AuditLog를 통해 추적 가능해야 한다.
- 데이터셋은 수집 이력을 관리한다.

---

# Notes

본 문서는 Public Procurement Policy System의 논리적 데이터베이스 설계 문서이다.

Company, Certification, Purchase, Policy 테이블의 기본 컬럼은 정의되었으며, 구현 시 기준 설계로 사용한다.

## v1.1 변경 이력 (Issue #12)

실제 기관 업무 기준(정책별 판정 기준일)을 반영하기 위해 다음을 개정하였다.

- Purchase: `purchase_date` 제거 → `payment_date`(대금 지급일), `contract_date`(계약일) 추가
- Policy: `evaluation_basis`(판정 기준일 유형) 추가 — MVP 값은 `PAYMENT_DATE`, `CONTRACT_DATE`

판정 기준일과 유효기간 판정 규칙은 `docs/POLICY_DEFINITION.md`를 정본으로 한다.
컬럼의 실제 스키마 반영(Model/Repository)은 Issue #13 이후에서 구현한다.

인덱스(Index), 외래키(Foreign Key), 상세 제약조건(Constraint) 및 Dataset, AuditLog의 컬럼 정의는 다음 버전에서 보완한다.

## v1.2 변경 이력 (Issue #26 — 기간 필터 · 월별 누적 적재)

매월 데이터를 누적으로 올리는 운영 방식을 지원하기 위해 다음을 개정하였다.

- **ImportBatch 테이블 신규 추가** — 업로드 단위, 대상 기간, 상태(`ACTIVE`/`SUPERSEDED`)
- Purchase: `batch_id`(NULL 허용) 추가 + 인덱스 `idx_purchase_batch`
- 계산 조회는 `find_for_calculation()` 을 사용하며, 대체된 배치의 행을 제외한다
- 기존 `find_all()` 의 동작은 **변경하지 않았다**(하위 호환)

Foreign Key 제약은 걸지 않는다. `purchase.company_id` 와 같은 기존 방식(논리 참조)을 유지한다.
