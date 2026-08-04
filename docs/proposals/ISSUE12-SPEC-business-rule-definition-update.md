# Issue #12 — Business Rule Definition Update (명세서 / SPEC)

## 문서 정보

| 항목 | 내용 |
|------|------|
| Version | v1.0 (명세 초안 / Draft) |
| Status | PM 검토 대기 |
| Last Updated | 2026-08-04 |
| 유형 | **문서 정의(Definition) Issue** — 코드/스키마 구현 없음 |
| 선행 | #10 (Calculator, 완료), #11 (분석·설계, 승인) |

> 본 문서는 Issue #12 의 **명세서**이다. 구현(문서 개정 작업)은 PM 승인 후 진행한다.

---

# Background

- Issue #10 에서 Calculator 계층을 구축했으나, 인증 유효기간을 반영하지 않아 `POLICY_DEFINITION.md` 공통 계산 원칙과 어긋난다.
- Issue #11 분석에서 (1) 유효기간 미적용, (2) 정책별 판정 기준 차이, (3) Purchase 데이터 구조 한계를 확인했고 PM 이 방향을 확정했다.
- Issue #12 는 그 확정 방향을 **설계 문서에 반영**하여, 이후 구현 Issue(#13~)가 참조할 **기준 문서(정본)** 를 만든다.

## PM 확정 방향 (Issue #11 승인 시)

1. 시스템 기준은 **실제 기관 업무 기준을 우선**한다. `POLICY_DEFINITION.md` 의 단순 "구매일 기준" 은 수정한다.
2. 정책별 평가 기준은 **데이터 구조(Policy 컬럼)로 관리**할 수 있도록 설계한다.
3. Purchase 날짜 구조를 단일 `purchase_date` 에서 **`payment_date` / `contract_date`** 로 확장한다.
4. **자활용사촌** 및 **녹색제품 제품 기준** 은 MVP 범위에서 제외하고 **별도 Issue** 로 분리한다.

---

# Objectives

Issue #12 의 목표는 **문서 정의를 실제 업무 기준으로 갱신**하는 것이다.

- `POLICY_DEFINITION.md` 에 정책별 판정 기준을 명시한다.
- `DATABASE_DESIGN.md` 에 Purchase/Policy 목표 스키마를 반영한다.
- Purchase/Policy 변경 방향을 문서로 확정한다.
- #13 이후 구현 Issue 연결 구조를 정의한다.

**이번 Issue 에서는 코드·실제 스키마(CREATE TABLE)를 구현하지 않는다.**

---

# Scope — Issue #12 가 수행하는 작업

## 1. POLICY_DEFINITION.md 개정

### 1-1. 공통 계산 원칙 수정

기존(정본에서 수정 대상):
> - 구매일을 기준으로 인증 여부를 판단한다.

개정 방향:
> - 정책별 **판정 기준일**을 기준으로 인증 유효 여부를 판단한다.
> - 판정 기준일이 인증 유효기간(`valid_from` ~ `valid_to`) 내에 있어야 실적으로 인정한다.
> - 유효기간 경계는 **포함(inclusive)** 한다: `valid_from <= 판정기준일 <= valid_to`.

### 1-2. 정책별 판정 기준 표 추가 (MVP)

| 정책 | 판정 기준일 | 평가 기준(evaluation_basis) | 비고 |
|------|-------------|-----------------------------|------|
| 중소기업 | 대금 지급일 | `PAYMENT_DATE` | |
| 여성기업 | 대금 지급일 | `PAYMENT_DATE` | |
| 장애인기업 | 대금 지급일 | `PAYMENT_DATE` | |
| 창업기업 | 계약일 | `CONTRACT_DATE` | |
| 녹색제품 | 대금 지급일 (기업 기준) | `PAYMENT_DATE` | 제품 기준 판정은 MVP 제외 |

### 1-3. MVP 제외 항목 명시

- **자활용사촌**: 기간 무관·거래 유무·품목 갯수·금액 집계 → 별도 Issue (향후)
- **녹색제품 제품 기준**: 품목(Item) 단위 판정 → 별도 Issue (향후)

## 2. DATABASE_DESIGN.md 개정

### 2-1. Purchase 테이블 (목표 스키마)

| Column | Type | Required | Description | 변경 |
|--------|------|----------|-------------|------|
| purchase_id | INTEGER | Yes | PK | 유지 |
| business_no | TEXT | Yes | 사업자등록번호 | 유지 |
| company_id | INTEGER | No | Company 참조 | 유지 |
| company_name | TEXT | Yes | 공급업체명 | 유지 |
| **contract_date** | DATE | Yes | 계약일 (창업기업 판정) | **추가** |
| **payment_date** | DATE | Yes | 대금 지급일/지출완료 (일반 정책 판정) | **추가** |
| amount | NUMERIC | Yes | 구매금액 | 유지 |
| created_at | DATETIME | Yes | 생성일시 | 유지 |
| updated_at | DATETIME | Yes | 수정일시 | 유지 |

