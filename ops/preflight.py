"""T17 正式設定檢查：python -m ops.preflight {cloud|team} --env-file <檔案>

只檢查設定是否完整、安全（不連線、不呼叫外部服務），錯誤訊息只列變數名稱，絕不印出秘密值。
回傳 (errors, warnings)：errors 阻擋部署；warnings 例如團隊電腦服務未設定（功能將顯示不可用）。
"""
import argparse
import sys
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

PLACEHOLDER_MARKERS = ("replace", "placeholder", "changeme", "change_me", "example")


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
    origins = [o.strip().rstrip("/") for o in (env.get("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
    if not host or origins != [f"https://{host}"]:
        errors.append("ALLOWED_ORIGINS 必須只有 https://<PUBLIC_HOSTNAME>")
    if (env.get("COOKIE_SECURE") or "true").lower() != "true":
        errors.append("COOKIE_SECURE 必須為 true")
    _secret(env, "POSTGRES_PASSWORD", 16, errors)
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
    for prefix, label in (("RECOGNITION", "辨識"), ("KNOWLEDGE", "營養知識")):
        url = env.get(f"{prefix}_SERVICE_URL") or ""
        if not url:
            warnings.append(f"{prefix}_SERVICE_URL 未設定：{label}將顯示服務不可用")
            continue
        any_team = True
        if urlsplit(url).scheme != "https":
            errors.append(f"{prefix}_SERVICE_URL 必須是 https（經 Cloudflare Tunnel）")
        _secret(env, f"{prefix}_SERVICE_TOKEN", 32, errors)
    if any_team:
        _secret(env, "TEAM_ACCESS_CLIENT_ID", 8, errors)
        _secret(env, "TEAM_ACCESS_CLIENT_SECRET", 32, errors)
    if not env.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY 未設定：AI 回退與知識向量化將不可用")
    if env.get("INTERNAL_API_TOKEN"):
        _secret(env, "INTERNAL_API_TOKEN", 32, errors)
    else:
        warnings.append("INTERNAL_API_TOKEN 未設定：團隊電腦的補抓佇列與建索引向量化（雲端內部 API）將無法使用")


def _team(env: dict, errors: list, warnings: list) -> None:
    _secret(env, "TEAM_TUNNEL_TOKEN", 32, errors)
    _secret(env, "KNOWLEDGE_TOKEN", 32, errors)
    _secret(env, "INFERENCE_TOKEN", 32, errors)
    _secret(env, "KNOWLEDGE_DB_PASSWORD", 16, errors)
    for key in ("TEAM_BACKUP_ROOT", "BACKUP_MIRROR_ROOT"):
        if not env.get(key):
            errors.append(f"{key} 未設定")
        elif not _absolute(env[key]):
            errors.append(f"{key} 必須是絕對路徑")
    # pre-review #6：團隊電腦只經雲端內部 API（Access＋Bearer）存取佇列與向量化，不可持有雲端資料庫連線
    if env.get("DATABASE_URL"):
        errors.append("DATABASE_URL 不應設定在團隊電腦（雲端資料庫不直接公開；改用 CLOUD_INTERNAL_URL）")
    url = env.get("CLOUD_INTERNAL_URL") or ""
    if not url:
        warnings.append("CLOUD_INTERNAL_URL 未設定：crawl --queued、index build 與佇列狀態將無法使用")
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
