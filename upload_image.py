#!/usr/bin/env python3
"""
tar.gz 파일(docker save 형식)을 Gitea 내장 컨테이너 레지스트리에 push하는 스크립트
Docker 없이 이미지를 업로드할 수 있습니다.

────────────────────────────────────────────────────────────
레지스트리 정보 (하드코딩)
────────────────────────────────────────────────────────────
  주소   : 172.16.28.203:30001
  프로토콜: http  (Gitea 내부망 서비스이므로 https 미사용)
  종류   : Gitea 내장 컨테이너 레지스트리
             → Docker Registry v2 API 호환
             → 인증: Gitea 계정 ID / Access Token (Basic Auth)
────────────────────────────────────────────────────────────

사용법:
  python upload_image.py <tar.gz 파일> <image:tag> [username] [token]

  * image:tag 형식:  <Gitea조직(또는유저)>/<이미지명>:<태그>
    레지스트리 호스트(172.16.28.203:30001)는 자동으로 앞에 붙습니다.

예시:
  python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0 gitea_user gitea_token

  # 환경변수 사용
  set DOCKER_USERNAME=gitea_user
  set DOCKER_TOKEN=gitea_token
  python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0
"""


import requests
import json
import gzip
import os
import sys
import tarfile
import hashlib
from io import BytesIO
import urllib3

# SSL 경고 비활성화
# → 172.16.28.203:30001 은 http 이므로 실질적으로 SSL 경고는 발생하지 않지만
#   혼합 환경 대비를 위해 유지
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ────────────────────────────────────────────────────────────
# ★ 레지스트리 주소 하드코딩 (변경 시 이 값만 수정)
# ────────────────────────────────────────────────────────────
# Gitea 내장 컨테이너 레지스트리
#   - IP   : 172.16.28.203  (내부망 Gitea 서버)
#   - Port : 30001          (NodePort or 직접 바인딩 포트)
#   - 프로토콜: http         (내부망 전용, TLS 미적용)
REGISTRY_HOST     = "172.16.28.203:30001"
REGISTRY_BASE_URL = f"http://{REGISTRY_HOST}"   # http 고정 (https 아님)


# ────────────────────────────────────────────────────────────
# ★ Gitea 계정 정보 하드코딩 (변경 시 이 두 값만 수정)
# ────────────────────────────────────────────────────────────
# 주의: 이 파일을 공개 저장소에 push 할 경우 계정 정보가 노출됩니다.
#       공개 Repo 라면 환경변수(GITEA_USERNAME / GITEA_TOKEN) 방식을 사용하세요.
#
# 인증 정보 우선순위:
#   1순위: 명령행 인수          (python upload_image.py ... <user> <token>)
#   2순위: 환경변수             (GITEA_USERNAME / GITEA_TOKEN)
#   3순위: 환경변수             (DOCKER_USERNAME / DOCKER_TOKEN)
#   4순위: 아래 하드코딩 값     ← 위 1~3순위 모두 없을 때 사용
# GITEA_DEFAULT_USERNAME = "계정명을_여기에_입력"   # ← Gitea 로그인 ID
# GITEA_DEFAULT_PASSWORD = "패스워드를_여기에_입력"  # ← Gitea 로그인 PW 또는 Access Token

GITEA_DEFAULT_USERNAME = "zezoadmin"   # ← Gitea 로그인 ID
GITEA_DEFAULT_PASSWORD = "gksghk12!"  # ← Gitea 로그인 PW 또는 Access Token


# ────────────────────────────────────────────────────────────
# 레지스트리 주소 파싱
# ────────────────────────────────────────────────────────────

