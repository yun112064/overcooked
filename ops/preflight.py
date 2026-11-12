"""T17 正式設定檢查（單機）：python -m ops.preflight --env-file .env.prod

只檢查設定是否完整、安全（不連線、不呼叫外部服務），錯誤訊息只列變數名稱，絕不印出秘密值。
回傳 (errors, warnings)：errors 阻擋部署；warnings 例如 YOLO／知識／小幫手未設定（該功能將顯示不可用）。

整套系統跑在團隊電腦（2026-11-12 決議）；對外 HTTPS 一律由 Cloudflare named tunnel＋自己的網域處理，
Caddy 只在內部網路提供 HTTP。trycloudflare.com 快速通道只用於 compose.standby.yaml 的備援，不能當正式網域。
"""
import argparse
import sys
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

PLACEHOLDER_MARKERS = ("replace", "placeholder", "changeme", "change_me", "example")
# 同一台電腦上的原生服務（YOLO、T11 知識、飲食小幫手檢索）；未設定時該功能顯示不可用，其餘功能照常
LOCAL_SERVICES = (("RECOGNITION_SERVICE_URL", "RECOGNITION_SERVICE_TOKEN", "辨識"),
                  ("KNOWLEDGE_SERVICE_URL", "KNOWLEDGE_SERVICE_TOKEN", "營養知識"),
                  ("ASSISTANT_OPENAI_SERVICE_URL", "ASSISTANT_SERVICE_TOKEN", "飲食小幫手"))
# 在容器裡 localhost 指的是容器自己，連不到主機上的原生服務
CONTAINER_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


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
    c = PurePosixPath(child.replace("\\", "/").lower())
    p = PurePosixPath(parent.replace("\\", "/").lower())
    return c == p or p in c.parents


def _public(env: dict, errors: list) -> None:
    _secret(env, "CLOUDFLARE_TUNNEL_TOKEN", 32, errors)
    host = env.get("PUBLIC_HOSTNAME") or ""
    if not host or host in ("localhost", "127.0.0.1") or "." not in host or _placeholder(host):
        errors.append("PUBLIC_HOSTNAME 需設為正式網域（例如 meal.<你的網域>）")
    elif host.lower().endswith(".trycloudflare.com"):
        errors.append("PUBLIC_HOSTNAME 不可用 trycloudflare.com 快速通道（只限備援）；請用自己的網域")
    origins = [o.strip().rstrip("/") for o in (env.get("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
    if not host or origins != [f"https://{host}"]:
        errors.append("ALLOWED_ORIGINS 必須只有 https://<PUBLIC_HOSTNAME>")
    if (env.get("COOKIE_SECURE") or "true").lower() != "true":
        errors.append("COOKIE_SECURE 必須為 true")


def _databases(env: dict, errors: list) -> None:
    _secret(env, "POSTGRES_PASSWORD", 16, errors)
    db_url = env.get("DATABASE_URL") or ""
    if not db_url:
        errors.append("DATABASE_URL 未設定（填 install_real_nutrition 產生的連線）")
    elif urlsplit(db_url.replace("+psycopg", "")).hostname != "db":
        errors.append("DATABASE_URL 必須連到 Compose 內部的 db 服務")
    for key in ("KNOWLEDGE_DB_PASSWORD", "ASSISTANT_DB_PASSWORD"):
        _secret(env, key, 16, errors)


def _roots(env: dict, errors: list) -> None:
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


def _services(env: dict, errors: list, warnings: list) -> None:
    for url_key, token_key, label in LOCAL_SERVICES:
        url = env.get(url_key) or ""
        if not url:
            warnings.append(f"{url_key} 未設定：{label}將顯示服務不可用")
            continue
        if (urlsplit(url).hostname or "") in CONTAINER_LOOPBACK:
            errors.append(f"{url_key} 在容器內連不到主機的 localhost；請用 http://host.docker.internal:<埠>")
        _secret(env, token_key, 32, errors)
    # 飲食小幫手由雲端模型生成：本機檢索後回呼 backend 的 /internal/assistant/chat，以 T10 額度呼叫 OpenAI
    if not env.get("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY 未設定：飲食小幫手改用雲端模型生成，必須設定")
    if env.get("INTERNAL_API_TOKEN"):
        _secret(env, "INTERNAL_API_TOKEN", 32, errors)
    else:
        errors.append("INTERNAL_API_TOKEN 未設定：飲食小幫手呼叫雲端模型與內部 API 需要")


def check(env: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    _public(env, errors)
    _databases(env, errors)
    _roots(env, errors)
    _services(env, errors, warnings)
    return errors, warnings


def main(argv=None) -> int:
    from pathlib import Path

    from ops.env import load_env
    parser = argparse.ArgumentParser(description="T17 正式設定檢查（不印秘密值）")
    parser.add_argument("--env-file", type=Path, default=Path(".env.prod"))
    args = parser.parse_args(argv)
    errors, warnings = check(load_env(args.env_file))
    for w in warnings:
        print(f"警告：{w}")
    for e in errors:
        print(f"錯誤：{e}")
    print("preflight ok" if not errors else "preflight FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
