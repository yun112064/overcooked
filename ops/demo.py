"""單機 demo 模式（compose.demo.yaml，scripts/demo-up.ps1 呼叫）：.env.demo 產生、改寫單一鍵、擷取快速通道網址。

  python -m ops.demo init-env [--env-file .env.demo] [--dev-env .env.dev]   不存在才建立；只印鍵名
  python -m ops.demo set --env-file .env.demo KEY VALUE                     改寫（或新增）一個鍵
  python -m ops.demo tunnel-url < cloudflared.log                           印出 https://*.trycloudflare.com；找不到回 1

只用標準函式庫；任何秘密值都不輸出到畫面。
"""
import argparse
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TUNNEL_URL = re.compile(r"https://(?:[a-z0-9-]+\.)+trycloudflare\.com(?![a-z0-9.-])", re.IGNORECASE)
# 每次產生的隨機秘密（各至少 32 字元）
GENERATED_SECRETS = ("POSTGRES_PASSWORD", "KNOWLEDGE_DB_PASSWORD", "KNOWLEDGE_SERVICE_TOKEN",
                     "INFERENCE_TOKEN", "INTERNAL_API_TOKEN")
# 從 .env.dev 帶入（沒有就留空）
FROM_DEV = ("OPENAI_API_KEY", "KNOWLEDGE_AUTHORIZATION_REF", "INFERENCE_DEVICE")


def extract_tunnel_url(text: str) -> str | None:
    """cloudflared 快速通道日誌中的公開網址（取最後一個，容器重建後以新的為準）；api.trycloudflare.com 是註冊端點不算。"""
    urls = [u for u in _TUNNEL_URL.findall(text or "") if u.lower() != "https://api.trycloudflare.com"]
    return urls[-1].lower() if urls else None


def render_demo_env(dev_env: dict, token=lambda: secrets.token_hex(32)) -> str:
    lines = ["# 單機 demo 設定（scripts/demo-up.ps1 產生；不進 Git）",
             "# PUBLIC_HOSTNAME 由 demo-up 從 cloudflared 日誌寫入（快速通道每次重開都會變）",
             "PUBLIC_HOSTNAME=", ""]
    lines += [f"{key}={token()}" for key in GENERATED_SECRETS]
    lines.append("")
    lines += [f"{key}={dev_env.get(key, '')}" for key in FROM_DEV]
    lines += ["DEMO_YOLO_PORT=8775", ""]
    return "\n".join(lines)


def read_env_file(path: Path) -> dict:
    """只讀檔案內容（不混入目前程序的環境變數）；同鍵以最後一個非空值為準。"""
    values = {}
    if path and path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
                if value or key not in values:
                    values[key] = value
    return values


def init_env(env_file: Path, dev_env_file: Path | None, out=print) -> bool:
    if env_file.exists():
        return False
    dev = {k: v for k, v in read_env_file(dev_env_file).items() if k in FROM_DEV}
    env_file.write_text(render_demo_env(dev), encoding="utf-8", newline="\n")
    missing = [k for k in ("OPENAI_API_KEY",) if not dev.get(k)]
    out(f"已建立 {env_file.name}（隨機產生：{'、'.join(GENERATED_SECRETS)}；值不顯示）")
    if missing:
        out(f"  .env.dev 沒有 {'、'.join(missing)}：付費功能（菜單生成、知識向量化）不可用")
    return True


def set_key(env_file: Path, key: str, value: str) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in value:
        raise ValueError("鍵名或值格式不正確")
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="單機 demo 輔助指令")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init-env")
    init.add_argument("--env-file", default=".env.demo")
    init.add_argument("--dev-env", default=".env.dev")
    setp = sub.add_parser("set")
    setp.add_argument("--env-file", default=".env.demo")
    setp.add_argument("key")
    setp.add_argument("value")
    sub.add_parser("tunnel-url")
    args = parser.parse_args(argv)

    if args.cmd == "init-env":
        if not init_env(REPO_ROOT / args.env_file, REPO_ROOT / args.dev_env):
            print(f"{args.env_file} 已存在，沿用")
        return 0
    if args.cmd == "set":
        set_key(REPO_ROOT / args.env_file, args.key, args.value)
        return 0
    url = extract_tunnel_url(sys.stdin.read())
    if not url:
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
