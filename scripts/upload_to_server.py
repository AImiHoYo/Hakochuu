"""开发期客户端资产上传脚本：支持 SHA-256 秒传校验与文件上传到服务端。

用法示例:
    .venv/bin/python scripts/upload_to_server.py \
        --server http://localhost:8000 \
        --file ./demo_ref.wav \
        --kind speech \
        --character-id char_001
"""

import argparse
import hashlib
import sys
from pathlib import Path

import httpx


def compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def upload_asset(
    server_url: str,
    file_path: Path,
    kind: str,
    character_id: str | None,
    timeout_s: float = 60.0,
) -> None:
    if not file_path.is_file():
        print(f"错误: 文件不存在 -> {file_path}", file=sys.stderr)
        sys.exit(1)

    base = server_url.rstrip("/")
    sha256 = compute_sha256(file_path)
    print(f"文件: {file_path.name} ({file_path.stat().st_size} bytes)")
    print(f"SHA-256: {sha256}")

    with httpx.Client(timeout=timeout_s) as client:
        check_resp = client.post(
            f"{base}/v1/assets/upload-check",
            json={"hashes": [sha256], "kind": kind},
        )
        if check_resp.status_code == 200:
            data = check_resp.json()
            existing = data.get("existing", [])
            if sha256 in existing:
                print(f"命中服务端秒传缓存，跳过上传: {data}")
                return
        else:
            print(
                f"警告: upload-check 返回状态 {check_resp.status_code}，继续尝试直接上传...",
                file=sys.stderr,
            )

        form_data = {"sha256": sha256, "kind": kind}
        if character_id:
            form_data["character_id"] = character_id

        with file_path.open("rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            upload_resp = client.post(
                f"{base}/v1/assets/upload",
                data=form_data,
                files=files,
            )

        if upload_resp.status_code >= 400:
            print(
                f"上传失败 ({upload_resp.status_code}): {upload_resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"上传成功: {upload_resp.json()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hakochuu 服务端资产上传工具")
    parser.add_argument("--server", default="http://localhost:8000", help="服务端基础 URL")
    parser.add_argument("--file", required=True, type=Path, help="待上传的本地文件路径")
    parser.add_argument(
        "--kind",
        default="speech",
        choices=["speech", "sing", "song", "avatar"],
        help="资产类型",
    )
    parser.add_argument("--character-id", default=None, help="关联的角色 ID（可选）")
    args = parser.parse_args()

    upload_asset(
        server_url=args.server,
        file_path=args.file,
        kind=args.kind,
        character_id=args.character_id,
    )


if __name__ == "__main__":
    main()
