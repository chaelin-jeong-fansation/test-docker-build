# 🐳 Docker 이미지 다운로드 / 업로드 스크립트

> **Docker 없이** Docker Hub에서 이미지를 받고, Gitea 내장 레지스트리에 올릴 수 있는 Python 스크립트 모음입니다.

---

## 📁 파일 구성

| 파일 | 역할 |
|---|---|
| `download_image.py` | Docker Hub → `tar.gz` 저장 |
| `upload_image.py` | `tar.gz` → Gitea 내장 레지스트리 push |
| `Dockerfile` | 컨테이너 빌드용 (Flask + Selenium + Python 패키지) |

---

## ⚙️ 사전 준비

Python 3 환경에서 `requests` 패키지만 있으면 됩니다.

```powershell
pip install requests
```

---

## 📥 download_image.py — Docker Hub 이미지 다운로드

### 문법

```
python download_image.py <image:tag> [출력파일.tar.gz] [username] [token]
```

| 인자 | 필수 | 설명 |
|---|---|---|
| `image:tag` | ✅ | 다운로드할 이미지 (예: `nginx:latest`, `myorg/myimage:v1.0`) |
| `출력파일.tar.gz` | ❌ | 생략 시 `myorg_myimage_v1.0.tar.gz` 형태로 자동 생성 |
| `username` | ❌ | Docker Hub 계정명 (비공개 이미지 또는 rate limit 우회 시 필요) |
| `token` | ❌ | Docker Hub Access Token |

### 사용 예시

```powershell
# ① 공개 이미지 — 인증 없이 다운로드
python download_image.py nginx:latest

# ② 공개 이미지 — 출력 파일명 직접 지정
python download_image.py nginx:latest nginx_latest.tar.gz

# ③ 비공개 이미지 — 인수로 인증 전달
python download_image.py myorg/myimage:v2.0.0 myimage.tar.gz myuser dckr_pat_xxxx

# ④ 비공개 이미지 — 환경변수로 인증 전달 (권장)
$env:DOCKER_USERNAME = "myuser"
$env:DOCKER_TOKEN    = "dckr_pat_xxxx"
python download_image.py myorg/myimage:v2.0.0
```

### 인증 우선순위

```
1순위  명령행 인수   python download_image.py <image> <output> <user> <token>
2순위  환경변수      $env:DOCKER_USERNAME  /  $env:DOCKER_TOKEN
인증 없음             anonymous 토큰으로 시도 (공개 이미지만 가능, rate limit 있음)
```

### 다운로드 후 Docker에 로드하기

```powershell
docker load -i myimage.tar.gz
docker images   # 로드된 이미지 확인
```

---

## 📤 upload_image.py — Gitea 레지스트리에 업로드

### 문법

```
python upload_image.py <tar.gz파일> <image:tag> [username] [token]
```

| 인자 | 필수 | 설명 |
|---|---|---|
| `tar.gz파일` | ✅ | `docker save` 형식의 tar.gz 파일 경로 |
| `image:tag` | ✅ | 업로드 대상 이미지 참조 (아래 형식 참고) |
| `username` | ❌ | Gitea 계정명 |
| `token` | ❌ | Gitea Access Token |

### 이미지 참조 형식

```powershell
# 형식 A — 레지스트리 호스트 생략 (REGISTRY_HOST 자동 사용)
myorg/myimage:v1.0.0

# 형식 B — 레지스트리 호스트 명시 (결과 동일)
172.16.28.203:30001/myorg/myimage:v1.0.0

# 형식 C — namespace 생략 (username 또는 GITEA_DEFAULT_USERNAME 자동 사용)
myimage:v1.0.0
# → 172.16.28.203:30001/<username>/myimage:v1.0.0 으로 push
```

### 사용 예시

```powershell
# ① 명령행 인수로 인증 전달
python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0 gitea_user gitea_token

# ② 환경변수로 인증 전달 (권장)
$env:GITEA_USERNAME = "gitea_user"
$env:GITEA_TOKEN    = "gitea_access_token"
python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0

# ③ 레지스트리 호스트 명시
python upload_image.py myimage.tar.gz 172.16.28.203:30001/myorg/myimage:v1.0.0 gitea_user gitea_token

# ④ namespace 생략 (username이 namespace로 자동 설정됨)
python upload_image.py myimage.tar.gz myimage:v1.0.0 gitea_user gitea_token
# → 172.16.28.203:30001/gitea_user/myimage:v1.0.0 으로 push
```

