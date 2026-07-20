# System Architecture

## Document Information

| Item | Value |
|------|------|
| Version | v1.0 |
| Status | Draft |
| Last Updated | 2026-07-20 |

---

# Purpose

이 문서는 Public Procurement Policy System의 전체 시스템 구조를 정의한다.

모든 개발자는 본 문서를 기준으로 시스템을 설계하고 구현한다.

---

# High Level Architecture

```text
                   Government Data Sources
                             │
      ┌──────────────────────┼──────────────────────┐
      │                      │                      │
      ▼                      ▼                      ▼
 중소기업                 여성기업              녹색제품
 장애인기업               창업기업              기타 인증
      │                      │                      │
      └──────────────────────┼──────────────────────┘
                             ▼
                    Data Connector Layer
                             │
                             ▼
                      SQLite Database
                             │
         ┌───────────────────┼────────────────────┐
         ▼                   ▼                    ▼
 Purchase Import      Certification Engine   Company Search
         │                   │                    │
         └───────────────────┼────────────────────┘
                             ▼
                    Calculation Engine
                             │
         ┌───────────────────┼────────────────────┐
         ▼                   ▼                    ▼
      Dashboard          Excel Report        Audit Report
```

---

# System Components

## 1. Data Connector Layer

역할

- 정부기관 데이터 수집
- 데이터 다운로드
- 데이터 정규화
- 변경 여부 확인

입력

- 공공데이터
- API
- XLSX
- CSV

출력

- SQLite

---

## 2. SQLite Database

역할

모든 기업 정보와 인증정보를 저장한다.

예정 테이블

- Company
- Certification
- Purchase
- Policy
- Dataset
- History

---

## 3. Purchase Import

역할

기관의 구매실적 Excel을 업로드한다.

예상 입력

- Excel
- CSV

출력

Purchase Table

---

## 4. Calculation Engine

역할

정책별 구매실적을 자동 계산한다.

계산 대상

- 중소기업
- 여성기업
- 장애인기업
- 창업기업
- 녹색제품

---

## 5. Dashboard

역할

사용자가 실적을 확인한다.

예상 기능

- 달성률
- 통계
- 검색
- 그래프

---

## Data Flow

```text
정부기관

↓

Connector

↓

SQLite

↓

Purchase Upload

↓

Calculation Engine

↓

Dashboard

↓

Excel Report
```

---

# Development Principles

- Python 사용
- SQLite 사용
- GitHub 기반 개발
- GitHub Actions 자동 실행
- 모든 데이터는 가능한 자동 수집
- 데이터 변경 이력 관리
- 모듈 구조 개발
- Connector 기반 구조

---

# Future Expansion

향후 추가 예정

- PostgreSQL 지원
- REST API
- 사용자 로그인
- 기관별 관리
- AI 기반 질의응답
- 자동 보고서 생성
