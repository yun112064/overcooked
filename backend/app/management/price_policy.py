"""Management-only T02 rules and preview. Raw observations remain immutable."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import NamedTuple

import sqlalchemy as sa
from pydantic import ValidationError

from app.source_adapters.price_models import PriceQuote, QuoteStatus, WeightReliability
from app.source_adapters.price_scope import PACKAGE_MASS_JOIN, materialize_quote, quote_unusable_reason
from app.source_adapters.pxmart import STORE_CONDITION as PX_DEFAULT_STORE
from app.source_adapters.uni_prosperity import STORE_CONDITION as UNI_DEFAULT_STORE
from .models import tables as T

MAX_AGE = timedelta(hours=48)
CONDITIONS = ("region_condition", "store_condition", "promotion_condition")
SCOPE = ("food_id", "ingredient_version_id", "ingredient_publication_id",
         "legacy_recipe_release_id", "provider", *CONDITIONS)
SNAPSHOT_FROM = f"""
    FROM price_quote_snapshots s
    JOIN price_collection_runs r ON r.id = s.run_id
    LEFT JOIN price_data_releases p ON p.release_id = r.price_data_release_id
    {PACKAGE_MASS_JOIN}
"""
FOOD_BINDING = """
    EXISTS (SELECT 1 FROM price_snapshot_foods f
            WHERE f.snapshot_id = s.id AND f.recipe_release_id = r.recipe_release_id
              AND f.food_id = :food_id)
"""
SNAPSHOT_COLUMNS = """
    s.*, r.recipe_release_id, r.price_data_release_id, p.usage_status,
    m.net_mass_g, m.source_url AS mass_source_url
"""
SNAPSHOT = sa.text(f"""
    SELECT {SNAPSHOT_COLUMNS} {SNAPSHOT_FROM}
    WHERE s.id = :snapshot_id AND r.recipe_release_id = :release_id AND {FOOD_BINDING}
