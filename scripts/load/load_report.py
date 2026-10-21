"""T18 負載報告：每步延遲樣本 → p95 → 依 AC21 門檻判定，輸出 JSON 與 Markdown。

門檻只能收緊、不能放寬（ticket 18：未達標不得降低門檻）。
dry-run（對假伺服器）或環境資料不完整時，G06 結論一律為 not_verified。
"""
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# AC21：同時 5 人時的 p95 上限（秒）
SPEC_THRESHOLDS_S = {"api": 1.0, "recognition_warm": 10.0, "generation": 90.0}
CATEGORY_LABELS = {
    "api": "一般 API",
    "recognition_warm": "暖機辨識端到端",
    "generation": "生成（含排隊）",
}
# 少於此樣本數的 p95 不具代表性，不可判定通過
MIN_SAMPLES = 20
REQUIRED_USERS = 5


class ThresholdLoweringRefused(ValueError):
    """嘗試放寬或新增門檻；產品條件變更必須回到規劃，不能在測試腳本調整。"""


@dataclass
class Sample:
    step: str
    category: str
    user: int
    iteration: int
    latency_s: float
    ok: bool
    status_code: int | None = None
    started_at: str | None = None
    detail: dict = field(default_factory=dict)


def p95(values: list[float]) -> float | None:
    """nearest-rank p95；沒有樣本回 None（不以 0 代替）。"""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def effective_thresholds(overrides: dict | None = None) -> dict:
    thresholds = dict(SPEC_THRESHOLDS_S)
    for key, value in (overrides or {}).items():
        if key not in SPEC_THRESHOLDS_S:
            raise ThresholdLoweringRefused(f"Unknown threshold {key!r}; only {sorted(SPEC_THRESHOLDS_S)} exist.")
        if float(value) > SPEC_THRESHOLDS_S[key]:
            raise ThresholdLoweringRefused(
                f"Threshold {key} may not be loosened above the spec value {SPEC_THRESHOLDS_S[key]} s.")
        thresholds[key] = float(value)
    return thresholds


def environment_template() -> dict:
    """AC21／G06 要求記錄的環境資料；None 表示尚未填寫。"""
    return {
        "cloud_host": {"provider": None, "machine": None, "cpu": None, "memory": None, "region": None},
        "team_computer": {"machine": None, "cpu": None, "gpu": None, "memory": None, "os": None,
                          "status": None},
        "network": {"team_computer_link": None, "cloud_to_team_rtt_ms": None, "status": None},
        "models": {"generation": None, "embedding": None, "yolo": None, "yolo_warm": None},
        "inputs": {"scenario_file": None, "image_files": None, "dataset_version": None},
        "source_status": {"price_source": None, "knowledge_rag": None, "openai": None, "recognition": None},
        "deployment": {"base_url": None, "git_commit": None, "pricing_version": None},
    }


def missing_fields(env: dict, template: dict | None = None, prefix: str = "") -> list[str]:
    template = template or environment_template()
    missing = []
    for key, sub in template.items():
        value = (env or {}).get(key)
        path = f"{prefix}{key}"
        if isinstance(sub, dict):
            missing += missing_fields(value if isinstance(value, dict) else {}, sub, path + ".")
        elif value is None or value == "":
            missing.append(path)
    return missing


def _summary(group: list[Sample], threshold: float | None) -> dict:
    ok_lat = [s.latency_s for s in group if s.ok]
    errors = sum(1 for s in group if not s.ok)
    value = p95(ok_lat)
    if not group:
        verdict = "no_samples"
    elif len(group) < MIN_SAMPLES:
        verdict = "insufficient_samples"
    elif errors or value is None or (threshold is not None and value > threshold):
        verdict = "fail"
    else:
        verdict = "pass"
    return {"samples": len(group), "errors": errors, "p95_s": value,
            "max_s": max(ok_lat) if ok_lat else None, "threshold_s": threshold, "verdict": verdict}


def build_report(samples: list[Sample], *, environment: dict, dry_run: bool,
                 thresholds: dict | None = None, users: int = REQUIRED_USERS, issues: list | None = None) -> dict:
    limits = effective_thresholds(thresholds)
    categories = {name: _summary([s for s in samples if s.category == name], limit)
                  for name, limit in limits.items()}
    by_mode: dict[str, list[Sample]] = {}
    for s in samples:
        if s.category == "generation":
            by_mode.setdefault(s.detail.get("mode") or "unknown", []).append(s)
    by_step: dict[str, list[Sample]] = {}
    for s in samples:
        by_step.setdefault(s.step, []).append(s)
    missing = missing_fields(environment)
    passed = (not dry_run and not missing and users >= REQUIRED_USERS
              and all(c["verdict"] == "pass" for c in categories.values()))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "concurrent_users": users,
        "thresholds_s": limits,
        "min_samples": MIN_SAMPLES,
        "categories": categories,
        "generation_by_mode": {m: _summary(g, limits["generation"]) for m, g in by_mode.items()},
        "steps": {name: _summary(g, None) for name, g in sorted(by_step.items())},
        "environment": environment,
        "missing_environment": missing,
        "issues": issues or [],
        # pass 只代表此次量測；ticket 18 仍需人工審閱報告後才算驗收
        "g06_verdict": "pass" if passed else ("fail" if not dry_run and any(
            c["verdict"] == "fail" for c in categories.values()) else "not_verified"),
    }


def _fmt(value) -> str:
    return "—" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def render_markdown(report: dict) -> str:
    lines = [
        "# T18 五人負載報告",
        "",
        f"- 產生時間：{report['generated_at']}",
        f"- 模式：{'dry-run（假伺服器，不能作為驗收證據）' if report['dry_run'] else '實機'}",
        f"- 並行使用者：{report['concurrent_users']}",
        f"- G06 結論：**{report['g06_verdict']}**",
        "",
        "| 類別 | 樣本數 | 錯誤 | p95 (s) | 最大 (s) | 門檻 (s) | 判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, c in report["categories"].items():
        lines.append(f"| {CATEGORY_LABELS.get(name, name)} | {c['samples']} | {c['errors']} | {_fmt(c['p95_s'])} "
                     f"| {_fmt(c['max_s'])} | {_fmt(c['threshold_s'])} | {c['verdict']} |")
    lines += ["", "## 生成模式", "", "| 模式 | 樣本數 | 錯誤 | p95 (s) | 判定 |", "|---|---|---|---|---|"]
    for mode, c in report["generation_by_mode"].items():
        lines.append(f"| {mode} | {c['samples']} | {c['errors']} | {_fmt(c['p95_s'])} | {c['verdict']} |")
    lines += ["", "## 各步驟", "", "| 步驟 | 樣本數 | 錯誤 | p95 (s) | 最大 (s) |", "|---|---|---|---|---|"]
    for step, c in report["steps"].items():
        lines.append(f"| {step} | {c['samples']} | {c['errors']} | {_fmt(c['p95_s'])} | {_fmt(c['max_s'])} |")
    lines += ["", "## 環境資料缺漏", ""]
    lines += [f"- {m}" for m in report["missing_environment"]] or ["- （無）"]
    lines += ["", "## 問題紀錄", ""]
    lines += [f"- {json.dumps(i, ensure_ascii=False)}" for i in report["issues"]] or ["- （無）"]
    return "\n".join(lines) + "\n"


def write_report(report: dict, samples: list[Sample], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"t18-load-{stamp}.json"
    md_path = out_dir / f"t18-load-{stamp}.md"
    json_path.write_text(json.dumps({"report": report, "samples": [asdict(s) for s in samples]},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
