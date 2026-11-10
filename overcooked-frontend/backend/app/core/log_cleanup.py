
import argparse
import os
import re
import stat
import time
from pathlib import Path

from app.core.logging_config import scheduler_logger


RETENTION_DAYS = 14
MAX_TOTAL_BYTES = 200 * 1024 * 1024

DATA_ROOT = Path(os.getenv("PROJECT_DATA_ROOT", "/app/data"))
LOG_DIR = DATA_ROOT / "logs"

# 支援舊版數字與新版時間戳記輪替檔案；同刻度重複輪替會加 -序號
ROTATED_PATTERN = re.compile(
    r".+\.jsonl\.(?:[1-9][0-9]*|[0-9]{8}T[0-9]{12}Z(?:-[1-9][0-9]*)?)$"
)


def opened_files():
    """查詢目前程序開啟的檔案，使用 device/inode 識別。"""
    opened = set()

    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue

            try:
                fds = list((proc / "fd").iterdir())
            except FileNotFoundError:
                continue

            for fd in fds:
                try:
                    info = fd.stat()
                except FileNotFoundError:
                    continue

                if stat.S_ISREG(info.st_mode):
                    opened.add((info.st_dev, info.st_ino))

    except OSError as exc:
        raise RuntimeError(
            f"無法完整檢查開啟中的檔案：{exc}"
        ) from exc

    return opened


def list_logs():
    """取得一般檔案，略過目錄與符號連結。"""
    if LOG_DIR.is_symlink() or not LOG_DIR.is_dir():
        raise RuntimeError(f"無效的日誌目錄：{LOG_DIR}")

    files = []

    for path in LOG_DIR.iterdir():
        info = path.lstat()

        if stat.S_ISREG(info.st_mode):
            files.append((path, info))

    return files


def cleanup(apply=False):
    files = list_logs()

    try:
        opened = opened_files()
    except RuntimeError as exc:
        if apply:
            raise SystemExit(
                f"安全檢查失敗，停止刪除：{exc}"
            )

        print(f"警告：{exc}；僅供預覽")
        opened = set()

    total = sum(info.st_size for _, info in files)
    remaining = total
    now = time.time()

    candidates = []
    protected = 0

    # 尋找可清理的輪替檔案
    for path, info in files:
        if not ROTATED_PATTERN.fullmatch(path.name):
            continue

        hold = LOG_DIR / f"{path.name}.hold"
        identity = (info.st_dev, info.st_ino)

        if hold.exists() or identity in opened:
            protected += 1
            continue

        candidates.append((path, info))

    candidates.sort(key=lambda item: item[1].st_mtime_ns)

    planned = []
    selected = set()

    # 1. 優先清理超過 14 天的檔案
    for path, info in candidates:
        if now - info.st_mtime > RETENTION_DAYS * 86400:
            planned.append((path, info, "超過14天"))
            selected.add(path)
            remaining -= info.st_size

    # 2. 若仍超過 200 MiB，繼續由舊到新選擇
    for path, info in candidates:
        if remaining <= MAX_TOTAL_BYTES:
            break

        if path in selected:
            continue

        planned.append((path, info, "超過空間預算"))
        remaining -= info.st_size

    mode = "正式執行" if apply else "預覽，不刪除"

    print(f"=== 日誌清理：{mode} ===")
    print(f"日誌目錄：{LOG_DIR}")
    print(f"目前大小：{total} Bytes")
    print(f"受保護輪替檔：{protected} 個")
    print(f"預計清理：{len(planned)} 個檔案")

    deleted = 0

    for path, previous, reason in planned:
        if not apply:
            print(f"[預覽] {path.name}：{reason}")
            continue

        try:
            # 再次確認檔案沒有被變更
            current = path.lstat()
            hold = LOG_DIR / f"{path.name}.hold"

            old_identity = (
                previous.st_dev,
                previous.st_ino,
                previous.st_size,
                previous.st_mtime_ns,
            )

            new_identity = (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            )

            if (
                not stat.S_ISREG(current.st_mode)
                or old_identity != new_identity
                or hold.exists()
            ):
                print(f"[跳過] {path.name}：已變動或受保護")
                continue

            # 刪除前再次檢查是否被開啟
            identity = (current.st_dev, current.st_ino)

            if identity in opened_files():
                print(f"[跳過] {path.name}：仍有程序使用")
                continue

            path.unlink()
            deleted += 1

            print(f"[已刪除] {path.name}：{reason}")

        except OSError as exc:
            print(f"[跳過] {path.name}：{exc}")

    if apply:
        remaining = sum(
            info.st_size for _, info in list_logs()
        )
        print(f"實際刪除：{deleted} 個檔案")

    label = "實際剩餘" if apply else "預估剩餘"
    print(f"{label}：{remaining} Bytes")

    if remaining > MAX_TOTAL_BYTES:
        print("警告：可清理空間不足，仍超過200MiB預算")
        scheduler_logger.warning(
            "Log space budget exceeded",
            extra={
                "event": "log_cleanup_budget_exceeded",
                "source": "scheduler",
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際清理符合政策的輪替日誌",
    )

    args = parser.parse_args()
    cleanup(apply=args.apply)
