"""單機 demo 備援（compose.standby.yaml，scripts/standby-up.ps1 呼叫）：.env.standby 產生、改寫單一鍵、合併資料庫設定、擷取快速通道網址。

  python -m ops.standby init-env [--env-file .env.standby] [--dev-env .env.dev]   不存在才建立；只印鍵名
  python -m ops.standby set --env-file .env.standby KEY VALUE                     改寫（或新增）一個鍵
  python -m ops.standby merge --env-file .env.standby --from .dev/standby-db.env     帶入 install_real_nutrition 的輸出
  python -m ops.standby get --env-file .env.standby KEY                           印出一個非秘密的鍵（例如 PUBLIC_HOSTNAME）
  python -m ops.standby tunnel-url < cloudflared.log                           印出 https://*.trycloudflare.com；找不到回 1

快速通道（trycloudflare.com）只供展示：網址每次重開都會變、不支援 SSE（飲食小幫手串流回答送不出去）、
沒有可用性保證；正式環境用 compose.prod.yaml 的 named tunnel＋自己的網域。只用標準函式庫；秘密值不輸出到畫面。
"""
import argparse
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TUNNEL_URL = re.compile(r"https://(?:[a-z0-9-]+\.)+trycloudflare\.com(?![a-z0-9.-])", re.IGNORECASE)
# 每次產生的隨機秘密（各 64 個十六進位字元）
GENERATED_SECRETS = ("POSTGRES_PASSWORD", "INFERENCE_TOKEN", "KNOWLEDGE_SERVICE_TOKEN", "ASSISTANT_SERVICE_TOKEN",
                     "INTERNAL_API_TOKEN")
# 從 .env.dev 帶入（沒有就留空）
FROM_DEV = ("OPENAI_API_KEY", "INFERENCE_DEVICE", "KNOWLEDGE_SERVICE_URL", "ASSISTANT_OPENAI_SERVICE_URL")
# install_real_nutrition 輸出、demo 需要的鍵
DB_KEYS = ("DATABASE_URL", "NUTRITION_POLICY_MANIFEST", "NUTRITION_POLICY_MANIFEST_SHA256",
           "GENERATION_RECIPE_RELEASE_ID", "GENERATION_NUTRITION_IMPORT_ID", "GENERATION_FOOD_MAPPING_VERSION",
           "T12_PREVIEW_CONVERSION")
PUBLIC_KEYS = ("PUBLIC_HOSTNAME", "STANDBY_YOLO_PORT", "STANDBY_DATABASE", "INFERENCE_DEVICE")


def extract_tunnel_url(text: str) -> str | None:
    """cloudflared 快速通道日誌中的公開網址（取最後一個，容器重建後以新的為準）；api.trycloudflare.com 是註冊端點不算。"""
    urls = [u for u in _TUNNEL_URL.findall(text or "") if u.lower() != "https://api.trycloudflare.com"]
    return urls[-1].lower() if urls else None


def render_demo_env(dev_env: dict, token=lambda: secrets.token_hex(32)) -> str:
    lines = ["# 單機 demo 備援設定（scripts/standby-up.ps1 產生；不進 Git）",
             "# PUBLIC_HOSTNAME 由 standby-up 從 cloudflared 日誌寫入（快速通道每次重開都會變）",
             "PUBLIC_HOSTNAME=", "STANDBY_DATABASE=meal_planner_standby", "STANDBY_YOLO_PORT=8776", ""]
    lines += [f"{key}={token()}" for key in GENERATED_SECRETS]
    lines += ["", "# 團隊電腦服務在同一台電腦：容器以 host.docker.internal:<埠> 連線；留空則顯示不可用"]
    lines += [f"{key}={dev_env.get(key, '')}" for key in FROM_DEV]
    lines.append("")
    return "\n".join(lines)


def read_env_file(path: Path | None) -> dict:
    """只讀檔案內容（不混入目前程序的環境變數）；同鍵以最後一個非空值為準。"""
    values = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
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
    out(f"已建立 {env_file.name}（隨機產生：{'、'.join(GENERATED_SECRETS)}；值不顯示）")
    if not dev.get("OPENAI_API_KEY"):
        out("  .env.dev 沒有 OPENAI_API_KEY：AI 菜單生成、知識說明與雲端模型小幫手不可用")
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


def merge_db_env(env_file: Path, source: Path) -> list[str]:
    values = read_env_file(source)
    missing = [k for k in DB_KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"資料庫設定缺少：{', '.join(missing)}")
    for key in DB_KEYS:
        set_key(env_file, key, values[key])
    return list(DB_KEYS)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="單機 demo 輔助指令")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init-env")
    init.add_argument("--env-file", default=".env.standby")
    init.add_argument("--dev-env", default=".env.dev")
    setp = sub.add_parser("set")
    setp.add_argument("--env-file", default=".env.standby")
    setp.add_argument("key")
    setp.add_argument("value")
    merge = sub.add_parser("merge")
    merge.add_argument("--env-file", default=".env.standby")
    merge.add_argument("--from", dest="source", required=True)
    get = sub.add_parser("get")
    get.add_argument("--env-file", default=".env.standby")
    get.add_argument("key", choices=PUBLIC_KEYS)
    sub.add_parser("tunnel-url")
    args = parser.parse_args(argv)

    if args.cmd == "init-env":
        if not init_env(REPO_ROOT / args.env_file, REPO_ROOT / args.dev_env):
            print(f"{args.env_file} 已存在，沿用")
        return 0
    if args.cmd == "set":
        set_key(REPO_ROOT / args.env_file, args.key, args.value)
        return 0
    if args.cmd == "merge":
        try:
            keys = merge_db_env(REPO_ROOT / args.env_file, REPO_ROOT / args.source)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"已帶入：{'、'.join(keys)}（值不顯示）")
        return 0
    if args.cmd == "get":
        print(read_env_file(REPO_ROOT / args.env_file).get(args.key, ""))
        return 0
    url = extract_tunnel_url(sys.stdin.read())
    if not url:
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
