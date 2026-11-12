"""T17 正式設定檢查：python -m ops.preflight --env-file .env.prod

只檢查設定是否完整、安全（不連線、不呼叫外部服務），錯誤訊息只列變數名稱，絕不印出秘密值。
回傳 (errors, warnings)：errors 阻擋啟動；warnings 例如沒有獨立備份位置、沒有 OpenAI 金鑰。

部署方式與 t11-t20-demo 相同（2026-11-12 決議）：整套在團隊電腦，對外只經 trycloudflare.com 快速通道，
所以不檢查網域與 tunnel 權杖；PUBLIC_HOSTNAME 由 prod-up 從 cloudflared 日誌寫入。
"""
import argparse
import sys
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

from ops.prodenv import GENERATED_SECRETS

PLACEHOLDER_MARKERS = ("replace", "placeholder", "changeme", "change_me", "example")
REQUIRED_PATHS = ("PROJECT_DATA_ROOT", "PROJECT_RUNTIME_ROOT", "PROJECT_BACKUP_ROOT", "ASSISTANT_DATA_DIR",
                  "ASSISTANT_RERANKER_MODEL_DIR")


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


def check(env: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for key in GENERATED_SECRETS:
        _secret(env, key, 32, errors)
    host = env.get("PUBLIC_HOSTNAME") or ""
    if host and not host.lower().endswith(".trycloudflare.com"):
        warnings.append("PUBLIC_HOSTNAME 不是 trycloudflare.com；prod-up 開通道時會改寫")
    if (env.get("COOKIE_SECURE") or "true").lower() != "true":
        errors.append("COOKIE_SECURE 必須為 true")
    db_url = env.get("DATABASE_URL") or ""
    if db_url and urlsplit(db_url.replace("+psycopg", "")).hostname != "db":
        errors.append("DATABASE_URL 必須連到 Compose 內部的 db 服務")

    roots = {}
    for key in REQUIRED_PATHS + ("BACKUP_MIRROR_ROOT",):
        value = env.get(key) or ""
        if not value:
            if key == "BACKUP_MIRROR_ROOT":
                warnings.append("BACKUP_MIRROR_ROOT 未設定：每日備份只在本機，不符合「獨立位置」要求")
            else:
                errors.append(f"{key} 未設定")
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

    if not env.get("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY 未設定：AI 菜單生成與知識說明不可用（飲食小幫手用本機模型，不受影響）")
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
