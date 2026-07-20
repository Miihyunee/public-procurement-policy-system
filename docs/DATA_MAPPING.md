# Data Mapping

## Purpose

본 문서는 각 데이터 소스의 컬럼을 시스템 표준 컬럼으로 매핑하는 규칙을 정의한다.

---

# Standard Keys

| Standard Column | Description |
|-----------------|-------------|
| business_no | 사업자등록번호 |
| company_name | 기업명 |
| representative_name | 대표자명 |
| valid_from | 인증 시작일 |
| valid_to | 인증 종료일 |

---

# Mapping Rules

모든 데이터는 시스템 표준 컬럼으로 변환한 후 데이터베이스에 저장한다.

사업자등록번호는 모든 데이터 연결의 기준(Key)으로 사용한다.