def parse_image_ref(image_ref: str, default_namespace: str = None):
    """
    image_ref 예시 (레지스트리 호스트 없이 입력):
      myorg/myimage:v1.0.0          → namespace=myorg,     repo=myimage
      postgres:v.17.7-test          → namespace=<계정명>,   repo=postgres  (슬래시 없을 때)
      172.16.28.203:30001/myorg/myimage:v1.0.0  → 호스트 포함 전체 경로

    Gitea 레지스트리 경로 구조:
      http://<host>/v2/<namespace>/<repo>/blobs/uploads/
      namespace = Gitea 계정명 또는 조직명

    default_namespace:
      슬래시 없이 이미지명만 입력했을 때 사용할 namespace.
      미지정 시 GITEA_DEFAULT_USERNAME 을 사용.

    반환: (registry_host, namespace, repo, tag)
    """
    # 태그 분리
    if ":" in image_ref.split("/")[-1]:
        ref_no_tag, tag = image_ref.rsplit(":", 1)
    else:
        ref_no_tag = image_ref
        tag = "latest"

    parts = ref_no_tag.split("/")

    # 첫 번째 파트에 점(.) 또는 콜론(:) 이 있으면 레지스트리 호스트로 간주
    # 예) "172.16.28.203:30001" → 호스트로 인식
    if len(parts) >= 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry_host = parts[0]
        remainder = "/".join(parts[1:])
    else:
        # 레지스트리 호스트 미입력 시 하드코딩된 REGISTRY_HOST 를 자동으로 사용
        registry_host = REGISTRY_HOST
        remainder = ref_no_tag

    # remainder → namespace/repo
    if "/" in remainder:
        # 슬래시 포함: 앞부분이 namespace(Gitea 계정/조직), 뒷부분이 repo
        # 예) zezoadmin/postgres → namespace=zezoadmin, repo=postgres
        namespace, repo = remainder.split("/", 1)
    else:
        # 슬래시 없이 이미지명만 입력된 경우
        # Gitea 레지스트리는 반드시 namespace(계정명)가 필요함
        # → default_namespace 또는 GITEA_DEFAULT_USERNAME 을 자동으로 사용
        # 예) postgres → namespace=zezoadmin, repo=postgres
        repo = remainder
        namespace = default_namespace or GITEA_DEFAULT_USERNAME
        print(f"  [info] namespace 미지정 → Gitea 계정명 '{namespace}' 을 자동 사용")
        print(f"         (명시하려면: {registry_host}/{namespace}/{repo}:{tag} 형식으로 입력)")

    return registry_host, namespace, repo, tag


def registry_base_url(registry_host: str) -> str:
    # ★ 172.16.28.203:30001 은 반드시 http 사용
    #   Gitea 내부망 레지스트리는 TLS 미적용 상태
    if registry_host == REGISTRY_HOST:
        return REGISTRY_BASE_URL   # "http://172.16.28.203:30001"
    # 로컬 개발 레지스트리도 http 허용
    if registry_host.startswith("localhost") or registry_host.startswith("127."):
        return f"http://{registry_host}"
    # 그 외 외부 레지스트리는 https
    return f"https://{registry_host}"


# ────────────────────────────────────────────────────────────
# 인증 헤더 생성
# ────────────────────────────────────────────────────────────

def get_auth_token(registry_host: str, namespace: str, repo: str,
                   username: str = None, password: str = None) -> str | None:
    """
    레지스트리 인증 헤더 값을 반환합니다.

    ★ 172.16.28.203:30001 (Gitea 내장 레지스트리) 인증 방식:
       - Docker Hub 처럼 별도 토큰 서버가 없음
       - Gitea 계정 ID + Access Token 으로 HTTP Basic Auth 사용
       - 반환값: "Basic <base64(user:token)>"
       - Access Token 생성 위치:
           Gitea UI → 우상단 아이콘 → Settings → Applications → Generate Token
    """
    import base64

    if username and password:
        # Gitea 포함 모든 프라이빗 레지스트리 → Basic Auth
        auth_str = base64.b64encode(f"{username}:{password}".encode()).decode()
        print(f"  [auth] Basic Auth 인증 (user={username}, registry={registry_host})")
        return f"Basic {auth_str}"

    print(f"  [auth] 인증 정보 없음 — anonymous push 시도 (레지스트리={registry_host})")
    return None


# ────────────────────────────────────────────────────────────
# tar.gz 파일 파싱 (docker save 형식)
# ────────────────────────────────────────────────────────────

