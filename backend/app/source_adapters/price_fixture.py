"""Run a single deterministic T02 collection from a local retail HTML fixture."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .price_models import IngredientQuery, QuoteStatus
from .pxmart import build_price_quote_from_candidates, candidate_from_raw, parse_products
from .uni_prosperity import RETAILER, SEARCH_PAGE_MARKER, STORE_CONDITION, UniProsperityAdapter, parse_tiles

MAX_FIXTURE_BYTES = 2 * 1024 * 1024


def collect_fixture(provider, query, html, *, observed_at):
    if observed_at.utcoffset() is None:
        raise ValueError("Fixture observation time must include a timezone")
    if len(html) > MAX_FIXTURE_BYTES:
        raise ValueError("HTML fixture exceeds the size limit")
    text = html.decode("utf-8")
    if provider == "pxmart":
        products = parse_products(text)
    elif provider == "uniprosperity":
        products = parse_tiles(text)
    else:
        raise ValueError("Unknown fixture provider")
    make_candidate = candidate_from_raw if provider == "pxmart" else UniProsperityAdapter._candidate
    quote = build_price_quote_from_candidates(
        query, [make_candidate(query, product) for product in products],
        source_url=f"https://fixture.invalid/{provider}", queried_at=observed_at,
    )
    changes = {"source": "Local HTML fixture"}
    if provider == "uniprosperity":
        changes.update(retailer=RETAILER, store_condition=STORE_CONDITION)
    if not products and (provider != "uniprosperity" or SEARCH_PAGE_MARKER not in text):
        changes.update(status=QuoteStatus.SCHEMA_CHANGED, missing_reason="fixture has no recognized product markup")
    return {
        "execution_mode": "fixtures_only",
        "provider": provider,
        "fixture_sha256": hashlib.sha256(html).hexdigest(),
        "observation": quote.model_copy(update=changes).model_dump(mode="json", warnings="error"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["pxmart", "uniprosperity"])
    parser.add_argument("html", type=Path)
    parser.add_argument("--food-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--observed-at", required=True, type=datetime.fromisoformat)
    args = parser.parse_args()
    query = IngredientQuery(canonical_name=args.name, food_ids=[args.food_id])
    with args.html.open("rb") as handle:
        html = handle.read(MAX_FIXTURE_BYTES + 1)
    print(json.dumps(collect_fixture(args.provider, query, html, observed_at=args.observed_at), ensure_ascii=False))


if __name__ == "__main__":
    main()
