"""Collect PX Mart price candidates for the workbook's canonical ingredients."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote as url_quote

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.source_adapters.ingredient_workbook import load_ingredient_queries
from app.source_adapters.price_models import IngredientQuery, PriceQuote, QuoteStatus
from app.source_adapters.pxmart import PxMartAdapter, SEARCH_URL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="input .xlsx path")
    parser.add_argument("--output", type=Path, required=True, help="output .json/.csv file or directory")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--limit", type=int, help="only process the first N deduplicated ingredient work items")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, choices=range(0, 4), default=1)
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between ingredients")
    parser.add_argument("--dry-run", action="store_true", help="validate the batch plan without network requests")
    parser.add_argument("--batch-size", type=int, help="process at most N unfinished ingredients")
    parser.add_argument("--checkpoint", type=Path, help="JSON checkpoint used to resume an interrupted batch")
    return parser.parse_args()


def _resolve_output(path: Path, output_format: str) -> Path:
    if path.exists() and path.is_dir() or not path.suffix:
        path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        return path / f"pxmart_prices_{stamp}.{output_format}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _scope_counts(ingredients: list[IngredientQuery]) -> dict[str, int]:
    return {
        "food_id_total": sum(len(item.food_ids) for item in ingredients),
        "mapped_food_id_total": sum(
            len(item.food_ids)
            for item in ingredients
            if not item.mapping_missing_reason
        ),
        "canonical_ingredient_total": sum(
            not item.mapping_missing_reason for item in ingredients
        ),
        "unmapped_food_id_total": sum(
            len(item.food_ids)
            for item in ingredients
            if item.mapping_missing_reason
        ),
    }


def _mapping_error_quote(ingredient: IngredientQuery) -> PriceQuote:
    return PriceQuote(
        canonical_name=ingredient.canonical_name,
        food_ids=ingredient.food_ids,
        source="recipe workbook Food_ID mapping",
        source_url="data/recipe_dataset_1027.xlsx#recipe_ingredients",
        queried_at=datetime.now(timezone.utc),
        status=QuoteStatus.NOT_FOUND,
        missing_reason=ingredient.mapping_missing_reason,
        selection_method="workbook_mapping",
    )


def _write_json(
    path: Path,
    quotes: list[PriceQuote],
    scope: dict[str, int],
    selected: list[IngredientQuery],
) -> None:
    counts = Counter(quote.status.value for quote in quotes)
    tiers = Counter(quote.selection_tier.value for quote in quotes)
    processed = len(quotes)
    payload = {
        "source": "PX Mart Hour Arrive public search",
        **scope,
        "selected_ingredient_total": len(selected),
        "selected_search_total": sum(
            not item.mapping_missing_reason for item in selected
        ),
        "counts": dict(sorted(counts.items())),
        "coverage": {
            "processed": processed,
            "auto_adopted": tiers["auto_adopted"],
            "estimated_alternatives": tiers["estimate"],
            "missing": tiers["missing"],
            "mapping_failure_rate": tiers["missing"] / processed if processed else 0,
            "price_update_success_rate": tiers["auto_adopted"] / processed if processed else 0,
        },
        "quotes": [quote.model_dump(mode="json") for quote in quotes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, quotes: list[PriceQuote]) -> None:
    fields = [
        "canonical_name", "food_ids", "status", "missing_reason", "product_id",
        "product_name", "price", "currency", "package_quantity", "package_unit",
        "package_description", "product_url", "availability", "price_kind",
        "promotion_description", "selection_tier", "confidence",
        "selection_policy_version", "selection_method", "selection_reasons",
        "estimated_product_id", "estimated_product_name", "estimated_price",
        "estimated_currency", "package_total_quantity", "package_item_count",
        "package_per_item_quantity", "package_tolerance", "edible_quantity",
        "weight_basis", "weight_reliability", "retailer", "source", "source_url", "queried_at",
        "valid_from", "valid_until", "region_condition", "store_condition", "candidates_json",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for quote in quotes:
            row = quote.model_dump(mode="json", exclude={"candidates"})
            row["food_ids"] = "|".join(quote.food_ids)
            row["selection_reasons"] = "|".join(quote.selection_reasons)
            row["candidates_json"] = json.dumps(
                [candidate.model_dump(mode="json") for candidate in quote.candidates], ensure_ascii=False
            )
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.delay < 0:
        raise SystemExit("--delay cannot be negative")
    if args.batch_size is not None and args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    ingredients = load_ingredient_queries(args.input)
    scope = _scope_counts(ingredients)
    selected = ingredients[: args.limit] if args.limit else ingredients
    print(f"recipe_ingredients 唯一 Food_ID: {scope['food_id_total']}", flush=True)
    print(f"可對應名稱的 Food_ID: {scope['mapped_food_id_total']}", flush=True)
    print(f"需搜尋的不同標準食材: {scope['canonical_ingredient_total']}", flush=True)
    print(f"無法對應名稱的 Food_ID: {scope['unmapped_food_id_total']}", flush=True)
    print(
        "本次查詢食材數: "
        f"{sum(not item.mapping_missing_reason for item in selected)}",
        flush=True,
    )

    output = _resolve_output(args.output, args.format)
    checkpoint = args.checkpoint or output.with_suffix(output.suffix + ".checkpoint.json")
    if args.dry_run:
        payload = {
            "dry_run": True,
            "network_requests_made": 0,
            **scope,
            "selected_ingredient_total": len(selected),
            "canonical_names": [
                item.canonical_name
                for item in selected
                if not item.mapping_missing_reason
            ],
            "unmapped_food_ids": [
                {
                    "food_ids": item.food_ids,
                    "missing_reason": item.mapping_missing_reason,
                }
                for item in selected
                if item.mapping_missing_reason
            ],
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"dry-run 完成，未發出網路請求；輸出: {output}", flush=True)
        return 0

    quotes: list[PriceQuote] = []
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        quotes = [PriceQuote.model_validate(item) for item in saved.get("quotes", [])]
    completed = {quote.canonical_name for quote in quotes}
    remaining = [item for item in selected if item.canonical_name not in completed]
    if args.batch_size:
        remaining = remaining[: args.batch_size]
    counts: Counter[str] = Counter()
    counts.update(quote.status.value for quote in quotes)
    with PxMartAdapter(timeout=args.timeout, retries=args.retries) as adapter:
        for index, ingredient in enumerate(remaining, start=len(quotes) + 1):
            if ingredient.mapping_missing_reason:
                quote = _mapping_error_quote(ingredient)
            else:
                try:
                    quote = adapter.quote(ingredient)
                except Exception as exc:
                    quote = PriceQuote(
                        canonical_name=ingredient.canonical_name,
                        food_ids=ingredient.food_ids,
                        source_url=SEARCH_URL.format(
                            query=url_quote(ingredient.canonical_name, safe="")
                        ),
                        queried_at=datetime.now(timezone.utc),
                        region_condition="Taiwan; no precise delivery region selected",
                        store_condition="unexpected adapter failure; collection continued",
                        status=QuoteStatus.SOURCE_ERROR,
                        missing_reason=f"unexpected adapter error: {type(exc).__name__}",
                    )
            quotes.append(quote)
            counts[quote.status.value] += 1
            auto = sum(item.selection_tier.value == "auto_adopted" for item in quotes)
            estimate = sum(item.selection_tier.value == "estimate" for item in quotes)
            missing = len(quotes) - auto - estimate
            print(
                f"[{index}/{len(selected)}] {ingredient.canonical_name}: {quote.status.value} "
                f"(自動採用={auto}, 估價備選={estimate}, 缺值={missing})",
                flush=True,
            )
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(
                json.dumps(
                    {"quotes": [item.model_dump(mode="json") for item in quotes]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if index < len(selected) and args.delay:
                time.sleep(args.delay)

    output_format = output.suffix.lower().lstrip(".") if output.suffix.lower() in {".csv", ".json"} else args.format
    if output_format == "csv":
        _write_csv(output, quotes)
    else:
        _write_json(output, quotes, scope, selected)
    tiers = Counter(quote.selection_tier.value for quote in quotes)
    processed = len(quotes)
    print(
        "覆蓋率: "
        f"總數={len(ingredients)}, 已處理={processed}, 自動採用={tiers['auto_adopted']}, "
        f"估價備選={tiers['estimate']}, 缺值={tiers['missing']}, "
        f"對映失效率={(tiers['missing'] / processed if processed else 0):.2%}, "
        f"價格更新成功率={(tiers['auto_adopted'] / processed if processed else 0):.2%}",
        flush=True,
    )
    print(f"輸出: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