def load_tar_gz(filepath: str):
    """
    tar.gz(docker save 형식) 파일을 파싱하여 반환.
    반환: {
        "config_digest": str,      # sha256:xxx
        "config_data": bytes,
        "layers": [
            {"digest": "sha256:xxx", "data": bytes},
            ...
        ],
        "repo_tags": ["image:tag", ...]
    }
    """
    print(f"\n[parse] tar.gz 파일 읽는 중: {filepath}")

    members = {}
    with tarfile.open(filepath, "r:gz") as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            if f:
                members[member.name] = f.read()

    # manifest.json 파싱
    if "manifest.json" not in members:
        raise ValueError("manifest.json 이 tar.gz 안에 없습니다. docker save 형식인지 확인하세요.")

    manifest_list = json.loads(members["manifest.json"])
    if not manifest_list:
        raise ValueError("manifest.json 이 비어 있습니다.")

    entry = manifest_list[0]
    repo_tags = entry.get("RepoTags", [])
    config_filename = entry["Config"]          # e.g. sha256:abc.json or abc.json
    layer_filenames = entry.get("Layers", [])  # e.g. ["sha256:xxx/layer.tar", ...]

    # Config
    config_data = members.get(config_filename)
    if config_data is None:
        raise ValueError(f"Config 파일 '{config_filename}' 이 tar 안에 없습니다.")
    config_digest = "sha256:" + hashlib.sha256(config_data).hexdigest()
    print(f"  Config digest : {config_digest[:32]}...")
    print(f"  RepoTags      : {repo_tags}")
    print(f"  Layers 수     : {len(layer_filenames)}")

    # Layers
    layers = []
    for lf in layer_filenames:
        layer_data = members.get(lf)
        if layer_data is None:
            raise ValueError(f"레이어 파일 '{lf}' 이 tar 안에 없습니다.")
        digest = "sha256:" + hashlib.sha256(layer_data).hexdigest()
        layers.append({"digest": digest, "data": layer_data, "filename": lf})
        print(f"  Layer {len(layers):>2}: {digest[:32]}...  ({len(layer_data):,} bytes)")

    return {
        "config_digest": config_digest,
        "config_data": config_data,
        "layers": layers,
        "repo_tags": repo_tags,
    }


# ────────────────────────────────────────────────────────────
# Registry v2 API — blob / manifest push
# ────────────────────────────────────────────────────────────

def blob_exists(base_url: str, namespace: str, repo: str,
                digest: str, auth_header: str) -> bool:
    """레지스트리에 이미 해당 blob이 있는지 확인 (HEAD 요청)."""
    url = f"{base_url}/v2/{namespace}/{repo}/blobs/{digest}"
    resp = requests.head(url, headers={"Authorization": auth_header}, verify=False)
    return resp.status_code == 200


def push_blob(base_url: str, namespace: str, repo: str,
              digest: str, data: bytes, auth_header: str,
              label: str = "blob") -> bool:
    """
    Blob을 레지스트리에 push합니다 (chunked monolithic upload).
    1) POST  /v2/{name}/blobs/uploads/         → upload URL 획득
    2) PUT   <upload_url>&digest=<digest>       → 데이터 전송
    """
    # 이미 존재하면 skip
    if blob_exists(base_url, namespace, repo, digest, auth_header):
        print(f"  [skip] {label} 이미 존재: {digest[:32]}...")
        return True

    print(f"  [push] {label} 업로드 시작: {digest[:32]}... ({len(data):,} bytes)")

    # Step 1: 업로드 URL 요청
    post_url = f"{base_url}/v2/{namespace}/{repo}/blobs/uploads/"
    resp = requests.post(post_url,
                         headers={"Authorization": auth_header, "Content-Length": "0"},
                         verify=False)
    if resp.status_code not in (202, 201):
        print(f"  [error] 업로드 초기화 실패: {resp.status_code} {resp.text[:300]}")
        return False

    upload_url = resp.headers.get("Location", "")
    if not upload_url:
        print("  [error] Location 헤더 없음")
        return False

    # 상대 경로면 base_url 붙이기
    if upload_url.startswith("/"):
        upload_url = base_url + upload_url

    # Step 2: PUT으로 데이터 전송
    sep = "&" if "?" in upload_url else "?"
    put_url = f"{upload_url}{sep}digest={digest}"
    put_resp = requests.put(
        put_url,
        data=data,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        },
        verify=False,
    )
    if put_resp.status_code in (201, 204):
        print(f"  [ok]   {label} push 완료.")
        return True
    else:
        print(f"  [error] {label} push 실패: {put_resp.status_code} {put_resp.text[:300]}")
        return False


