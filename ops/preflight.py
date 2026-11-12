"""T17 正式設定檢查：python -m ops.preflight {cloud|team} --env-file <檔案>

只檢查設定是否完整、安全（不連線、不呼叫外部服務），錯誤訊息只列變數名稱，絕不印出秘密值。
回傳 (errors, warnings)：errors 阻擋部署；warnings 例如團隊電腦服務未設定（功能將顯示不可用）。

對外 HTTPS 一律由 Cloudflare Tunnel（named tunnel＋自己的網域）處理；Caddy 只在內部網路提供 HTTP，不簽憑證。
trycloudflare.com 快速通道只用於 compose.standby.yaml 的展示備援，不能當正式網域。
"""
import argparse
import sys
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

PLACEHOLDER_MARKERS = ("replace", "placeholder", "changeme", "change_me", "example")
# 雲端呼叫的團隊電腦服務（YOLO、T11 知識、飲食小幫手檢索）；未設定時該功能顯示不可用，其餘雲端功能照常
TEAM_SERVICES = (("RECOGNITION_SERVICE_URL", "RECOGNITION_SERVICE_TOKEN", "辨識"),
                 ("KNOWLEDGE_SERVICE_URL", "KNOWLEDGE_SERVICE_TOKEN", "營養知識"),
                 ("ASSISTANT_OPENAI_SERVICE_URL", "ASSISTANT_SERVICE_TOKEN", "飲食小幫手"))