### 인증 우선순위

```
1순위  명령행 인수   python upload_image.py <tar> <image> <user> <token>
2순위  환경변수      $env:GITEA_USERNAME  /  $env:GITEA_TOKEN
3순위  환경변수      $env:DOCKER_USERNAME /  $env:DOCKER_TOKEN
4순위  하드코딩 값   GITEA_DEFAULT_USERNAME / GITEA_DEFAULT_PASSWORD  ← ⚠️ 아래 주의사항 참고
```

### 업로드 검증

```powershell
docker pull 172.16.28.203:30001/myorg/myimage:v1.0.0
```

---

## ⚠️ 하드코딩 주의 사항

`upload_image.py` 파일 상단에 아래 값들이 **소스코드에 직접 기재**되어 있습니다.

```python
# upload_image.py 상단 (~55번째 줄 부근)
REGISTRY_HOST          = "172.16.28.203:30001"  # ← 레지스트리 주소
GITEA_DEFAULT_USERNAME = "zezoadmin"             # ← Gitea 계정명
GITEA_DEFAULT_PASSWORD = "gksghk12!"            # ← Gitea 비밀번호 / Access Token
```

### 환경에 맞게 수정해야 하는 값

| 항목 | 파일 위치 | 수정할 변수 |
|---|---|---|
| 레지스트리 주소 | `upload_image.py` 상단 | `REGISTRY_HOST` |
| 기본 Gitea 계정명 | `upload_image.py` 상단 | `GITEA_DEFAULT_USERNAME` |
| 기본 비밀번호/토큰 | `upload_image.py` 상단 | `GITEA_DEFAULT_PASSWORD` |

### 🔒 보안 권고

> **⚠️ 이 파일을 공개(public) 저장소에 그대로 push하면 계정 정보가 외부에 노출됩니다.**  
> 아래 환경변수 방식을 사용하면 소스코드를 수정하지 않고도 안전하게 인증할 수 있습니다.

```powershell
# 현재 터미널 세션에만 적용
$env:GITEA_USERNAME = "내_계정명"
$env:GITEA_TOKEN    = "내_액세스_토큰"

# 사용자 환경변수로 영구 저장 (재부팅 후에도 유지)
[System.Environment]::SetEnvironmentVariable("GITEA_USERNAME", "내_계정명",      "User")
[System.Environment]::SetEnvironmentVariable("GITEA_TOKEN",    "내_액세스_토큰", "User")
```

> **Gitea Access Token 발급 위치:**  
> Gitea UI → 우상단 아이콘 → **Settings** → **Applications** → **Generate Token**

---

## 🔄 전체 워크플로우 예시

Docker Hub 이미지를 Gitea 내부 레지스트리로 이전하는 전형적인 흐름입니다.

```powershell
# Step 1. Docker Hub에서 이미지 다운로드 (인증 필요 시 환경변수 설정 후 실행)
python download_image.py selenium/standalone-chrome:latest selenium_chrome.tar.gz

# Step 2. Gitea 레지스트리에 업로드
$env:GITEA_USERNAME = "myuser"
$env:GITEA_TOKEN    = "gitea_access_token"
python upload_image.py selenium_chrome.tar.gz myorg/selenium-chrome:latest

# Step 3. 업로드 검증 (Docker가 설치된 환경에서)
docker pull 172.16.28.203:30001/myorg/selenium-chrome:latest
```

---

## 🛠️ 문제 해결

| 증상 | 원인 | 해결 방법 |
|---|---|---|
| `Failed to fetch manifest` | 이미지명/태그 오타 또는 인증 실패 | 이미지명 재확인, Docker Hub 토큰 재발급 |
| `401 Unauthorized` (upload) | Gitea 인증 실패 | 계정명·토큰 확인, Access Token 재발급 |
| `404 Not Found` (upload) | namespace 또는 repo 경로 오류 | 이미지 참조 형식 확인 (`namespace/repo:tag`) |
| `Connection refused` | 레지스트리 주소/포트 불일치 | `REGISTRY_HOST` 값 확인 |
| `manifest.json 이 tar.gz 안에 없습니다` | `docker save` 형식이 아닌 tar.gz | `docker save -o out.tar.gz image:tag` 로 재생성 |
| rate limit (download) | Docker Hub 익명 pull 한도 초과 | Docker Hub 계정 인증 후 재시도 |