def push_manifest(base_url: str, namespace: str, repo: str,
                  tag: str, config_digest: str, config_size: int,
                  layers: list, auth_header: str) -> bool:
    """
    Docker Distribution Manifest v2 를 push합니다.
    layers: [{"digest": ..., "data": bytes}, ...]
    """
    manifest_payload = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {
            "mediaType": "application/vnd.docker.container.image.v1+json",
            "size": config_size,
            "digest": config_digest,
        },
        "layers": [
            {
                "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                "size": len(layer["data"]),
                "digest": layer["digest"],
            }
            for layer in layers
        ],
    }

    manifest_json = json.dumps(manifest_payload, indent=2).encode("utf-8")
    url = f"{base_url}/v2/{namespace}/{repo}/manifests/{tag}"

    print(f"\n  [manifest] push → {url}")
    resp = requests.put(
        url,
        data=manifest_json,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/vnd.docker.distribution.manifest.v2+json",
        },
        verify=False,
    )
    if resp.status_code in (200, 201):
        print(f"  [ok] Manifest push 완료 (tag={tag})")
        return True
    else:
        print(f"  [error] Manifest push 실패: {resp.status_code} {resp.text[:400]}")
        return False


# ────────────────────────────────────────────────────────────
# 메인 업로드 흐름
# ────────────────────────────────────────────────────────────

def upload_image(tar_gz_path: str, image_ref: str,
                 username: str = None, password: str = None) -> bool:
    """
    tar.gz → registry push 전체 흐름.
    image_ref 형식:
      [registry_host/]namespace/repo:tag
    """
    # 1. tar.gz 파싱
    image_data = load_tar_gz(tar_gz_path)

    # 2. 레지스트리 주소 파싱
    # username 을 default_namespace 로 전달 → 슬래시 없이 이미지명만 입력했을 때
    # Gitea 계정명(=namespace)을 자동으로 채워줌
    # 예) "172.16.28.203:30001/postgres:v.17.7-test"
    #     → namespace=zezoadmin, repo=postgres  (username 기반 자동 설정)
    registry_host, namespace, repo, tag = parse_image_ref(
        image_ref, default_namespace=username
    )
    base_url = registry_base_url(registry_host)
    print(f"\n[target] 레지스트리 : {base_url}")
    print(f"         이미지      : {namespace}/{repo}:{tag}")

    # 3. 인증 토큰 획득
    auth = get_auth_token(registry_host, namespace, repo, username, password)
    if auth is None:
        print("[warn] 인증 정보 없음 — anonymous push 시도 (프라이빗 레지스트리는 실패할 수 있음)")
        auth = ""

    # 4. Config blob push
    ok = push_blob(base_url, namespace, repo,
                   image_data["config_digest"], image_data["config_data"],
                   auth, label="Config")
    if not ok:
        return False

    # 5. Layer blobs push
    for i, layer in enumerate(image_data["layers"]):
        ok = push_blob(base_url, namespace, repo,
                       layer["digest"], layer["data"],
                       auth, label=f"Layer {i+1}/{len(image_data['layers'])}")
        if not ok:
            return False

    # 6. Manifest push
    ok = push_manifest(base_url, namespace, repo, tag,
                       image_data["config_digest"],
                       len(image_data["config_data"]),
                       image_data["layers"], auth)
    return ok


