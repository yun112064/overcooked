"""Offline-reassess a PX Mart run, then selectively refresh insufficient quotes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.source_adapters.ingredient_workbook import load_ingredient_queries
from app.source_adapters.price_models import IngredientQuery, PriceQuote, SelectionTier
from app.source_adapters.product_matching import NON_FOOD_MARKERS, normalize
from app.source_adapters.pxmart import (
    PxMartAdapter,
    build_price_quote_from_candidates,
    candidate_from_raw,
)


KNOWN_MISMATCHES = {
    "水": "純水冰角", "火腿": "火腿鳳梨蛋炒飯", "蜂蜜": "蜂蜜優格",
    "胡椒": "胡椒豬肚雞煲", "番茄": "番茄火鍋底料", "起司": "起司饅頭",
    "奶油": "奶油餐包", "大蒜": "大蒜花生米", "麻油": "麻油雞",
    "啤酒": "啤酒酵母洗髮精", "蛤蠣": "蛤蠣雞鍋", "花枝": "花枝卷",
    "蝦仁": "蝦仁炒飯", "蘑菇": "蘑菇球爆米花", "紅椒": "甜紅椒粉",
    "迷迭香": "迷迭香薯條", "柴魚": "貓用柴魚罐頭", "雞腿": "雞腿便當",
    "蒜頭": "蒜頭雞", "昆布": "昆布味噌", "巧克力": "巧克力吐司",
    "牛肉": "寶路寵物食品", "沙茶": "沙茶火鍋", "蝦米": "蝦米朝天辣椒醬",
    "芋頭": "芋頭饅頭", "皮蛋": "皮蛋麻婆豆腐", "蛋白": "蛋白牛奶花生",
    "小卷": "海苔小卷", "大頭菜": "大頭菜泡菜", "松阪豬": "涼拌松阪豬",
    "花椒": "花椒雞翅腿", "高粱酒": "高粱酒豆腐乳", "可樂": "可樂果",
    "牛腩": "黑胡椒牛腩料理包", "紅酒": "紅酒燉牛肉", "雞湯": "已調味燉雞湯",
    "韭菜": "韭菜水餃", "鱈魚": "貓飼料", "蛋黃": "蛋黃素洗髮乳",
    "檸檬": "檸檬愛玉", "鹹蛋黃": "鹹蛋黃方塊酥", "蔥花": "蔥花捲",
    "草莓": "草莓優酪", "鹹蛋": "鹹蛋黃方塊酥", "竹筍": "竹筍粥",
    "黑醋": "黑醋栗脆穀", "玉米": "玉米筍", "地瓜": "地瓜葉",
    "甘草": "甘草瓜子", "豆干": "客家小炒豆干",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reuse-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("outputs/pxmart-foodid-v3-summary.txt"),
    )
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, choices=range(0, 4), default=1)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--limit-refetch", type=int)
    return parser.parse_args()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _raw_candidate(candidate: dict[str, object]) -> dict[str, object]:
    raw = candidate.get("raw")
    if isinstance(raw, dict) and raw:
        return raw
    return {
        "@type": "Product",
        "name": candidate.get("product_name"),
        "url": candidate.get("product_url"),
        "brand": candidate.get("brand"),
        "offers": {
            "price": candidate.get("price"),
            "priceCurrency": candidate.get("currency"),
            "availability": "https://schema.org/InStock"
            if candidate.get("availability") == "in_stock" else None,
            "validFrom": candidate.get("valid_from"),
            "priceValidUntil": candidate.get("valid_until"),
        },
    }


def _reevaluate(
    query: IngredientQuery, original: dict[str, object]
) -> tuple[PriceQuote, dict[str, object]]:
    raw_candidates = [
        _raw_candidate(item)
        for item in original.get("candidates", [])
        if isinstance(item, dict)
    ]
    candidates = [candidate_from_raw(query, raw) for raw in raw_candidates]
    original_queried_at = datetime.fromisoformat(
        str(original["queried_at"]).replace("Z", "+00:00")
    )
    quote = build_price_quote_from_candidates(
        query,
        candidates,
        source_url=str(original.get("source_url") or ""),
        queried_at=original_queried_at,
        query_terms=[query.canonical_name],
        source_request_count=0,
        force_reassessment=True,
    ).model_copy(
        update={
            "selection_method": "offline_reevaluation",
            "selection_reasons": [
                "re-evaluated from preserved source candidates; no new request",
                *quote_reason_placeholder(),
            ],
        }
    )
    old_product_id = original.get("product_id")
    old_candidate = next(
        (item for item in candidates if item.product_id == old_product_id), None
    )
    old_eligible = bool(
        old_candidate
        and old_candidate.match_decision.value == "match"
        and old_candidate.confidence.value == "high"
        and old_candidate.price is not None
        and old_candidate.price > 0
        and old_candidate.package_total_quantity is not None
        and old_candidate.package_unit
    )
    detail = {
        "old_selected_still_eligible": old_eligible,
        "old_selected_rejection_reasons": (
            old_candidate.rejection_reasons if old_candidate else ["old selected product absent from candidates"]
        ),
    }
    return quote, detail


def quote_reason_placeholder() -> list[str]:
    # Kept as a helper so offline provenance is explicit without implying a live refresh.
    return ["policy pxmart-selection-v3"]


def _counts(quotes: list[PriceQuote]) -> dict[str, int]:
    tiers = Counter(item.selection_tier.value for item in quotes)
    return {
        "processed": len(quotes),
        "auto_adopted": tiers["auto_adopted"],
        "estimate": tiers["estimate"],
        "missing": tiers["missing"],
    }


def _full_payload(quotes: list[PriceQuote], request_count: int) -> dict[str, object]:
    counts = _counts(quotes)
    total = len(quotes)
    return {
        "source": "PX Mart Hour Arrive public search",
        "selection_policy_version": "pxmart-selection-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "food_id_total": sum(len(item.food_ids) for item in quotes),
        "canonical_ingredient_total": total,
        "source_request_count": request_count,
        "counts": counts,
        "coverage": {
            "search_completion_rate": total / 434 if total else 0,
            "formal_price_coverage_rate": counts["auto_adopted"] / total if total else 0,
            "estimate_coverage_rate": counts["estimate"] / total if total else 0,
            "missing_count": counts["missing"],
            "manually_verified_price_count": 0,
            "manually_verified_accuracy": None,
        },
        "quotes": [item.model_dump(mode="json") for item in quotes],
    }


def _audit_payload(
    original_quotes: list[dict[str, object]],
    offline_quotes: list[PriceQuote],
    final_quotes: list[PriceQuote],
    offline_details: dict[str, dict[str, object]],
    live_attempts: dict[str, dict[str, object]],
    request_count: int,
) -> dict[str, object]:
    originals = {str(item["canonical_name"]): item for item in original_quotes}
    offline = {item.canonical_name: item for item in offline_quotes}
    final = {item.canonical_name: item for item in final_quotes}
    changes: list[dict[str, object]] = []
    for name, old in originals.items():
        off = offline[name]
        new = final[name]
        changes.append({
            "canonical_name": name,
            "food_ids": new.food_ids,
            "original_tier": old.get("selection_tier"),
            "original_product_id": old.get("product_id"),
            "original_product_name": old.get("product_name"),
            "offline_tier": off.selection_tier.value,
            "offline_product_id": off.product_id,
            "final_tier": new.selection_tier.value,
            "final_status": new.status.value,
            "final_product_id": new.product_id,
            "final_product_name": new.product_name,
            "final_missing_reason": new.missing_reason,
            "old_selected_still_eligible": offline_details[name]["old_selected_still_eligible"],
            "old_selected_rejection_reasons": offline_details[name]["old_selected_rejection_reasons"],
            "refetch": live_attempts.get(name),
        })

    known_checks = []
    for name, wrong_name in KNOWN_MISMATCHES.items():
        if name not in final:
            known_checks.append({"canonical_name": name, "status": "missing_food_id_result"})
            continue
        selected_name = final[name].product_name or final[name].estimated_product_name or ""
        passed = wrong_name not in selected_name
        known_checks.append({
            "canonical_name": name,
            "known_wrong_product": wrong_name,
            "final_tier": final[name].selection_tier.value,
            "final_product_name": selected_name or None,
            "passed": passed,
        })

    original_auto_unsuitable = sum(
        old.get("selection_tier") == "auto_adopted"
        and not offline_details[str(old["canonical_name"])]["old_selected_still_eligible"]
        for old in original_quotes
    )
    original_missing_rescued = sum(
        old.get("selection_tier") == "missing"
        and final[str(old["canonical_name"])].selection_tier is SelectionTier.AUTO_ADOPTED
        for old in original_quotes
    )
    original_estimate_upgraded = sum(
        old.get("selection_tier") == "estimate"
        and final[str(old["canonical_name"])].selection_tier is SelectionTier.AUTO_ADOPTED
        for old in original_quotes
    )
    auto_quotes = [item for item in final_quotes if item.selection_tier is SelectionTier.AUTO_ADOPTED]
    identity_mismatches = sum(
        not item.candidates
        or item.candidates[0].product_id != item.product_id
        or item.candidates[0].match_decision.value != "match"
        or item.candidates[0].confidence.value != "high"
        for item in auto_quotes
    )
    non_food_auto = sum(
        any(normalize(marker) in normalize(item.product_name or "") for marker in NON_FOOD_MARKERS)
        for item in auto_quotes
    )
    incomplete_auto = sum(
        not all((item.product_name, item.product_id, item.price, item.package_unit, item.source_url, item.queried_at))
        for item in auto_quotes
    )
    final_counts = _counts(final_quotes)
    retained_candidates = [
        candidate for quote in final_quotes for candidate in quote.candidates
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original_counts": _counts([PriceQuote.model_validate(item) for item in original_quotes]),
        "offline_counts": _counts(offline_quotes),
        "final_counts": final_counts,
        "original_auto_unsuitable_count": original_auto_unsuitable,
        "original_missing_rescued_count": original_missing_rescued,
        "original_estimate_upgraded_count": original_estimate_upgraded,
        "source_request_count": request_count,
        "known_mismatch_checks": known_checks,
        "known_mismatch_failure_count": sum(item.get("passed") is False for item in known_checks),
        "non_food_auto_adopted_count": non_food_auto,
        "auto_identity_rule_failure_count": identity_mismatches,
        "auto_required_field_failure_count": incomplete_auto,
        "candidate_limit_failure_count": sum(len(item.candidates) > 5 for item in final_quotes),
        "candidate_missing_brand_count": sum(
            not candidate.brand for candidate in retained_candidates
        ),
        "max_query_terms_used": max(
            (len(item.query_terms) for item in final_quotes), default=0
        ),
        "zero_price_count": sum(item.price == 0 or item.estimated_price == 0 for item in final_quotes),
        "specification_or_weight_risk_count": sum(
            item.selection_tier is SelectionTier.ESTIMATE for item in final_quotes
        ),
        "manual_review_count": final_counts["estimate"],
        "no_price_ingredient_count": final_counts["missing"],
        "obvious_mismatch_count": sum(item.get("passed") is False for item in known_checks),
        "product_identity_mismatch_count": identity_mismatches,
        "manually_verified_price_count": 0,
        "manually_verified_accuracy": None,
        "formal_price_coverage_rate": final_counts["auto_adopted"] / len(final_quotes),
        "estimate_coverage_rate": final_counts["estimate"] / len(final_quotes),
        "missing_ingredients": [
            {"canonical_name": item.canonical_name, "food_ids": item.food_ids, "reason": item.missing_reason}
            for item in final_quotes if item.selection_tier is SelectionTier.MISSING
        ],
        "large_acceptance_passed": all((
            len(final_quotes) == 434,
            sum(len(item.food_ids) for item in final_quotes) == 434,
            not any(item.get("passed") is False for item in known_checks),
            non_food_auto == 0,
            identity_mismatches == 0,
            incomplete_auto == 0,
            not any(len(item.candidates) > 5 for item in final_quotes),
            not any(len(item.query_terms) > 3 for item in final_quotes),
            not any(item.price == 0 or item.estimated_price == 0 for item in final_quotes),
        )),
        "changes": changes,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    source = json.loads(args.reuse_input.read_text(encoding="utf-8"))
    original_quotes = source.get("quotes", [])
    queries = load_ingredient_queries(args.input)
    query_by_name = {item.canonical_name: item for item in queries}
    if len(original_quotes) != 434 or len(query_by_name) != 434:
        raise SystemExit("expected exactly 434 original quotes and 434 canonical queries")

    offline_quotes: list[PriceQuote] = []
    offline_details: dict[str, dict[str, object]] = {}
    for original in original_quotes:
        name = str(original["canonical_name"])
        quote, detail = _reevaluate(query_by_name[name], original)
        offline_quotes.append(quote)
        offline_details[name] = detail

    checkpoint_data: dict[str, object] = {}
    if args.checkpoint.exists():
        checkpoint_data = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    final_by_name = {item.canonical_name: item for item in offline_quotes}
    completed_live = set(checkpoint_data.get("completed_live", []))
    live_attempts = dict(checkpoint_data.get("live_attempts", {}))
    for item in checkpoint_data.get("quotes", []):
        name = str(item.get("canonical_name", ""))
        if name not in completed_live or name not in query_by_name:
            continue
        quote, _ = _reevaluate(query_by_name[name], item)
        attempt = live_attempts.get(name, {})
        final_by_name[name] = quote.model_copy(
            update={
                "selection_method": "checkpoint_policy_reevaluation",
                "query_terms": list(attempt.get("query_terms", item.get("query_terms", []))),
                "source_request_count": int(
                    attempt.get("request_count", item.get("source_request_count", 0))
                ),
            }
        )
    request_count = int(checkpoint_data.get("source_request_count", 0))

    targets = [
        item for item in offline_quotes
        if item.selection_tier is not SelectionTier.AUTO_ADOPTED
        and item.canonical_name not in completed_live
    ]
    if args.limit_refetch is not None:
        targets = targets[:args.limit_refetch]

    if not args.offline_only and targets:
        with PxMartAdapter(timeout=args.timeout, retries=args.retries) as adapter:
            for index, offline_quote in enumerate(targets, 1):
                query = query_by_name[offline_quote.canonical_name]
                live = adapter.quote(query, force_reassessment=True)
                request_count += live.source_request_count
                use_live = (
                    live.selection_tier is SelectionTier.AUTO_ADOPTED
                    or live.selection_tier is SelectionTier.ESTIMATE
                    or live.status.value not in {"source_error", "timeout", "schema_changed"}
                )
                if use_live:
                    final_by_name[query.canonical_name] = live
                completed_live.add(query.canonical_name)
                live_attempts[query.canonical_name] = {
                    "status": live.status.value,
                    "selection_tier": live.selection_tier.value,
                    "request_count": live.source_request_count,
                    "query_terms": live.query_terms,
                    "used_in_final": use_live,
                }
                checkpoint = {
                    "policy_version": "pxmart-selection-v3",
                    "completed_live": sorted(completed_live),
                    "source_request_count": request_count,
                    "live_attempts": live_attempts,
                    "quotes": [
                        final_by_name[item.canonical_name].model_dump(mode="json")
                        for item in queries
                    ],
                }
                _atomic_json(args.checkpoint, checkpoint)
                print(
                    f"[{index}/{len(targets)}] {query.canonical_name}: "
                    f"{live.status.value} requests={live.source_request_count}",
                    flush=True,
                )
                if index < len(targets) and args.delay:
                    time.sleep(args.delay)

    final_quotes = [final_by_name[item.canonical_name] for item in queries]
    checkpoint = {
        "policy_version": "pxmart-selection-v3",
        "completed_live": sorted(completed_live),
        "source_request_count": request_count,
        "live_attempts": live_attempts,
        "quotes": [item.model_dump(mode="json") for item in final_quotes],
    }
    _atomic_json(args.checkpoint, checkpoint)
    _atomic_json(args.output, _full_payload(final_quotes, request_count))
    audit = _audit_payload(
        original_quotes, offline_quotes, final_quotes, offline_details,
        live_attempts, request_count,
    )
    _atomic_json(args.audit_output, audit)
    final_counts = _counts(final_quotes)
    missing_names = [
        item.canonical_name for item in final_quotes
        if item.selection_tier is SelectionTier.MISSING
    ]
    _atomic_text(
        args.summary_output,
        "\n".join((
            "T02 PX Mart v3 大型驗收摘要",
            f"產生時間（UTC）：{audit['generated_at']}",
            f"Food_ID／食材總數：{len(final_quotes)}",
            f"auto_adopted：{final_counts['auto_adopted']}",
            f"estimate／manual_review：{final_counts['estimate']}",
            f"missing：{final_counts['missing']}",
            f"正式價格覆蓋率：{audit['formal_price_coverage_rate']:.2%}",
            f"原 254 筆中不再適合正式採用：{audit['original_auto_unsuitable_count']}",
            f"原 missing 補救：{audit['original_missing_rescued_count']}",
            f"原 estimate 升級：{audit['original_estimate_upgraded_count']}",
            f"已知錯配回歸失敗：{audit['known_mismatch_failure_count']}",
            f"非食品自動採用：{audit['non_food_auto_adopted_count']}",
            f"補抓 HTTP 請求：{request_count}",
            f"人工逐筆驗證：0；正確率：未知（不可宣稱 100%）",
            f"大型規則驗收：{'通過' if audit['large_acceptance_passed'] else '未通過'}",
            "missing 食材：" + ("、".join(missing_names) if missing_names else "無"),
        )) + "\n",
    )
    print(json.dumps(_counts(final_quotes), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
