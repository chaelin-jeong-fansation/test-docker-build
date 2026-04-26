#!/usr/bin/env python3
"""
Docker Hub에서 이미지를 다운로드하여 tar.gz 파일로 저장하는 스크립트
Docker 없이 이미지를 다운로드할 수 있습니다.
"""

import requests
import json
import gzip
import os
import sys
from io import BytesIO
import urllib3

# SSL 경고 비활성화 (프록시 환경에서 필요할 수 있음)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_docker_hub_token(username=None, token=None, image_name=None):
    """Docker Hub에서 JWT 토큰을 가져옵니다. (인증 정보가 없으면 anonymous 토큰 사용)"""
    import base64
    auth_url = "https://auth.docker.io/token"
    
    # 이미지 이름에 따라 scope 동적 설정
    if image_name:
        if '/' in image_name:
            namespace, repo = image_name.split('/', 1)
        else:
            namespace = "library"
            repo = image_name
    else:
        namespace = "library"
        repo = "alpine"  # 기본값
    
    params = {
        "service": "registry.docker.io",
        "scope": f"repository:{namespace}/{repo}:pull"
    }
    
    headers = {}
    
    # 인증 정보가 있으면 사용, 없으면 anonymous 토큰 요청
    if username and token:
        auth_string = f"{username}:{token}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        headers["Authorization"] = f"Basic {auth_b64}"
        print("Getting Docker Hub token (authenticated)...")
    else:
        print("Getting Docker Hub token (anonymous)...")
    
    response = requests.get(auth_url, params=params, headers=headers, verify=False)
    if response.status_code == 200:
        return response.json().get('token')
    return None

def get_image_manifest(image_name, tag="latest", username=None, token=None):
    """Docker Hub에서 이미지 manifest를 가져옵니다. (manifest, jwt_token) 튜플 반환"""
    # 이미지 이름 파싱
    if '/' in image_name:
        namespace, repo = image_name.split('/', 1)
    else:
        namespace = "library"
        repo = image_name
    
    # Docker Hub API v2
    manifest_url = f"https://registry.hub.docker.com/v2/{namespace}/{repo}/manifests/{tag}"
    
    headers = {
        "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"
    }
    
    # JWT 토큰 가져오기 (인증 정보가 있으면 사용, 없으면 anonymous)
    jwt_token = get_docker_hub_token(username, token, image_name)
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    elif username and token:
        # JWT 토큰 실패 시 Basic Auth 시도
        import base64
        auth_string = f"{username}:{token}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        headers["Authorization"] = f"Basic {auth_b64}"
    
    print(f"Fetching manifest from: {manifest_url}")
    response = requests.get(manifest_url, headers=headers, verify=False)
    
    if response.status_code != 200:
        print(f"Error: Failed to fetch manifest. Status code: {response.status_code}")
        print(f"Response: {response.text}")
        return None, None
    
    manifest_data = response.json()
    
    # Manifest list (multi-arch)인 경우 실제 manifest 가져오기
    if 'manifests' in manifest_data:
        print("Found manifest list, fetching actual manifest...")
        # linux/amd64 플랫폼 찾기
        for m in manifest_data['manifests']:
            platform = m.get('platform', {})
            if platform.get('architecture') == 'amd64' and platform.get('os') == 'linux':
                digest = m['digest']
                actual_manifest_url = f"https://registry.hub.docker.com/v2/{namespace}/{repo}/manifests/{digest}"
                print(f"Fetching actual manifest: {digest[:20]}...")
                actual_response = requests.get(actual_manifest_url, headers=headers, verify=False)
                if actual_response.status_code == 200:
                    return actual_response.json(), jwt_token
        
        # amd64를 찾지 못한 경우 첫 번째 manifest 사용
        if manifest_data['manifests']:
            digest = manifest_data['manifests'][0]['digest']
            actual_manifest_url = f"https://registry.hub.docker.com/v2/{namespace}/{repo}/manifests/{digest}"
            print(f"Fetching first available manifest: {digest[:20]}...")
            actual_response = requests.get(actual_manifest_url, headers=headers, verify=False)
            if actual_response.status_code == 200:
                return actual_response.json(), jwt_token
    
    return manifest_data, jwt_token

def download_blob(image_name, digest, username=None, token=None, jwt_token=None):
    """특정 blob을 다운로드합니다."""
    if '/' in image_name:
        namespace, repo = image_name.split('/', 1)
    else:
        namespace = "library"
        repo = image_name
    
    blob_url = f"https://registry.hub.docker.com/v2/{namespace}/{repo}/blobs/{digest}"
    
    headers = {}
    # 인증 추가 - JWT 토큰 우선 사용
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    elif username and token:
        # JWT 토큰이 없으면 다시 가져오기 시도
        jwt_token = get_docker_hub_token(username, token, image_name)
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"
        else:
            # JWT 토큰 실패 시 Basic Auth 시도
            import base64
            auth_string = f"{username}:{token}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers["Authorization"] = f"Basic {auth_b64}"
    else:
        # 인증 정보가 없으면 anonymous 토큰 가져오기
        jwt_token = get_docker_hub_token(None, None, image_name)
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"
    
    print(f"Downloading blob: {digest[:20]}...")
    response = requests.get(blob_url, stream=True, verify=False, headers=headers)
    
    if response.status_code != 200:
        print(f"Error downloading blob: {response.status_code}")
        print(f"Response: {response.text[:200]}")
        return None
    
    return response.content

