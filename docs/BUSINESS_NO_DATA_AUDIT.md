# 사업자등록번호 실데이터 정합성 조사

## 0. 조사 목적

STEP 74 에서 기업 사업자등록번호의 저장·조회 표기를 구매 데이터와 맞췄다.
이 문서는 그 변경이 **실제 데이터에서도 안전한지** 확인하기 위한 조사 기록이다.

확인하려는 것은 셋이다.

```
① 실제 기업 데이터의 사업자등록번호가 어떤 표기로 저장되어 있는가
② 표기를 정리하면 서로 부딪히는(같은 번호가 되는) 기업이 있는가
③ 표기 차이 때문에 지금 연결되지 않은 구매가 얼마나 되는가
```

> ⛔ **고치는 STEP 이 아니다.** 실제 DB 를 수정하지 않고, 기업을 병합하지 않고,
> 업무규칙을 정하지 않는다.

> ⛔ **"연결 후보" 를 "연결 확정" 으로 쓰지 않는다.** 순서는 아래와 같으며,
> 조사는 첫 칸까지만 한다.
>
> ```
> 표기상 연결 후보  →  실제 동일 기업인지 사람 확인  →  인증 보유 확인  →  분자 반영
> ```

---

## 1. 조사 환경

| 항목 | 값 |
|---|---|
| branch | `claude/period-filter-import-batch` |
| commit | `3db65a0` (STEP 74) · working tree clean |
| 조사일 | 2026-08-31 |
| 실행 환경 | Linux · Python 3.12.3 · SQLite |
| `database/procurement.db` | **존재하지만 0 bytes · 테이블 0개** |
| 저장소 내 데이터 파일 | `.db` · `.xlsx` · `.csv` **0건** |

### 🔴 실제 고객 데이터는 이 환경에서 사용할 수 없다

**실제 고객 DB 부재로 실데이터 사업자등록번호 표기 현황 및 실제 매칭 영향은
확인하지 못했다.**

⛔ **합성 데이터로 대신 조사하지 않았다.** 합성 숫자를 실데이터 조사 결과처럼
적으면, 읽는 사람이 "확인된 현황" 으로 받아들이게 된다.

따라서 이 문서가 담은 것은 다음 셋뿐이다.

```
① 실제 데이터가 왔을 때 무엇을 어떤 순서로 조사하는가   (§2 ~ §6)
② 조사 기능이 데이터를 건드리지 않는다는 확인            (§9)
③ 조사 없이도 확인할 수 있었던 구조적 사실               (§7 · §8)
```

---

## 2. 기업 사업자등록번호 표기 현황

### 2.1 실데이터 결과

🔴 **미확인** — 실제 기업 데이터가 없다.

### 2.2 실데이터가 오면 이렇게 조사한다

`CompanyRepository.survey_business_no_formats()` 는 **읽기만** 한다(§9).

```python
from procurement.database.company_repository import CompanyRepository

survey = CompanyRepository("database/procurement.db").survey_business_no_formats()
print(survey)
```

| 항목 | 뜻 |
|---|---|
| `total` | 기업 전체 |
| `digits_only` | 구분자 없이 숫자만 |
| `with_hyphen` | 하이픈이 든 값 |
| `with_space` | 공백이 든 값 |
| `conflicting` | 구분자를 지우면 **다른 행과 같아지는** 값 |

`survey` 가 세지 않는 두 가지는 아래로 확인한다 — ⛔ 이것도 **읽기만** 한다.

```python
from procurement.core.business_no_storage import to_storage_business_no

rows = CompanyRepository("database/procurement.db").execute(
    "SELECT company_id, business_no, company_name FROM company"
)

buckets: dict[str, list[int]] = {}
for row in rows:
    raw = str(row["business_no"])
    clean = to_storage_business_no(raw)
    if not clean:
        kind = "빈 값"
    elif raw == clean:
        kind = "숫자만"
    elif "-" in raw and any(ch.isspace() for ch in raw):
        kind = "하이픈+공백"
    elif "-" in raw:
        kind = "하이픈"
    elif any(ch.isspace() for ch in raw):
        kind = "공백"
    else:
        kind = "기타 구분자"
    buckets.setdefault(kind, []).append(row["company_id"])

for kind, ids in sorted(buckets.items()):
    print(f"{kind:10} {len(ids):>6}")
```

⛔ **자릿수·체크섬으로 유효/무효를 판정하지 않는다.** 이 조사가 보는 것은
**표기**뿐이다. 9자리든 10자리든 여기서는 구분하지 않는다.

