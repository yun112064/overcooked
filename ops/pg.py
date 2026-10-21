"""PostgreSQL 用戶端工具轉接：在資料庫容器內（docker exec）或本機 PATH 執行 pg_dump／pg_restore／psql。

正式環境資料庫不對外開埠，因此預設在容器內以 Unix socket 連線（容器內本機信任），不需在主機保存密碼。
"""
import os
import re
import subprocess
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9_]{1,63}$")


class PgError(RuntimeError):
    pass


def _check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise PgError(f"不合法的資料庫名稱：{name!r}")
    return name


def find_container_for_port(port: int) -> str | None:
    """找出發佈指定主機埠的容器（測試與本機演練用）。"""
    try:
        out = subprocess.run(["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=30, check=True).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[0] if out else None


class PgTools:
    def __init__(self, container: str | None = None, user: str = "postgres", host: str | None = None,
                 port: int | None = None, password: str | None = None):
        self.container, self.user, self.host, self.port, self.password = container, user, host, port, password

    def _cmd(self, tool: str, *args: str) -> list[str]:
        if self.container:
            return ["docker", "exec", "-i", self.container, tool, "-U", self.user, *args]
        conn = []
        if self.host:
            conn += ["-h", self.host]
        if self.port:
            conn += ["-p", str(self.port)]
        return [tool, *conn, "-U", self.user, *args]

    def _run(self, cmd: list[str], *, stdin=None, stdout=None, input_text: str | None = None) -> str:
        env = dict(os.environ)
        if self.password and not self.container:
            env["PGPASSWORD"] = self.password
        proc = subprocess.run(cmd, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
                              stderr=subprocess.PIPE, input=input_text.encode() if input_text is not None else None,
                              env=env, timeout=3600)
        if proc.returncode != 0:
            tool = next((c for c in cmd if c in ("psql", "pg_dump", "pg_restore")), cmd[0])
            raise PgError(f"{tool} 失敗：{proc.stderr.decode(errors='replace').strip()[:500]}")
        return proc.stdout.decode() if stdout is None and proc.stdout is not None else ""

    # --- 查詢 ---

    def query(self, database: str, sql: str) -> list[list[str]]:
        out = self._run(self._cmd("psql", "-X", "-q", "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1",
                                  "-d", _check_name(database), "-c", sql))
        return [line.split("\t") for line in out.splitlines() if line != ""]

    def run_sql(self, database: str, sql: str) -> None:
        """以 stdin 執行多句 SQL（可含 COPY ... FROM STDIN 資料）。"""
        self._run(self._cmd("psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", _check_name(database)),
                  input_text=sql)

    def database_exists(self, database: str) -> bool:
        return bool(self.query("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{_check_name(database)}'"))

    def create_db(self, database: str) -> None:
        self.query("postgres", f'CREATE DATABASE "{_check_name(database)}"')

    def drop_db(self, database: str) -> None:
        self.query("postgres", f'DROP DATABASE IF EXISTS "{_check_name(database)}" WITH (FORCE)')

    # --- 傾印與還原 ---

    def dump(self, database: str, out_path: Path) -> None:
        with open(out_path, "wb") as fh:
            self._run(self._cmd("pg_dump", "-Fc", "--no-owner", "--no-privileges", "-d", _check_name(database)),
                      stdout=fh)

    def restore(self, database: str, in_path: Path) -> None:
        with open(in_path, "rb") as fh:
            self._run(self._cmd("pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
                                "-d", _check_name(database)), stdin=fh)

    def copy_out(self, database: str, sql: str, out_path: Path) -> None:
        with open(out_path, "wb") as fh:
            self._run(self._cmd("psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", _check_name(database),
                                "-c", f"COPY ({sql}) TO STDOUT"), stdout=fh)