**`purchase_date` 처리 (확정)**: `purchase_date` 를 `payment_date` 로 **의미 확정(대체)** 하고 `contract_date` 를 신규 추가한다. (Issue #11 권고 B-1)

> `item_count`(품목 수) 는 **자활용사촌 전용**이므로 이번 MVP 개정에 포함하지 않는다. 자활용사촌 별도 Issue 에서 추가한다.

### 2-2. Policy 테이블 (목표 스키마)

| Column | Type | Required | Description | 변경 |
|--------|------|----------|-------------|------|
| policy_id | INTEGER | Yes | PK | 유지 |
| policy_code | TEXT | Yes | 정책 코드 (Unique) | 유지 |
| policy_name | TEXT | Yes | 정책명 | 유지 |
| description | TEXT | No | 설명 | 유지 |
| is_active | BOOLEAN | Yes | 사용 여부 | 유지 |
| **evaluation_basis** | TEXT | Yes | 판정 기준일 유형 | **추가** |

**`evaluation_basis` 허용 값 (MVP)**

| 값 | 의미 | MVP |
|----|------|-----|
| `PAYMENT_DATE` | 지급일이 유효기간 내 | ✅ |
| `CONTRACT_DATE` | 계약일이 유효기간 내 | ✅ |
| `VENDOR_EXISTENCE` | 기간 무관, 거래 유무 + 품목·금액 | 예약(향후 자활용사촌) |

> MVP 에서는 `PAYMENT_DATE` / `CONTRACT_DATE` 두 값만 실제 사용한다. `VENDOR_EXISTENCE` 는 값만 예약하고 구현은 향후 Issue.

## 3. Purchase/Policy 변경 방향 확정 (요약)

- Purchase: `purchase_date` → `payment_date` 대체 + `contract_date` 추가. `item_count` 미포함(향후).
- Policy: `evaluation_basis` 추가. MVP 값은 2종.

## 4. 향후 구현 Issue 연결 구조 (#13 이후)

| Issue | 제목 | 범위 | 선행 |
|-------|------|------|------|
| **#13** | Purchase 날짜 구조 개정 구현 | `purchase_date`→`payment_date`, `contract_date` 추가 (Model · Repository · 테스트) | #12 |
| **#14** | Policy evaluation_basis 구현 | `evaluation_basis` 컬럼 추가 (Model · Repository · 시드 매핑 · 테스트) | #12, #13 |
| **#15** | Calculator 판정 기준 적용 | 유효기간 판정 + `evaluation_basis` 별 지급일/계약일 분기 | #13, #14 |
| **(별도 트랙 A)** | 자활용사촌 유형 지원 | REQUIREMENTS/POLICY_DEFINITION 범위 개정 → `item_count`·`VENDOR_EXISTENCE` 집계 | #14, #15 |
| **(별도 트랙 B)** | 녹색제품 제품 기준 | 품목(Item) 데이터 모델 설계 | 별도 설계 |

---

# Out of Scope (Issue #12)

- 코드 구현 (Model / Repository / Calculator) — #13 이후
- 실제 스키마(CREATE TABLE) 변경 — #13 이후
- 데이터 마이그레이션 — #13 에서 다룸
- 자활용사촌 유형 (`item_count`, `VENDOR_EXISTENCE` 구현)
- 녹색제품 제품 기준 (품목 모델)
- Dashboard / API / UI / 목표율 DB 관리

---

# Deliverables (Issue #12)

1. **POLICY_DEFINITION.md 개정본** — 정책별 판정 기준·유효기간 원칙·MVP 제외 항목 반영
2. **DATABASE_DESIGN.md 개정본** — Purchase(`payment_date`,`contract_date`) / Policy(`evaluation_basis`) 목표 스키마 반영
3. **구현 Issue 연결 구조** — #13~#15 및 별도 트랙 정의 (본 명세의 Scope 4 를 정본화)

---

# Acceptance Criteria

## 문서
- [ ] POLICY_DEFINITION.md 공통 계산 원칙이 정책별 판정 기준일로 수정됨
- [ ] 정책별 판정 기준 표(중소·여성·장애인=지급일 / 창업=계약일 / 녹색=기업기준)가 추가됨
- [ ] 유효기간 경계 포함(inclusive) 규칙이 명시됨
- [ ] MVP 제외 항목(자활용사촌·녹색 제품기준)이 명시됨

## 설계
- [ ] DATABASE_DESIGN.md Purchase 에 `payment_date`/`contract_date` 반영 (`purchase_date` 대체 명시)
- [ ] DATABASE_DESIGN.md Policy 에 `evaluation_basis` 및 허용값 반영
- [ ] `item_count` 는 MVP 제외로 표기

## 연결
- [ ] #13~#15 구현 Issue 연결 구조 확정
- [ ] 별도 트랙(자활용사촌·녹색 제품기준) 분리 명시

## 구현 금지 (이번 Issue)
- [ ] 코드 변경 없음 (Model/Repository/Calculator)
- [ ] 실제 CREATE TABLE 변경 없음

---

# 명세 확정 시 잠그는 결정사항 (PM 확인)

| # | 결정 | 명세 기본값 |
|---|------|-------------|
| D1 | `purchase_date` 처리 | `payment_date` 로 대체 + `contract_date` 신규 (B-1) |
| D2 | `evaluation_basis` MVP 값 | `PAYMENT_DATE`, `CONTRACT_DATE` 2종 (VENDOR_EXISTENCE 예약) |
| D3 | 유효기간 경계 | inclusive (`<=`) |
| D4 | `item_count` | MVP 제외, 자활용사촌 Issue 로 |
| D5 | 녹색제품 | MVP 는 기업 기준(`PAYMENT_DATE`), 제품 기준은 별도 트랙 |
| D6 | 기존 저장 데이터 마이그레이션 | 현재 실데이터 없음 가정 → #13 에서 신규 스키마로 재생성. 실데이터 있으면 별도 논의 |

---

# Dependencies

- Issue #10 — Calculator Foundation (완료)
- Issue #11 — Business Rule Analysis (승인)

# Priority

High (후속 구현 Issue 의 기준 문서)

# Estimated Effort

S (문서 작업, 약 1~2시간)