---

## 3. 정규화 충돌 현황

### 3.1 실데이터 결과

🔴 **미확인**.

### 3.2 조사 방법

```python
for conflict in CompanyRepository("database/procurement.db").find_normalization_conflicts():
    print(conflict.business_no)
    for company in conflict.companies:
        print("   ", company.company_id, repr(company.business_no), company.company_name)
```

각 충돌에서 기록할 것은 넷이다 — `company_id` · 현재 `business_no` ·
정규화 후 값 · 기업명.

### 3.3 충돌을 만나면

| 구분 | 무엇이 보이는가 | 처리 |
|---|---|---|
| **Case 1** | 기업명이 같거나 비슷하다 — 같은 기업의 중복 등록으로 보인다 | 🔴 **사람 확인 필요** |
| **Case 2** | 기업명이 다르거나 같은 기업인지 판단할 수 없다 | 🔴 **사람 확인 필요** |

⛔ **어느 경우든 자동 병합하지 않는다.** 이름이 달라도 같은 회사일 수 있고,
같아도 잘못 입력된 남남일 수 있다 — 시스템은 그것을 알 수 없다.

⚠️ `normalize_stored_business_numbers()` 는 **충돌 묶음을 건드리지 않는다.**
`apply=True` 로 정리해도 그 행들은 옛 표기 그대로 남는다.

---

## 4. 구매-기업 매칭 현황

### 4.1 실데이터 결과

🔴 **미확인** — 실제 구매·기업 데이터가 없다.

### 4.2 분류 기준 (§5 Case A~E)

⚠️ 아래는 **분류 규칙**이며 실데이터 집계가 아니다. 구매의 사업자등록번호는
적재하면서 이미 숫자만 남으므로, 차이는 **기업 쪽 표기**에서만 생긴다.

| Case | 구매 | 기업 | 지금 어떻게 되는가 |
|---|---|---|---|
| **A** | `2208162517` | `2208162517` | 연결됨 — 예전부터 정상 |
| **B** | `2208162517` | `220-81-62517` | **연결됨** — STEP 74 로 표기를 맞춰 본다 |
| **C** | `2208162517` | `220 81 62517` | **연결됨** — 위와 같은 이유 |
| **D** | `2208162517` | `22081` | ⛔ **연결하지 않는다** — 부분번호는 매칭 대상이 아니다 |
| **E** | `2208162517` | 다른 번호 | 연결하지 않는다 — 미매칭 유지 |

B · C 는 **DB 를 고치지 않고도** 연결된다. 저장값 정리는 별개의 판단이다.

### 4.3 조사 방법

```python
status = client.get("/dashboard/data-status").json()
status["matched_purchase_count"], status["unmatched_purchase_count"]
```

⛔ **재매칭(`POST /purchases/rematch`)을 조사 목적으로 실행하지 않는다.** 그것은
읽기가 아니라 `purchase.company_id` 를 바꾸는 동작이다.

---

## 5. 표기 차이로 인한 미매칭 후보

### 5.1 실데이터 결과

🔴 **미확인**.

### 5.2 조사 방법 — 읽기 전용

```python
from procurement.core.business_no_storage import to_storage_business_no
from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository

db = "database/procurement.db"
companies = {
    to_storage_business_no(row["business_no"]): row["company_id"]
    for row in CompanyRepository(db).execute("SELECT company_id, business_no FROM company")
}

buckets = {"표기 차이 가능성": [], "번호 자체가 기업정보에 없음": [], "부분번호/잘못된 형태": []}
for purchase in PurchaseRepository(db).find_unmatched():
    key = to_storage_business_no(purchase.business_no)
    if len(key) != 10 or not key.isdigit():
        buckets["부분번호/잘못된 형태"].append(purchase)
    elif key in companies:
        buckets["표기 차이 가능성"].append(purchase)
    else:
        buckets["번호 자체가 기업정보에 없음"].append(purchase)
```

정리 형식은 다음과 같다.

| 구분 | 건수 |
|---|---|
| 전체 미매칭 구매 | 🔴 미확인 |
| 번호 자체가 기업정보에 없음 | 🔴 미확인 |
| 표기 차이 가능성 | 🔴 미확인 |
| 부분번호/잘못된 형태 | 🔴 미확인 |
| 기타 | 🔴 미확인 |

⛔ **"표기 차이 가능성" 은 같은 기업이라는 뜻이 아니다.** 번호를 지운 결과가
같다는 것뿐이며, 실제 동일 기업인지는 사람이 확인한다.