""")


def utc_stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instant(value):
    return datetime.fromisoformat(value) if value else None


def cutoff(observed_at, *expiry, freshness=MAX_AGE):
    # Subtraction avoids overflowing datetime.max for valid ISO dates near year 9999.
    limits = [end for end in expiry if end is not None and end - observed_at <= freshness]
    return min(limits) if limits else observed_at + min(freshness, datetime.max.replace(tzinfo=timezone.utc) - observed_at)


def source_freshness(db, content, pins):
    provider = content["provider"] if content["provider"] != "manual_evidence" else "supplied_document"
    if pins["source_policy_publication_id"] is None:
        return MAX_AGE
    member, entity = T["publication_members"], T["entity_versions"]
    policies = db.scalars(sa.select(entity.c.content).join(member, member.c.version_id == entity.c.id).where(
        member.c.publication_id == pins["source_policy_publication_id"], entity.c.domain == "source_policy",
    ))
    return min([MAX_AGE, *(timedelta(seconds=p["freshness_seconds"]) for p in policies if p["provider"] == provider)])


def window(content, freshness=MAX_AGE):
    start = instant(content["valid_from"])
    end = instant(content["valid_until"])
    if content["correction_kind"] == "quote_correction":
        end = cutoff(instant(content["observed_at"]), end, freshness=freshness)
    return start, end


def price_issues(db, content, pins, now):
    issues = []
    if any(content[key] != pins[key] for key in ("ingredient_publication_id", "legacy_recipe_release_id")):
        issues.append("PRICE_DICTIONARY_PIN_MISMATCH")
    member, entity, publication = T["publication_members"], T["entity_versions"], T["publications"]
    dictionary = db.execute(sa.select(entity.c.content, publication.c.manifest).select_from(
        member.join(entity, entity.c.id == member.c.version_id).join(
            publication, publication.c.id == member.c.publication_id)
    ).where(
        publication.c.domain == "ingredient", member.c.publication_id == content["ingredient_publication_id"],
        member.c.logical_id == content["food_id"], entity.c.domain == "ingredient",
        entity.c.id == content["ingredient_version_id"], entity.c.logical_id == content["food_id"],
    )).first()
    if (dictionary is None or dictionary.content["food_id"] != content["food_id"]
            or dictionary.manifest["dependency_pins"]["legacy_recipe_release_id"] != content["legacy_recipe_release_id"]):
        issues.append("PRICE_FOOD_IDENTITY_UNVERIFIED")

    if not content["product_name"].strip() or any(
        content[key] is not None and (not content[key].strip() or content[key] != content[key].strip())
        for key in CONDITIONS
    ):
        issues.append("PRICE_SCOPE_INVALID")

    binding = content["correction_kind"] == "product_binding"
    observed_at = instant(content["observed_at"])
    if binding:
        if any(content[key] is not None for key in ("price_twd", "package_mass_g", "observed_at")) or content["availability"] != "unknown":
            issues.append("PRICE_BINDING_HAS_QUOTE")
    elif not any(source["origin_kind"] in {"supplied_document", "workbook", "manual_entry"}
                 for source in content["sources"]):
        issues.append("PRICE_MANUAL_EVIDENCE_REQUIRED")

    start, end = window(content, source_freshness(db, content, pins))
    if (start is not None and end is not None and start >= end) or (observed_at and start < observed_at):
        issues.append("PRICE_WINDOW_INVALID")
    if observed_at and observed_at > now:
        issues.append("PRICE_OBSERVATION_IN_FUTURE")
    if not binding and end <= now:
        issues.append("PRICE_EXPIRED")

    if content["snapshot_id"] is not None:
        snapshot = db.execute(SNAPSHOT, {
            "snapshot_id": content["snapshot_id"], "release_id": content["legacy_recipe_release_id"],
            "food_id": content["food_id"],
        }).mappings().first()
        if (snapshot is None or snapshot["provider"] != content["provider"]
                or snapshot["product_id"] != content["product_id"]
                or snapshot["product_name"] != content["product_name"]):
            issues.append("PRICE_SNAPSHOT_SCOPE_MISMATCH")
        elif observed_at and observed_at <= snapshot["queried_at"]:
            issues.append("PRICE_NEW_OBSERVATION_REQUIRED")
    return issues


def replacement_logical_id(db, content, pins):
    """Resolve the exact version in the pinned base, within its existing price scope."""
    member, entity, publication = T["publication_members"], T["entity_versions"], T["publications"]
    target = db.execute(sa.select(member.c.logical_id, entity.c.content)
        .join(entity, member.c.version_id == entity.c.id)
        .join(publication, member.c.publication_id == publication.c.id)
        .where(
            publication.c.id == pins["base_publication_id"],
            publication.c.manifest_hash == pins["base_publication_manifest_sha256"],
            publication.c.domain == "price", entity.c.domain == "price",
            member.c.version_id == content["replaces_version_id"],
        )).one_or_none()
    if target is not None and all(
        target.content[key] == content[key] for key in (*SCOPE, "correction_kind")
    ):
        return target.logical_id
    return None


def publication_price_issue(db, members, pins):
    """Check the complete policy, including unchanged base members, under the publish lock."""
    ids = [member["ref"]["version_id"] for member in members]
    entity, revision = T["entity_versions"], T["proposal_revisions"]
    records = db.execute(sa.select(entity.c.content, revision.c.dependency_pins).join(
        revision, sa.and_(revision.c.proposal_id == entity.c.proposal_id, revision.c.revision == entity.c.revision)
    ).where(entity.c.domain == "price", entity.c.id.in_(ids))).all()
    if len(records) != len(set(ids)):
        return "DEPENDENCY_CHANGED"
    scoped = defaultdict(list)
    for content, member_pins in records:
        if any(member_pins[key] != pins[key] for key in (
            "ingredient_publication_id", "ingredient_manifest_sha256", "legacy_recipe_release_id",
            "legacy_recipe_source_sha256", "legacy_db_capture_sha256", "source_policy_publication_id",
            "source_policy_manifest_sha256",
        )):
            return "DEPENDENCY_CHANGED"
        start, end = window(content, source_freshness(db, content, member_pins))
        scoped[tuple(content[key] for key in SCOPE)].append((start, end))

    floor = datetime.min.replace(tzinfo=timezone.utc)
    for intervals in scoped.values():
        ordered = sorted(intervals, key=lambda interval: interval[0] or floor)
        for (_, previous_end), (next_start, _) in zip(ordered, ordered[1:]):
            if previous_end is None or next_start is None or next_start < previous_end:
                return "PRICE_WINDOW_OVERLAP"
    return None


def observation_reason(quote, as_of, freshness=MAX_AGE):
    if quote.usage_status == "historical_requires_refresh":
        return "PRICE_REVALIDATION_REQUIRED"
    if (quote.region_condition not in (None, "Taiwan; no precise delivery region selected")
            or quote.store_condition not in (None, PX_DEFAULT_STORE, UNI_DEFAULT_STORE)
            or quote.promotion_description):
        return "PRICE_CONTEXT_REQUIRED"
    if quote.queried_at > as_of or quote.valid_from and quote.valid_from > as_of:
        return "PRICE_NOT_YET_VALID"
    if quote.status is QuoteStatus.SUCCESS and as_of >= cutoff(quote.queried_at, quote.valid_until, freshness=freshness):
        return "PRICE_EXPIRED"
    return quote_unusable_reason(quote, as_of)


class ManagedPrice(NamedTuple):
    version_id: str
    review_decision_id: str
    content: dict
    freshness: timedelta = MAX_AGE


class PricePreview(NamedTuple):
    quote: PriceQuote | None
    reason: str | None


def preview_price(entries, observations, fallback, *, food_id, release_id, dictionary_id, ingredient_version_id, as_of):
    """Resolve one explicitly scoped price policy at the requested time."""
    active = []
    expired = []
    for entry in entries:
        content = entry.content
        if (content["food_id"], content["legacy_recipe_release_id"], content["ingredient_publication_id"],
                content["ingredient_version_id"]) != (food_id, release_id, dictionary_id, ingredient_version_id):
            continue
        start, end = window(content, entry.freshness)
        if (start is None or start <= as_of) and (end is None or as_of < end):
            active.append((entry, end))
        elif end is not None and as_of >= end:
            expired.append(entry)
    if any(any(entry.content[key] is not None for key in CONDITIONS) for entry, _ in active):
        return PricePreview(None, "PRICE_CONTEXT_REQUIRED")

    # An implicit provider choice could defeat one of the active manual locks.
    if len({entry.content["provider"] for entry, _ in active}) > 1:
        return PricePreview(None, "PRICE_LOCKED_PROVIDER_REQUIRED")
    corrections = [(entry, end) for entry, end in active if entry.content["correction_kind"] == "quote_correction"]
    if corrections:
        entry, end = corrections[0]
        content = entry.content
        return PricePreview(PriceQuote(
            canonical_name=food_id, food_ids=[food_id], retailer=content["provider"], source="Reviewed manual evidence",
            source_url="", queried_at=instant(content["observed_at"]), valid_from=instant(content["valid_from"]),
            valid_until=end, status="success", selection_tier="manual_approved",
            product_id=content["product_id"], product_name=content["product_name"], price=Decimal(content["price_twd"]),
            currency="TWD", package_quantity=Decimal(content["package_mass_g"]), package_unit="g",
            weight_basis="reviewed_manual_evidence", weight_reliability=WeightReliability.EXACT,
            availability="in_stock", recipe_release_id=release_id, snapshot_id=content["snapshot_id"],
            manual_revision_id=entry.version_id, review_decision_id=entry.review_decision_id,
        ), None)

    bindings = [entry for entry, _ in active]
    if bindings:
        candidates = []
        for entry in bindings:
            content = entry.content
            matching = [quote for provider, quote in observations if (
                provider == content["provider"] and quote.product_id == content["product_id"]
                and quote.product_name == content["product_name"] and quote.recipe_release_id == release_id
                and food_id in quote.food_ids and quote.queried_at <= as_of
            )]
            if matching:
                candidates.append(max(matching, key=lambda q: (q.queried_at, q.snapshot_id or 0)))
        if not candidates:
            return PricePreview(None, "PRICE_LOCKED_SKU_UNAVAILABLE")
        quote = candidates[0]
        freshness = bindings[0].freshness
        reason = observation_reason(quote, as_of, freshness)
        return PricePreview(quote if reason is None else None, reason)

    if expired:
        return PricePreview(None, "PRICE_EXPIRED")
    if fallback and (fallback.recipe_release_id != release_id or food_id not in fallback.food_ids):
        return PricePreview(None, "PRICE_SNAPSHOT_SCOPE_MISMATCH")
    reason = observation_reason(fallback, as_of) if fallback else "NO_PRICE_DATA"
    return PricePreview(fallback if reason is None else None, reason)

OBSERVATIONS = f"""
    SELECT {SNAPSHOT_COLUMNS},
        ARRAY(SELECT f.food_id FROM price_snapshot_foods f
              WHERE f.snapshot_id = s.id AND f.recipe_release_id = r.recipe_release_id
              ORDER BY f.food_id) AS bound_food_ids
    {SNAPSHOT_FROM}
    WHERE r.recipe_release_id = :release_id AND {FOOD_BINDING} AND s.queried_at <= :as_of