# ────────────────────────────────────────────────────────────
# CLI 진입점
# ────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python upload_image.py <tar.gz 파일> <image:tag> [username] [token]")
        print()
        print("  ★ 레지스트리: http://172.16.28.203:30001  (Gitea 내장 컨테이너 레지스트리)")
        print("    image:tag 형식: <Gitea조직/유저>/<이미지명>:<태그>")
        print("    레지스트리 호스트는 자동으로 앞에 붙으므로 생략 가능")
        print()
        print("Examples:")
        print("  # 레지스트리 호스트 생략 (자동으로 172.16.28.203:30001 사용)")
        print("  python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0 gitea_user gitea_token")
        print()
        print("  # 환경변수 사용")
        print("  set DOCKER_USERNAME=gitea_user && set DOCKER_TOKEN=gitea_token")
        print("  python upload_image.py myimage.tar.gz myorg/myimage:v1.0.0")
        print()
        print("  # 레지스트리 호스트 명시 (동일 결과)")
        print("  python upload_image.py myimage.tar.gz 172.16.28.203:30001/myorg/myimage:v1.0.0 gitea_user gitea_token")
        sys.exit(1)

    tar_gz_path = sys.argv[1]
    image_ref   = sys.argv[2]

    # ──────────────────────────────────────────────────────
    # 인증 정보 우선순위 (앞에 있을수록 우선)
    #   1순위: 명령행 인수          python upload_image.py ... <user> <token>
    #   2순위: Gitea 전용 환경변수  GITEA_USERNAME / GITEA_TOKEN
    #   3순위: Docker 호환 환경변수 DOCKER_USERNAME / DOCKER_TOKEN
    #   4순위: 파일 내 하드코딩 값  GITEA_DEFAULT_USERNAME / GITEA_DEFAULT_PASSWORD
    #
    # 환경변수 설정 예시 (PowerShell):
    #   $env:GITEA_USERNAME = "계정명"
    #   $env:GITEA_TOKEN    = "토큰값"
    #
    # 환경변수 설정 예시 (CMD):
    #   set GITEA_USERNAME=계정명
    #   set GITEA_TOKEN=토큰값
    # ──────────────────────────────────────────────────────
    username = (
        sys.argv[3]                               if len(sys.argv) > 3 else
        os.environ.get("GITEA_USERNAME")       or
        os.environ.get("DOCKER_USERNAME")      or
        GITEA_DEFAULT_USERNAME                    # 4순위: 파일 내 하드코딩
    )
    password = (
        sys.argv[4]                               if len(sys.argv) > 4 else
        os.environ.get("GITEA_TOKEN")          or
        os.environ.get("DOCKER_TOKEN")         or
        GITEA_DEFAULT_PASSWORD                    # 4순위: 파일 내 하드코딩
    )

    if not os.path.isfile(tar_gz_path):
        print(f"[error] 파일을 찾을 수 없습니다: {tar_gz_path}")
        sys.exit(1)

    # 인증 정보 누락 경고 (anonymous push 는 Gitea 에서 대부분 실패)
    placeholder_user = "계정명을_여기에_입력"
    placeholder_pass = "패스워드를_여기에_입력"
    if not username or not password or username == placeholder_user or password == placeholder_pass:
        print("[warn] 인증 정보가 설정되지 않았습니다.")
        print("  upload_image.py 상단의 GITEA_DEFAULT_USERNAME / GITEA_DEFAULT_PASSWORD 를 채우거나,")
        print("  아래 방법 중 하나로 설정하세요:")
        print("    방법 1) 명령행 인수:        python upload_image.py <tar.gz> <image> <user> <token>")
        print("    방법 2) PowerShell 환경변수: $env:GITEA_USERNAME='계정명' ; $env:GITEA_TOKEN='토큰'")
        print("    방법 3) CMD 환경변수:        set GITEA_USERNAME=계정명 && set GITEA_TOKEN=토큰")
        print()

    print("=" * 60)
    print(f"  tar.gz  : {tar_gz_path}")
    print(f"  target  : {image_ref}")
    print(f"  user    : {username or '(anonymous)'}")
    print("=" * 60)

    success = upload_image(tar_gz_path, image_ref, username, password)

    print()
    if success:
        print(f"[완료] {image_ref} 업로드 성공!")
        print(f"  docker pull {image_ref}  으로 검증 가능")
    else:
        print("[실패] 업로드 중 오류가 발생했습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()