---

## 6. 잠재적 계산 영향

### 6.1 실데이터 결과

🔴 **미확인**.

### 6.2 어떻게 적어야 하는가

조사 결과는 반드시 이 형태로 적는다.

```
사업자등록번호 표기 차이로 현재 미매칭된 후보 금액이 N 원
```

⛔ 다음처럼 적지 않는다.

```
분자가 N 원 증가한다          ← 틀렸다
N 건의 매칭이 확정되었다       ← 틀렸다
```

### 6.3 왜 후보 금액 ≠ 분자 증가액인가

```
후보 금액
   ├─ 실제로 같은 기업이 아닐 수 있다        → 사람 확인
   ├─ 같은 기업이어도 인증이 없을 수 있다     → 분자에 들어가지 않는다
   ├─ 인증이 있어도 유효기간 밖일 수 있다     → 들어가지 않는다
   ├─ 조회 기간 밖일 수 있다                 → 들어가지 않는다
   └─ 실적 제외 대상일 수 있다               → 분모·분자 모두에서 빠진다
```

🟡 **계산식은 하나도 바뀌지 않았다.** 분자가 움직인다면 그것은 계산이 달라진
것이 아니라 **연결되지 않던 것이 연결된** 결과다(`DECISIONS.md` §0.11.5).

---

## 7. 인증 데이터 표기 현황

### 7.1 🟡 인증 테이블에는 사업자등록번호가 **없다**

실데이터 없이도 확인된 구조적 사실이다.

```
certification(certification_id, company_id, policy_id, certificate_number,
              valid_from, valid_to, issuing_agency, created_at, updated_at)
```

인증은 **`company_id` 로만** 기업에 붙는다. 사업자등록번호를 따로 들고 있지
않으므로, **인증 데이터에는 표기 불일치 문제가 생길 수 없다.** 표기 정합성을
지켜야 할 곳은 `company` 한 곳뿐이다.

→ 따라서 **인증 데이터 정규화·UPDATE 는 필요하지 않다.**

### 7.2 🟡 인증 수집 경로도 같은 조회를 쓴다 — 그래서 함께 해결되었다

`CertificationSyncService` 는 API 응답의 **정규화된** 사업자번호로 기업을 찾고,
없으면 `COMPANY_NOT_FOUND` 로 건너뛴다.

```
API 응답(정규화된 번호)  →  find_by_business_no()  →  없으면 인증을 저장하지 않음
```

기업이 옛 표기(`220-81-62517`)로 저장되어 있으면 **인증 수집이 조용히
건너뛰었다.** 인증이 없으면 정책 분자도 0 이므로, 매칭 실패와 같은 결과가
**한 단계 앞에서** 벌어지고 있었다.

STEP 74 로 조회가 양쪽 표기를 맞춰 보게 되면서 이 경로도 함께 연결된다.
회귀 시험으로 고정했다(`tests/test_business_no_data_audit.py`).

⚠️ **이번 STEP 에서 인증 데이터를 수정하지 않았다.** 수집 경로가 실제로 도는
것은 외부 API 가 열린 환경에서다.

---

## 8. 기업 등록 경로 확인

### 8.1 🟡 `CompanyRepository.insert()` 를 부르는 운영 코드가 **아직 없다**

`src/` 전체를 확인한 결과, 기업을 만드는 경로는 다음과 같다.

| 경로 | 기업을 만드는가 |
|---|---|
| 구매 업로드 (`PurchaseImporter`) | ❌ 찾기만 하고, 없으면 **미매칭으로 저장** |
| 인증 수집 (`CertificationSyncService`) | ❌ 찾기만 하고, 없으면 **건너뜀** |
| 부트스트랩 | ❌ 테이블만 만든다 |
| 재매칭 (`POST /purchases/rematch`) | ❌ 기존 기업과 잇기만 한다 |

즉 **기업정보를 넣는 경로 자체가 아직 없다.** 지금 저장 규칙을 맞춰 둔 것은
잘못된 표기가 쌓이기 **전에** 막아 둔 것이다.

### 8.2 🟡 앞으로 기업 등록 경로가 생기면

`CompanyRepository.insert()` 를 거치기만 하면 표기는 자동으로 정리된다 —
호출하는 쪽이 따로 신경 쓸 것이 없다.

⛔ **SQL 로 직접 INSERT 하지 않는다.** 저장소를 건너뛰면 옛 표기가 다시 쌓이고,
그때는 조회가 매번 전체를 훑게 된다.

