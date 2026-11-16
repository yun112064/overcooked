"""單機 demo 模式（compose.demo.yaml，scripts/demo-up.ps1 呼叫）：.env.demo 產生、補鍵、改寫單一鍵、擷取快速通道網址。

  python -m ops.demo init-env [--env-file .env.demo] [--dev-env .env.dev]     不存在才建立；只印鍵名
  python -m ops.demo ensure-keys [--env-file .env.demo] [--dev-env .env.dev]  只補上缺少的鍵（既有的值一律不改）；只印鍵名
  python -m ops.demo set --env-file .env.demo KEY [VALUE]                     改寫（或新增）一個鍵；省略 VALUE 即清空
  python -m ops.demo tunnel-url < cloudflared.log                             印出 https://*.trycloudflare.com；找不到回 1

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
                     "INFERENCE_TOKEN", "INTERNAL_API_TOKEN",
                     "SIM_MARKET_HANDOFF_KEY", "ASSISTANT_SERVICE_TOKEN", "ASSISTANT_DB_PASSWORD")
# 從 .env.dev 帶入（沒有就留空）；ASSISTANT_* 是 Git 之外的語料目錄與重排模型權重目錄
FROM_DEV = ("OPENAI_API_KEY", "KNOWLEDGE_AUTHORIZATION_REF", "INFERENCE_DEVICE",
            "ASSISTANT_DATA_DIR", "ASSISTANT_RERANKER_MODEL_DIR")
# T04 營養證據綁定：預設留空（POST /t04-data 回 422 NUTRITION_VERSION_REQUIRED），使用者確認綁定後才填入
GENERATION_KEYS = ("GENERATION_RECIPE_RELEASE_ID", "GENERATION_NUTRITION_IMPORT_ID", "GENERATION_FOOD_MAPPING_VERSION")
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def extract_tunnel_url(text: str) -> str | None:
    """cloudflared 快速通道日誌中的公開網址（取最後一個，容器重建後以新的為準）；api.trycloudflare.com 是註冊端點不算。"""
    urls = [u for u in _TUNNEL_URL.findall(text or "") if u.lower() != "https://api.trycloudflare.com"]
    return urls[-1].lower() if urls else None


def _sections(dev_env: dict, token) -> list[list[str]]:
    """.env.demo 的內容，分段（註解＋鍵）；ensure-keys 只補缺少的鍵與該段註解。"""
    return [
        ["# 單機 demo 設定（scripts/demo-up.ps1 產生；不進 Git）",
         "# PUBLIC_HOSTNAME 由 demo-up 從 cloudflared 日誌寫入（快速通道每次重開都會變）",
         "PUBLIC_HOSTNAME="],
        [f"{key}={token()}" for key in GENERATED_SECRETS],
        [f"{key}={dev_env.get(key, '')}" for key in FROM_DEV] + ["DEMO_YOLO_PORT=8775"],
        ["# 模擬超市公開網址：demo-up 從第二條快速通道寫入；留空時交接回 503 MARKET_UNAVAILABLE（不發出失效連結）",
         "SIM_MARKET_URL="],
        ["# T04 營養證據綁定（預設留空：POST /t04-data 回 422 NUTRITION_VERSION_REQUIRED）。",
         "# release_data/nutrition-v1 隨附 recipe-1103-v3-dd4f75c13772 / "
         "ca92a38b011998bf0e3b8163ce710c8b6228f8ecc0d71cf6e885ea25de6ba976 / food1018-v3-fd0e3666f627f0a8d6a4，",
         "# 綁定的是 1103_V3 工作簿而不是 demo 的 recipe_dataset_1027；使用者確認綁定後才填入。"]
        + [f"{key}=" for key in GENERATION_KEYS],
    ]


def render_demo_env(dev_env: dict, token=lambda: secrets.token_hex(32)) -> str:
    return "\n".join("\n".join(section) + "\n" for section in _sections(dev_env, token))


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


def _dev_values(dev_env_file: Path | None) -> dict:
    return {k: v for k, v in read_env_file(dev_env_file).items() if k in FROM_DEV}


def init_env(env_file: Path, dev_env_file: Path | None, out=print) -> bool:
    if env_file.exists():
        return False
    dev = _dev_values(dev_env_file)
    env_file.write_text(render_demo_env(dev), encoding="utf-8", newline="\n")
    missing = [k for k in ("OPENAI_API_KEY",) if not dev.get(k)]
    out(f"已建立 {env_file.name}（隨機產生：{'、'.join(GENERATED_SECRETS)}；值不顯示）")
    if missing:
        out(f"  .env.dev 沒有 {'、'.join(missing)}：付費功能（菜單生成、知識向量化）不可用")
    return True


def ensure_keys(env_file: Path, dev_env_file: Path | None, out=print, token=lambda: secrets.token_hex(32)) -> list[str]:
    """在既有 .env.demo 末尾補上缺少的鍵（新秘密隨機產生），既有內容逐位元組保留；第二次執行不做任何事。"""
    present = set(read_env_file(env_file))
    added, lines = [], []
    for section in _sections(_dev_values(dev_env_file), token):
        missing = [line for line in section if (m := _KEY_LINE.match(line)) and m.group(1) not in present]
        if missing:
            lines += [line for line in section if line.startswith("#")] + missing
            added += [_KEY_LINE.match(line).group(1) for line in missing]
    if added:
        raw = env_file.read_bytes()
        prefix = b"" if not raw or raw.endswith(b"\n") else b"\n"
        env_file.write_bytes(raw + prefix + ("\n".join(lines) + "\n").encode("utf-8"))
        out(f"{env_file.name} 已補上：{'、'.join(added)}（值不顯示）")
    else:
        out(f"{env_file.name} 的鍵已齊全")
    return added


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
    for name in ("init-env", "ensure-keys"):
        p = sub.add_parser(name)
        p.add_argument("--env-file", default=".env.demo")
        p.add_argument("--dev-env", default=".env.dev")
    setp = sub.add_parser("set")
    setp.add_argument("--env-file", default=".env.demo")
    setp.add_argument("key")
    setp.add_argument("value", nargs="?", default="", help="省略即改成空值（PowerShell 5.1 會丟掉空字串參數）")
    sub.add_parser("tunnel-url")
    args = parser.parse_args(argv)

    if args.cmd == "init-env":
        if not init_env(REPO_ROOT / args.env_file, REPO_ROOT / args.dev_env):
            print(f"{args.env_file} 已存在，沿用")
        return 0
    if args.cmd == "ensure-keys":
        env_file = REPO_ROOT / args.env_file
        if not env_file.is_file():
            print(f"找不到 {args.env_file}（先執行 init-env）", file=sys.stderr)
            return 1
        ensure_keys(env_file, REPO_ROOT / args.dev_env)
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
