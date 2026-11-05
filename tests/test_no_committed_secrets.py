"""API 金鑰不可進 Git：掃描所有被追蹤的檔案，找 OpenAI 等金鑰樣式；金鑰只能放在 .env／.env.dev（已在 .gitignore）。"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = re.compile(rb'sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}')
ENV_FILES = ('.env', '.env.dev')


def tracked_files():
    try:
        out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('not a git checkout')
    return [ROOT / p.decode() for p in out.split(b'\0') if p]


def test_no_api_key_in_tracked_files():
    leaks = [str(p.relative_to(ROOT)) for p in tracked_files()
             if p.is_file() and p.stat().st_size < 20_000_000 and PATTERNS.search(p.read_bytes())]
    assert leaks == [], f'API key pattern found in tracked files: {leaks}'


def test_env_files_with_keys_are_ignored():
    for name in ENV_FILES:
        result = subprocess.run(['git', 'check-ignore', '-q', name], cwd=ROOT)
        assert result.returncode == 0, f'{name} must be listed in .gitignore'