---

## 9. 자동 변경 여부

### 9.1 🟡 조사 기능은 읽기만 한다

| 기능 | 쓰기 |
|---|---|
| `survey_business_no_formats()` | 없음 |
| `find_normalization_conflicts()` | 없음 |
| `find_by_business_no()` · `exists()` | 없음 |
| `normalize_stored_business_numbers()` | **`apply=True` 일 때만** |

### 9.2 🟡 운영 중에는 저장값이 바뀌지 않는다

다음을 모두 수행해도 `company.business_no` 는 그대로다 — 시험으로 고정했다.

```
부트스트랩 · 앱 시작 · 파일 업로드 · 기업 조회 · 매칭 · 재매칭 · 대시보드 조회
```

`normalize_stored_business_numbers()` 는 **어느 경로에서도 자동 호출되지
않는다.** 사람이 `apply=True` 로 직접 불러야 반영된다.

⛔ **이번 조사에서 실제 DB 에 `apply=True` 를 실행하지 않았다.** 실행할 실제
DB 자체가 없었고, 있었더라도 조사 STEP 에서 할 일이 아니다.

---

## 10. 발견 사항

### ① 즉시 수정이 필요한 것 — **없음**

STEP 74 구현으로 조사에 필요한 기능이 모두 있었고, 새로 만들 것이 없었다.
이번 STEP 의 코드 변경은 **0건**이다.

### ② 운영 주의

| 항목 | 내용 |
|---|---|
| 기업 등록은 반드시 저장소를 거친다 | SQL 직접 INSERT 는 옛 표기를 다시 쌓는다(§8.2) |
| 옛 표기 행이 남아 있으면 조회가 전체를 훑는다 | 매칭 실패마다 한 번씩. 저장값을 정리하면 사라진다 |
| 재매칭은 조사가 아니다 | `POST /purchases/rematch` 는 `company_id` 를 바꾼다(§4.3) |

### ③ 추가 조사가 필요한 것 (실데이터를 받은 뒤)

```
□ 기업 표기 분포 6종                    (§2.2)
□ 정규화 충돌 목록과 Case 1/2 구분       (§3)
□ 미매칭 구매 4분류                     (§5.2)
□ 표기 차이 후보 금액                   (§6.2 형식으로)
□ 저장값 정리 여부 판단 — 충돌 확인 후    (§3.3)
```

---

## 11. 고객 확인이 필요한 사항

이 조사에서 **새로 생긴 고객 질문은 없다.** 사업자등록번호 표기는 업무규칙이
아니라 시스템 내부의 저장 형식 문제이기 때문이다.

다만 §3 의 충돌이 실데이터에서 발견되면 그때는 사람이 판단해야 한다.

| 상황 | 누가 판단하는가 |
|---|---|
| 같은 번호로 모이는 기업이 실제로 같은 회사인가 | 🔴 **담당자/고객 확인** — 시스템은 판단하지 않는다 |
| 합칠 것인가, 한쪽을 지울 것인가, 둘 다 둘 것인가 | 🔴 **고객 확인** — 기업 병합은 구현되어 있지 않다 |

---

## 12. 결론

| 질문 | 답 |
|---|---|
| 실데이터를 조사했는가 | 🔴 **아니다** — 실제 고객 DB 가 이 환경에 없다 |
| 조사 절차가 준비되었는가 | 🟡 **그렇다** — §2 ~ §6 에 명령과 분류 기준까지 |
| 조사 기능이 안전한가 | 🟡 **그렇다** — 읽기만 하며, 운영 중 자동 변경이 없음을 시험으로 고정 |
| 인증 데이터도 정리해야 하는가 | 🟡 **아니다** — 인증에는 사업자등록번호 컬럼이 없다(§7.1) |
| 새로 고칠 것이 있는가 | 🟡 **없다** — 이번 STEP 코드 변경 0건 |
| 업무규칙을 정했는가 | ⛔ **하나도 정하지 않았다** |

**미확정 사항은 그대로다** — W-1-2 · Q5-8 · Q5-9 · W-11~W-15 · W-6 구매유형
자동분류 · STEP 71 Q-A~Q-D. 이 조사는 그중 어느 것도 건드리지 않았다.

다음은 사업자등록번호가 아니라 **고객 답변**이 필요한 단계다 — 특히 **W-1-2**
는 확정 전까지 기간 조회 설정 자체가 임시값이며, 달성률 숫자에 직접 영향을
준다.