def _placeholder(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def _secret(env: dict, key: str, min_len: int, errors: list) -> None:
    value = env.get(key) or ""
    if not value:
        errors.append(f"{key} 未設定")
    elif len(value) < min_len:
        errors.append(f"{key} 太短（至少 {min_len} 字元）")
    elif _placeholder(value):
        errors.append(f"{key} 仍是範例值")


def _absolute(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _inside(child: str, parent: str) -> bool:
    c, p = PurePosixPath(child.replace("\\", "/")), PurePosixPath(parent.replace("\\", "/"))
    return c == p or p in c.parents


def _cloud(env: dict, errors: list, warnings: list) -> None:
    _secret(env, "CLOUDFLARE_TUNNEL_TOKEN", 32, errors)
    host = env.get("PUBLIC_HOSTNAME") or ""
    if not host or host in ("localhost", "127.0.0.1") or "." not in host or _placeholder(host):
        errors.append("PUBLIC_HOSTNAME 需設為正式網域（例如 meal.<你的網域>）")
    elif host.lower().endswith(".trycloudflare.com"):
        errors.append("PUBLIC_HOSTNAME 不可用 trycloudflare.com 快速通道（只限 demo 備援）；請用自己的網域")
    origins = [o.strip().rstrip("/") for o in (env.get("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
    if not host or origins != [f"https://{host}"]:
        errors.append("ALLOWED_ORIGINS 必須只有 https://<PUBLIC_HOSTNAME>")
    if (env.get("COOKIE_SECURE") or "true").lower() != "true":
        errors.append("COOKIE_SECURE 必須為 true")
    _secret(env, "POSTGRES_PASSWORD", 16, errors)
    db_url = env.get("DATABASE_URL") or ""
    if not db_url:
        errors.append("DATABASE_URL 未設定（填 install_real_nutrition 產生的連線）")
    elif urlsplit(db_url.replace("+psycopg", "")).hostname != "db":
        errors.append("DATABASE_URL 必須連到 Compose 內部的 db 服務")
    roots = {}
    for key in ("PROJECT_DATA_ROOT", "PROJECT_RUNTIME_ROOT", "PROJECT_BACKUP_ROOT", "BACKUP_MIRROR_ROOT"):
        value = env.get(key) or ""
        if not value:
            errors.append(f"{key} 未設定" + ("（備份必須另存獨立位置）" if key == "BACKUP_MIRROR_ROOT" else ""))
        elif not _absolute(value):
            errors.append(f"{key} 必須是絕對路徑")
        else:
            roots[key] = value
    for key in ("PROJECT_BACKUP_ROOT", "BACKUP_MIRROR_ROOT"):
        if key in roots and "PROJECT_DATA_ROOT" in roots and _inside(roots[key], roots["PROJECT_DATA_ROOT"]):
            errors.append(f"{key} 不可位於 Data Root 之內")
    if "BACKUP_MIRROR_ROOT" in roots and "PROJECT_BACKUP_ROOT" in roots and \
            _inside(roots["BACKUP_MIRROR_ROOT"], roots["PROJECT_BACKUP_ROOT"]):
        errors.append("BACKUP_MIRROR_ROOT 不可位於 PROJECT_BACKUP_ROOT 之內")

    any_team = False
    for url_key, token_key, label in TEAM_SERVICES:
        url = env.get(url_key) or ""
        if not url:
            warnings.append(f"{url_key} 未設定：{label}將顯示服務不可用")
            continue
        any_team = True
        if urlsplit(url).scheme != "https":
            errors.append(f"{url_key} 必須是 https（經 Cloudflare Tunnel）")
        _secret(env, token_key, 32, errors)
    if any_team:
        _secret(env, "TEAM_ACCESS_CLIENT_ID", 8, errors)
        _secret(env, "TEAM_ACCESS_CLIENT_SECRET", 32, errors)
    # 飲食小幫手改由雲端模型生成：團隊電腦檢索後回呼雲端 /internal/assistant/chat，雲端以 T10 額度呼叫 OpenAI
    if not env.get("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY 未設定：飲食小幫手改用雲端模型生成，必須設定")
    if env.get("INTERNAL_API_TOKEN"):
        _secret(env, "INTERNAL_API_TOKEN", 32, errors)
    else:
        errors.append("INTERNAL_API_TOKEN 未設定：團隊電腦呼叫雲端模型與內部 API 需要")


def _team(env: dict, errors: list, warnings: list) -> None:
    _secret(env, "TEAM_TUNNEL_TOKEN", 32, errors)
    for key in ("KNOWLEDGE_SERVICE_TOKEN", "INFERENCE_TOKEN", "ASSISTANT_SERVICE_TOKEN"):
        _secret(env, key, 32, errors)
    for key in ("KNOWLEDGE_DB_PASSWORD", "ASSISTANT_DB_PASSWORD"):
        _secret(env, key, 16, errors)
    for key in ("TEAM_BACKUP_ROOT", "BACKUP_MIRROR_ROOT"):
        if not env.get(key):
            errors.append(f"{key} 未設定")
        elif not _absolute(env[key]):
            errors.append(f"{key} 必須是絕對路徑")
    # 團隊電腦只經雲端內部 API（Access＋Bearer）存取佇列、向量化與雲端模型，不可持有雲端資料庫連線
    if env.get("DATABASE_URL"):
        errors.append("DATABASE_URL 不應設定在團隊電腦（雲端資料庫不直接公開；改用 CLOUD_INTERNAL_URL）")
    url = env.get("CLOUD_INTERNAL_URL") or ""
    if not url:
        errors.append("CLOUD_INTERNAL_URL 未設定：飲食小幫手經雲端模型生成回答需要")
        return
    if urlsplit(url).scheme != "https":
        errors.append("CLOUD_INTERNAL_URL 必須是 https（經 Cloudflare Tunnel 的內部 hostname）")
    _secret(env, "CLOUD_INTERNAL_TOKEN", 32, errors)
    _secret(env, "CLOUD_ACCESS_CLIENT_ID", 8, errors)
    _secret(env, "CLOUD_ACCESS_CLIENT_SECRET", 32, errors)


def check(env: dict, *, side: str) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    (_cloud if side == "cloud" else _team)(env, errors, warnings)
    return errors, warnings


def main(argv=None) -> int:
    from pathlib import Path

    from ops.env import load_env
    parser = argparse.ArgumentParser(description="T17 正式設定檢查（不印秘密值）")
    parser.add_argument("side", choices=("cloud", "team"))
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args(argv)
    errors, warnings = check(load_env(args.env_file), side=args.side)
    for w in warnings:
        print(f"警告：{w}")
    for e in errors:
        print(f"錯誤：{e}")
    print("preflight ok" if not errors else "preflight FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