def create_tar_from_manifest(image_name, tag, manifest, output_file, username=None, token=None, jwt_token=None):
    """Manifest를 기반으로 Docker 호환 tar 파일을 생성합니다."""
    import tarfile
    import tempfile
    import hashlib
    
    print(f"Creating tar file: {output_file}")
    print(f"Manifest structure: {list(manifest.keys())}")
    
    # Docker save 형식에 맞게 변환
    layers = manifest.get('layers', [])
    config_digest = None
    if 'config' in manifest:
        config_digest = manifest['config'].get('digest')
    
    # Layer 다운로드 및 저장
    layer_paths = []
    config_path = None
    config_hash = None
    
    with tarfile.open(output_file, 'w:gz') as tar:
        # 1. Config 다운로드 및 저장
        if config_digest:
            print(f"Downloading config: {config_digest[:20]}...")
            config_data = download_blob(image_name, config_digest, username, token, jwt_token)
            if config_data:
                # Docker 형식: sha256:xxx.json
                config_hash = config_digest.replace('sha256:', '')[:64]
                config_path = f"{config_hash}.json"
                config_info = tarfile.TarInfo(name=config_path)
                config_info.size = len(config_data)
                tar.addfile(config_info, BytesIO(config_data))
                print(f"  Config saved as: {config_path}")
        
        # 2. Layers 다운로드 및 저장
        if layers:
            print(f"Found {len(layers)} layers to download")
            for i, layer in enumerate(layers):
                digest = layer.get('digest') or layer.get('blobSum', '')
                if not digest:
                    continue
                print(f"Downloading layer {i+1}/{len(layers)}: {digest[:20]}...")
                
                blob_data = download_blob(image_name, digest, username, token, jwt_token)
                if blob_data:
                    print(f"  Layer {i+1} size: {len(blob_data)} bytes")
                    # Docker 형식: sha256:xxx/layer.tar
                    layer_hash = digest.replace('sha256:', '')[:64]
                    layer_path = f"{layer_hash}/layer.tar"
                    layer_paths.append(layer_path)
                    
                    layer_info = tarfile.TarInfo(name=layer_path)
                    layer_info.size = len(blob_data)
                    tar.addfile(layer_info, BytesIO(blob_data))
                else:
                    print(f"Warning: Failed to download layer {digest}")
        else:
            print("Warning: No layers found in manifest")
        
        # 3. repositories 파일 생성 (Docker 형식)
        repositories = {
            image_name: {
                tag: config_hash if config_digest else "unknown"
            }
        }
        repos_json = json.dumps(repositories, indent=2).encode('utf-8')
        repos_info = tarfile.TarInfo(name='repositories')
        repos_info.size = len(repos_json)
        tar.addfile(repos_info, BytesIO(repos_json))
        
        # 4. manifest.json 생성 (Docker save 형식)
        docker_manifest = [{
            "Config": config_path or f"{config_hash}.json",
            "RepoTags": [f"{image_name}:{tag}"],
            "Layers": layer_paths
        }]
        manifest_json = json.dumps(docker_manifest, indent=2).encode('utf-8')
        manifest_info = tarfile.TarInfo(name='manifest.json')
        manifest_info.size = len(manifest_json)
        tar.addfile(manifest_info, BytesIO(manifest_json))
    
    print(f"Successfully created: {output_file}")
    print(f"  Config: {config_path}")
    print(f"  Layers: {len(layer_paths)}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python download_image.py <image:tag> [output_file.tar.gz] [username] [token]")
        print("Example: python download_image.py 011dlehdgml/selenium-was:v2.0.0")
        print("With auth: python download_image.py 011dlehdgml/selenium-was:v2.0.0 output.tar.gz 011dlehdgml dckr_pat_xxx")
        sys.exit(1)
    
    image_tag = sys.argv[1]
    
    if ':' in image_tag:
        image_name, tag = image_tag.rsplit(':', 1)
    else:
        image_name = image_tag
        tag = "latest"
    
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"{image_name.replace('/', '_')}_{tag}.tar.gz"
    username = sys.argv[3] if len(sys.argv) > 3 else None
    token = sys.argv[4] if len(sys.argv) > 4 else None
    
    # 환경변수에서 토큰 가져오기
    if not token:
        token = os.environ.get('DOCKER_TOKEN')
    if not username:
        username = os.environ.get('DOCKER_USERNAME')
    
    print(f"Downloading image: {image_name}:{tag}")
    print(f"Output file: {output_file}")
    if username:
        print(f"Using authentication for user: {username}")
    
    # JWT 토큰 가져오기
    jwt_token = None
    if username and token:
        jwt_token = get_docker_hub_token(username, token)
        if not jwt_token:
            print("Warning: Failed to get JWT token, will try Basic Auth")
    
    # Manifest 가져오기 (토큰도 함께 반환)
    manifest, jwt_token_from_manifest = get_image_manifest(image_name, tag, username, token)
    if not manifest:
        print("Failed to get manifest")
        sys.exit(1)
    
    # manifest에서 가져온 토큰이 있으면 사용, 없으면 이전에 가져온 토큰 사용
    if jwt_token_from_manifest:
        jwt_token = jwt_token_from_manifest
    
    # Tar 파일 생성
    create_tar_from_manifest(image_name, tag, manifest, output_file, username, token, jwt_token)
    
    print(f"\nDone! Image saved to: {output_file}")
    print(f"You can load it with: docker load -i {output_file}")

if __name__ == "__main__":
    main()

