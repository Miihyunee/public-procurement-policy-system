# Windows 빌드 환경 준비 절차서 (STEP 126-1)

이 문서는 **Windows PC 앞에 앉은 사람이 그대로 따라 하는** 절차서다.
지시서 STEP 126-1 의 §4~§20 을 순서대로 옮긴 것이며, 이 저장소에서
미리 확인해 둔 **주의점 4가지**를 해당 자리에 끼워 넣었다.

> ⛔ 이 단계에서는 **EXE 를 만들지 않는다.** 환경이 준비됐는지만 본다.
> 실제 빌드는 STEP 126-2 다.

---

## 먼저 알아야 할 주의점 4가지

이 저장소를 미리 살펴보고 찾은 것들이다. 모르고 진행하면 중간에 막힌다.

| # | 무엇 | 어떻게 |
|---|---|---|
| ① | **`npm ci` 는 실패한다** | `package-lock.json` 이 아직 없다. `npm install` 을 쓴다 |
| ② | **`requirements.txt` 만으로는 부족하다** | `openpyxl` 이 빠져 있다. 아래 §10 의 명령을 그대로 쓴다 |
| ③ | **PyInstaller 는 버전이 정해져 있지 않다** | 어느 파일에도 선언돼 있지 않다. 설치한 버전을 **보고에 적는다** |
| ④ | **Python 은 3.12 이상** | `pyproject.toml` 의 `requires-python = ">=3.12"` |

---

## §4 Python 확인

```powershell
python --version
```

안 되면:

```powershell
py --version
```

**3.12 이상**이어야 한다. 없거나 낮으면 python.org 에서 Windows 용
3.12 이상을 설치한다.

> ⚠️ 설치 화면에서 **`Add Python to PATH`** 를 반드시 켠다. 이걸 놓치면
> PowerShell 이 `python` 을 못 찾는다.

설치 후 **PowerShell 을 새로 열고** 다시 확인한다.

```powershell
python --version
pip --version
```

## §5 Node.js 확인

```powershell
node --version
npm --version
```

없으면 nodejs.org 에서 **LTS** 를 설치하고, PowerShell 을 새로 열어
다시 확인한다.

## §6 Git 확인

```powershell
git --version
```

없으면 Git for Windows 를 설치한 뒤 다시 확인한다.

## §7~§8 저장소 받기

```powershell
mkdir C:\dev
cd C:\dev
git clone https://github.com/Miihyunee/public-procurement-policy-system.git
cd public-procurement-policy-system
git checkout claude/period-filter-import-batch
git pull
git log -1 --oneline
```

기준 커밋은 **`53bf4cf`** 다. 이보다 새 커밋이 있으면 그것을 쓴다.

## §9 가상환경

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**실행 정책 때문에 막히는 경우** — 보안 설정을 통째로 끄지 말고,
현재 사용자에 한해서만 푼다.

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

> ⛔ `Set-ExecutionPolicy Unrestricted` 나 `-Scope LocalMachine` 을 쓰지
> 않는다. 필요한 범위를 넘는다.

활성화되면 프롬프트 앞에 `(.venv)` 가 붙는다.

```powershell
python --version
pip --version
```

## §10 Python 의존성 ⚠️ 주의점 ②

이 저장소는 의존성을 **두 군데**에 적어 두었고, 둘이 완전히 같지 않다.

- `pyproject.toml` — 실제 기준. `openpyxl` 이 **있다**
- `requirements.txt` — `openpyxl` 이 **빠져 있다**

`openpyxl` 은 엑셀 업로드·양식 내려받기에 쓰는 필수 패키지다
(`uploads/excel_adapter.py`, `uploads/template.py`). 빠지면 업로드
기능이 통째로 죽고 시험도 무더기로 실패한다.

**그래서 아래 한 줄을 쓴다.** `pyproject.toml` 을 기준으로 런타임과
개발 도구를 함께 설치한다.

```powershell
pip install -e ".[dev]"
```

확인:

```powershell
pip list
```