"""
PAGE_SIZE = 100
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")


def list_observations(db, release_id, food_id, as_of, before=None, include_product_name=False):
    """Read immutable, release-bound rows with a deterministic time/ID cursor."""
    query = OBSERVATIONS
    args = dict(release_id=release_id, food_id=food_id, as_of=as_of, page_size=PAGE_SIZE + 1)
    if before is not None:
        query += " AND (s.queried_at, s.id) < (:before_at, :before_id)"
        args.update(before_at=before[0], before_id=before[1])
    query += " ORDER BY s.queried_at DESC, s.id DESC LIMIT :page_size"
    rows = db.execute(sa.text(query), args).mappings().all()
    items = [observation_view(row, as_of, include_product_name) for row in rows[:PAGE_SIZE]]
    cursor = None
    if len(rows) > PAGE_SIZE:
        last = items[-1]
        cursor = f'{last["queried_at"]}/{last["snapshot_id"]}'
    return {"items": items, "next_cursor": cursor}


def observation_view(row, as_of, include_product_name=False):
    historical = row["usage_status"] == "historical_requires_refresh"
    end = None
    if historical:
        reason = "PRICE_REVALIDATION_REQUIRED"
    else:
        if row["status"] == "success":
            end = cutoff(row["queried_at"], row["valid_until"])
        try:
            quote = materialize_quote(row)
            reason = observation_reason(quote, as_of)
            if reason and not IDENTIFIER.fullmatch(reason):
                reason = f'PRICE_{quote.status.value.upper()}'
        except (ValidationError, TypeError, OverflowError):
            reason = "PRICE_OBSERVATION_INVALID"
    raw = row["price"]
    if row["currency"] != "TWD" or raw is None or not raw.is_finite() or raw < 0:
        raw = None
    sku = row["product_id"]
    result = {
        "snapshot_id": row["id"],
        "recipe_release_id": row["recipe_release_id"],
        "food_ids": row["bound_food_ids"],
        "provider": row["provider"],
        "product_id": sku if sku and IDENTIFIER.fullmatch(sku) else None,
        "raw_price_twd": format(raw.normalize(), "f") if raw is not None else None,
        "queried_at": utc_stamp(row["queried_at"]),
        "effective_valid_until": utc_stamp(end) if end else None,
        "calculation_usable": reason is None,
        "missing_reason": reason,
        "usage_status": ("historical_requires_refresh" if historical else
                         "unusable" if reason else "live_observation"),
    }
    if include_product_name:
        result["product_name"] = row["product_name"]
    return result
