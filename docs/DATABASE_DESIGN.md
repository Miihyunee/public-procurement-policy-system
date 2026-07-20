# Database Design

## Document Information

| Item | Value |
|------|------|
| Version | v1.0 |
| Status | Draft |
| Last Updated | 2026-07-20 |

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
| issued_date | DATE | Yes | 인증 시작일 |
| expired_date | DATE | Yes | 인증 종료일 |
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

### Related Tables

- Certification

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

본 문서는 논리적 데이터베이스 설계 문서이다.

컬럼(Column), 데이터 타입(Data Type), 인덱스(Index), 제약조건(Constraint)은 다음 버전(v1.1)에서 정의한다.
