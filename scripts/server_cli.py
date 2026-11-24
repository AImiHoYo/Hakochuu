"""Hakochuu 服务端协同管理 CLI：
1. 解析 ~/.ssh/config，支持手动选择服务器或从历史记录快速恢复
2. 支持查看远端目录、在服务端新建项目文件夹并保存选择历史
3. 支持 rsync 同步代码、在服务端构建 .venv 虚拟环境、以及建立 SSH 端口转发隧道
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = REPO_ROOT / ".hakochuu_server_history.json"
SSH_CONFIG_FILE = Path.home() / ".ssh" / "config"

DEFAULT_TUNNEL_PORTS = [
    (23333, "FastAPI 网关"),
    (11434, "LLM (Ollama / vLLM)"),
    (23334, "TTS (CosyVoice 3 / Qwen3-TTS)"),
    (23335, "VC (Seed-VC)"),
    (23336, "ASR (SenseVoice / Faster-Whisper)"),
]


def parse_ssh_config(config_path: Path = SSH_CONFIG_FILE) -> list[dict[str, str]]:
    """解析 ~/.ssh/config 中的 Host 别名及其 HostName / User 信息。"""
    if not config_path.is_file():
        return []

    hosts: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        key, value = parts[0].lower(), parts[1].strip()

        if key == "host":
            if current is not None:
                hosts.append(current)
            # 跳过通配符 Host *
            alias = value.split()[0]
            if any(ch in alias for ch in ("*", "?", "!")):
                current = None
            else:
                current = {"host": alias, "hostname": "", "user": ""}
        elif current is not None:
            if key == "hostname":
                current["hostname"] = value
            elif key == "user":
                current["user"] = value

    if current is not None:
        hosts.append(current)
    return hosts


def load_history() -> list[dict[str, str]]:
    if not HISTORY_FILE.is_file():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(host: str, remote_dir: str) -> None:
    history = [
        item
        for item in load_history()
        if not (item.get("host") == host and item.get("remote_dir") == remote_dir)
    ]
    history.insert(
        0,
        {
            "host": host,
            "remote_dir": remote_dir,
            "last_used": datetime.now(UTC).isoformat(),
        },
    )
    HISTORY_FILE.write_text(
        json.dumps(history[:10], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def select_host_interactive() -> str:
    ssh_hosts = parse_ssh_config()
    if not ssh_hosts:
        print(f"未在 {SSH_CONFIG_FILE} 找到可用 Host，请直接输入 SSH Host 或 user@ip。")
        return input("目标服务器: ").strip()

    print("\n=== 可用 SSH 服务器 (~/.ssh/config) ===")
    for idx, item in enumerate(ssh_hosts, start=1):
        detail = " ".join(
            part
            for part in [
                f"user={item['user']}" if item["user"] else "",
                f"ip={item['hostname']}" if item["hostname"] else "",
            ]
            if part
        )
        suffix = f" ({detail})" if detail else ""
        print(f"  [{idx}] {item['host']}{suffix}")
    print("  [0] 手动输入其他 Host / user@ip")

    choice = input("请选择服务器编号 [默认 1]: ").strip() or "1"
    if choice == "0":
        return input("请输入 Host 或 user@ip: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(ssh_hosts):
        return ssh_hosts[int(choice) - 1]["host"]

    print("无效编号，退出。", file=sys.stderr)
    sys.exit(1)


def select_remote_dir_interactive(host: str, default_dir: str = "~/Hakochuu") -> str:
    print(f"\n正在检查服务端 {host} 家目录下的文件夹...")
    probe = subprocess.run(
        ["ssh", host, "ls -1d */ 2>/dev/null | head -n 20"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0 and probe.stdout.strip():
        print("服务端现有目录（参考）：")
        for line in probe.stdout.strip().splitlines():
            print(f"  - ~/{line.rstrip('/')}")

    remote_dir = input(f"请输入服务端项目目录路径（不存在可自动新建）[默认 {default_dir}]: ").strip()
    remote_dir = remote_dir or default_dir

    # 检查远端目录是否存在，不存在则询问新建
    quoted_dir = shlex.quote(remote_dir)
    # 注意：若路径以 ~/ 开头，避免引号阻止 ~ 展开
    check_expr = (
        f"test -d ~/{shlex.quote(remote_dir[2:])}"
        if remote_dir.startswith("~/")
        else f"test -d {quoted_dir}"
    )
    exists = subprocess.run(["ssh", host, check_expr], check=False)
    if exists.returncode != 0:
        confirm = input(f"服务端目录 {remote_dir} 不存在，是否立即新建？[Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            mkdir_expr = (
                f"mkdir -p ~/{shlex.quote(remote_dir[2:])}"
                if remote_dir.startswith("~/")
                else f"mkdir -p {quoted_dir}"
            )
            subprocess.run(["ssh", host, mkdir_expr], check=True)
            print(f"已创建服务端目录: {remote_dir}")
        else:
            print("已取消。", file=sys.stderr)
            sys.exit(1)

    return remote_dir


def resolve_target_interactive() -> tuple[str, str]:
    history = load_history()
    if history:
        print("\n=== 最近连接历史 ===")
        for idx, item in enumerate(history, start=1):
            print(f"  [{idx}] {item['host']}  ->  {item['remote_dir']}")
        print("  [n] 选择新服务器 / 新目录")

        choice = input("请选择历史记录编号或输入 n [默认 1]: ").strip().lower() or "1"
        if choice != "n" and choice.isdigit() and 1 <= int(choice) <= len(history):
            picked = history[int(choice) - 1]
            host, remote_dir = picked["host"], picked["remote_dir"]
            save_history(host, remote_dir)
            return host, remote_dir

    host = select_host_interactive()
    remote_dir = select_remote_dir_interactive(host)
    save_history(host, remote_dir)
    return host, remote_dir


def sync_code(
    host: str,
    remote_dir: str,
    dry_run: bool = False,
    with_frontend: bool = False,
) -> None:
    print(f"\n[Sync] {REPO_ROOT} -> {host}:{remote_dir}")
    cmd = [
        "rsync",
        "-avz",
        "--progress",
        "--exclude", ".git/",
        "--exclude", ".venv/",
        "--exclude", "frontend/node_modules/",
        "--exclude", "frontend/dist/",
        "--exclude", "storage/",
        "--exclude", "cache/",
        "--exclude", "__pycache__/",
        "--exclude", ".pytest_cache/",
        "--exclude", ".ruff_cache/",
        "--exclude", "*.pyc",
        "--exclude", "*.db",
        "--exclude", "*.sqlite3",
        "--exclude", ".env",
        "--exclude", ".hakochuu_server_history.json",
        "--exclude", ".DS_Store",
    ]
    if not with_frontend:
        cmd.extend(["--exclude", "frontend/"])
    if dry_run:
        cmd.append("--dry-run")
    cmd.extend([f"{REPO_ROOT}/", f"{host}:{remote_dir}/"])
    subprocess.run(cmd, check=True)
    print("代码同步完成。")


def setup_remote_venv(host: str, remote_dir: str) -> None:
    print(f"\n[Remote Venv] 在 {host}:{remote_dir}/.venv 构建虚拟环境并安装依赖...")
    dir_expr = (
        f"~/{shlex.quote(remote_dir[2:])}"
        if remote_dir.startswith("~/")
        else shlex.quote(remote_dir)
    )
    remote_script = f"""