`pydantic` · `fastapi` · `uvicorn` · **`openpyxl`** · `pytest` · `ruff` ·
`mypy` 가 보여야 한다. `openpyxl` 이 없으면 다음으로 넘어가지 말고
보고한다.

## §11 Node 의존성 ⚠️ 주의점 ①

`package-lock.json` 이 **아직 없다.** `npm ci` 는 lock 파일을 요구하므로
그대로 실패한다.

```powershell
npm install
```

이때 `package-lock.json` 이 새로 생긴다. **이것은 정상이며 버리지 않는다**
— STEP 126-2 에서 커밋 여부를 PM 이 정한다.

> ⛔ 의존성을 임의로 추가하지 않는다. `electron` 과 `electron-builder`
> 두 개만 들어 있으면 맞다.

Electron 은 내려받는 용량이 커서 몇 분 걸린다.

## §12 Electron 구조 확인 (읽기만)

```powershell
type electron\main.js
type electron\backend.js
type package.json
```

봐야 할 것 — **고치지는 않는다.**

- `electron/main.js` 가 Windows 에서 **`procurement.exe`** 를 부르는가
  (확장자 없이 부르면 `ENOENT` 로 못 찾는다)
- `electron/backend.js` 가 그 실행파일에 `run --host 127.0.0.1 --port <포트>`
  를 넘기는가

## §13 PyInstaller ⚠️ 주의점 ③

```powershell
pyinstaller --version
```

없으면 설치한다.

```powershell
pip install pyinstaller
```

> ⚠️ PyInstaller 는 이 저장소의 **어느 의존성 파일에도 선언돼 있지 않다.**
> 그래서 설치되는 버전이 그때그때 다르다. **설치된 버전 번호를 보고에
> 적어 주면** 다음 단계에서 고정할 수 있다.

## §14 빌드 사양 파일 확인

```powershell
git ls-files packaging/procurement-backend.spec
```

경로가 **출력되어야 한다.** 아무것도 안 나오면 파일이 저장소에 없다는
뜻이니 멈추고 보고한다.

(`.gitignore` 의 `*.spec` 규칙이 이 파일을 삼키던 문제는 STEP 125 에서
`!packaging/*.spec` 로 고쳤다.)

## §15~§17 시험

```powershell
pytest tests\test_step126_frozen_paths.py -q
pytest tests\test_step127_packaging_inputs.py -q
pytest -q
```

기준: **4,357 passed / 9 skipped**

> Windows 는 경로 구분자·`%APPDATA%`·파일 잠금이 Linux 와 달라서 차이가
> 날 수 있다. **숫자가 다르면 그대로 적는다.**
> ⛔ 시험을 지우거나 skip 해서 숫자를 맞추지 않는다.

## §18~§19 정적 검사

```powershell
ruff check .
mypy -p procurement
```

> `mypy .` 이 아니라 **`mypy -p procurement`** 다. 이 저장소가 쓰는
> 방식이며, `.` 로 하면 `.venv` 까지 훑어 엉뚱한 오류가 난다.

## §20 Git 상태

```powershell
git status
```

이 단계에서는 **소스 변경이 없어야 한다.**

`.venv` 와 `node_modules` 는 `.gitignore` 에 이미 들어 있어 잡히지
않는다(각각 157행·233행). `npm install` 이 만든
`package-lock.json` 은 새 파일로 보일 수 있는데, 이는 정상이다.

---

## 보고할 것

STEP 126-1 보고 양식에 아래를 채워 주면 된다. 특히 ★ 표시는 이
저장소에 아직 없는 정보라 꼭 필요하다.

- Windows 버전
- Python 버전 ★
- Node.js · npm 버전 ★
- Git 버전
- **PyInstaller 버전** ★ (주의점 ③)
- `git log -1 --oneline` 결과
- `pytest` 결과 숫자 그대로 ★
- `ruff` · `mypy` 통과 여부
- `git status` 결과
- 막힌 곳이 있으면 **화면에 나온 오류 문구 그대로**