set -euo pipefail
cd {dir_expr}
mkdir -p cache/hf cache/torch cache/audio_separator cache/tmp
if command -v uv >/dev/null 2>&1; then
  echo "使用 uv 构建 .venv..."
  uv venv .venv --python 3.12 || uv venv .venv
  uv pip install --python .venv/bin/python -e "backend[dev,asr,tts]"
else
  echo "未检测到 uv，回退使用 python3 -m venv..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e "backend[dev,asr,tts]"
fi
echo "服务端虚拟环境就绪: $(pwd)/.venv"
"""
    subprocess.run(["ssh", "-t", host, remote_script], check=True)


def start_tunnel(host: str) -> None:
    print(f"\n[Tunnel] 正在建立 SSH 端口转发 -> {host}")
    cmd = [
        "ssh",
        "-N",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
    ]
    for port, desc in DEFAULT_TUNNEL_PORTS:
        print(f"  127.0.0.1:{port:<5} -> remote:{port} ({desc})")
        cmd.extend(["-L", f"{port}:127.0.0.1:{port}"])
    cmd.append(host)
    print("按 Ctrl+C 断开隧道。")
    subprocess.run(cmd, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hakochuu 服务端协同管理 CLI")
    parser.add_argument("--host", help="~/.ssh/config 中的 Host 别名或 user@ip")
    parser.add_argument("--dir", dest="remote_dir", help="服务端项目目录路径")
    parser.add_argument(
        "--last",
        action="store_true",
        help="直接使用最近一次历史记录中的服务器与目录，免交互增量同步",
    )
    parser.add_argument(
        "--action",
        choices=["interactive", "sync", "venv", "all", "tunnel"],
        default="interactive",
        help="执行动作",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预演 rsync 同步")
    parser.add_argument(
        "--with-frontend",
        action="store_true",
        help="一并同步 frontend/ 目录（默认服务端只同步后端与共享协议）",
    )
    args = parser.parse_args()

    if args.host and args.remote_dir:
        host, remote_dir = args.host, args.remote_dir
        save_history(host, remote_dir)
    elif args.last:
        history = load_history()
        if not history:
            print("错误: 暂无历史连接记录，请先运行一次交互模式。", file=sys.stderr)
            sys.exit(1)
        host = history[0]["host"]
        remote_dir = history[0]["remote_dir"]
    elif args.host and args.action == "tunnel":
        start_tunnel(args.host)
        return
    else:
        host, remote_dir = resolve_target_interactive()

    if args.action == "interactive":
        print(f"\n当前目标: {host}:{remote_dir}")
        print("  [1] 同步本地代码到服务端 (rsync)")
        print("  [2] 在服务端项目目录构建 .venv 并安装后端依赖")
        print("  [3] 同步代码 + 构建服务端 .venv（一步到位）")
        print("  [4] 建立 SSH 端口转发隧道")
        op = input("请选择操作 [默认 3]: ").strip() or "3"
        mapping = {"1": "sync", "2": "venv", "3": "all", "4": "tunnel"}
        action = mapping.get(op)
        if not action:
            print("无效选项。", file=sys.stderr)
            sys.exit(1)
    else:
        action = args.action

    if action == "sync":
        sync_code(host, remote_dir, dry_run=args.dry_run, with_frontend=args.with_frontend)
    elif action == "venv":
        setup_remote_venv(host, remote_dir)
    elif action == "all":
        sync_code(host, remote_dir, dry_run=args.dry_run, with_frontend=args.with_frontend)
        setup_remote_venv(host, remote_dir)
    elif action == "tunnel":
        start_tunnel(host)


if __name__ == "__main__":
    main()
