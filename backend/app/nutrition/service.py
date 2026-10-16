"""Versioned T03 rules. Unconfirmed rules never receive implicit defaults."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, Inexact, localcontext
from fractions import Fraction
from pathlib import Path


class TargetInputError(ValueError):
    """Stable INVALID_INPUT with a more specific reason for API adapters."""

    code = "INVALID_INPUT"

    def __init__(self, reason: str, field: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.field = field

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": str(self),
            "details": {"reason": self.reason, "field": self.field},
        }


def _decimal(value: object, field: str) -> Decimal:
    # Python floats carry binary approximation. JSON readers use parse_float=Decimal.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TargetInputError("INVALID_NUMBER", field, "Use a decimal string or number.")
    try:
        number = Decimal(value)
    except DecimalException:
        raise TargetInputError("INVALID_NUMBER", field, "Invalid decimal value.") from None
    if not number.is_finite():
        raise TargetInputError("INVALID_NUMBER", field, "A finite number is required.")
    return number


def _required(data: Mapping, field: str) -> object:
    if field not in data or data[field] is None:
        raise TargetInputError("REQUIRED_TARGET_INPUT_MISSING", field, "Required input is missing.")
    return data[field]


@dataclass(frozen=True)
class MemberInput:
    member_id: str
    age_years: int
    equation_sex: str
    weight_kg: Decimal
    activity_category: str
    calculation_date: date
    goal: str

    @classmethod
    def from_mapping(cls, data: Mapping) -> MemberInput:
        fields = {
            "member_id", "age_years", "equation_sex", "weight_kg",
            "activity_category", "calculation_date", "goal",
        }
        if not isinstance(data, Mapping):
            raise TargetInputError("INVALID_MEMBER", "member", "Member input must be an object.")
        if set(data) - fields:
            raise TargetInputError("UNKNOWN_FIELD", "member", "Unexpected member input field.")
        member_id = _required(data, "member_id")
        if not isinstance(member_id, str) or not member_id.strip():
            raise TargetInputError("INVALID_MEMBER_ID", "member_id", "A member identifier is required.")
        age = _required(data, "age_years")
        if type(age) is not int or age < 0:
            raise TargetInputError("INVALID_AGE", "age_years", "Age must be a nonnegative whole number.")
        sex = _required(data, "equation_sex")
        if sex not in ("M", "F"):
            raise TargetInputError("UNSUPPORTED_EQUATION_SEX", "equation_sex", "Use M or F from the source equation.")
        weight = _decimal(_required(data, "weight_kg"), "weight_kg")
        if weight <= 0:
            raise TargetInputError("INVALID_WEIGHT", "weight_kg", "Weight must be positive.")
        activity = _required(data, "activity_category")
        if activity not in ("sedentary", "low_active", "active", "highly_active"):
            raise TargetInputError("INVALID_ACTIVITY", "activity_category", "Choose one of the four official activity categories.")
        goal = _required(data, "goal")
        if goal not in ("general", "maintain", "muscle_gain", "fat_loss"):
            raise TargetInputError("INVALID_GOAL", "goal", "Unknown nutrition goal.")
        date_value = _required(data, "calculation_date")
        try:
            if not isinstance(date_value, str):
                raise ValueError
            parsed_date = date.fromisoformat(date_value)
            if parsed_date.isoformat() != date_value:
                raise ValueError
        except ValueError:
            raise TargetInputError("INVALID_DATE", "calculation_date", "Use a YYYY-MM-DD date.") from None
        return cls(member_id, age, sex, weight, activity, parsed_date, goal)

    def snapshot(self) -> dict:
        return {
            "member_id": self.member_id,
            "age_years": self.age_years,
            "equation_sex": self.equation_sex,
            "weight_kg": str(self.weight_kg),
            "activity_category": self.activity_category,
            "calculation_date": self.calculation_date.isoformat(),
            "goal": self.goal,
        }


class NutritionTargetService:
    """Calculate explicit versioned daily and meal targets without implicit defaults."""

    def __init__(self, rule_path: str | Path):
        raw = Path(rule_path).read_bytes()
        rule = json.loads(raw)
        # Every historical version is explicit and immutable; no 'latest' fallback.
        versions = {
            "hpa-dri8-mvp-0.1.0": "57a1327d6eb3e3e14b69745ee8ad41e254495713b44088a2ed253d30f2db80aa",
            "hpa-dri8-mvp-0.2.0": "cdd29bd858a29ee60d765c67d5b207ac6b9ec495fb41f1da330ef76f691efc85",
        }
        if not isinstance(rule, dict) or not isinstance(rule.get("rule_version"), str) or rule["rule_version"] not in versions:
            raise TargetInputError("UNKNOWN_RULE_VERSION", "rule_version", "Unsupported nutrition rule version.")
        rule_sha256 = hashlib.sha256(raw).hexdigest()
        if rule_sha256 != versions[rule["rule_version"]]:
            raise TargetInputError("RULE_CONFIG_VERSION_CONFLICT", "rule_version", "Rule content differs from this frozen version. Publish a new version before changing rules.")
        self._rule = rule
        self._rule_sha256 = rule_sha256

    @property
    def rule_version(self) -> str:
        return self._rule["rule_version"]

    def validate_portion(self, value: object) -> Decimal:
        portion = _decimal(value, "n")
        if portion not in {Decimal(n) for n in self._rule["allowed_portions"]}:
            raise TargetInputError("INVALID_PORTION", "n", "Choose 0.7, 0.85, 1, 1.15 or 1.3.")
        return portion

    def energy_baseline(self, data: Mapping) -> dict:
        """Return E0 only, regardless of goal, never an adjusted daily target."""
        member = MemberInput.from_mapping(data)
        config = self._rule["energy"]
        # Increase precision for supplied decimal digits; trap rounding rather than hiding it.
        with localcontext() as ctx:
            ctx.prec = max(50, 2 * len(member.weight_kg.as_tuple().digits) + len(str(member.age_years)) + 32)
            ctx.traps[Inexact] = True
            try:
                per_kg = (
                    Decimal(config["intercept"])
                    + Decimal(config["male_coefficient"]) * (member.equation_sex == "M")
                    + Decimal(config["age_coefficient"]) * member.age_years
                    + Decimal(config["weight_coefficient"]) * member.weight_kg
                )
                ree = per_kg * member.weight_kg
                pal = Decimal(config["pal"][member.activity_category])
                energy = ree * pal
                if per_kg <= 0 or energy <= 0:
                    raise TargetInputError("UNSUPPORTED_TARGET_SCOPE", "member", "The source equation cannot produce a positive baseline for this input.")
            except DecimalException:
                raise TargetInputError("UNSUPPORTED_NUMERIC_PRECISION", "member", "Input cannot be calculated without intermediate rounding.") from None
        return {
            "member_id": member.member_id,
            "rule_version": self.rule_version,
            "rule_config_sha256": self._rule_sha256,
            "rule_status": self._rule["rule_status"],
            "source_verification": self._rule["source_verification"],
            "product_scope": self._rule["product_scope"],
            "source_energy_scope": self._rule["source"]["original_energy_scope"],
            "energy_equation_id": config["equation_id"],
            "unadjusted_energy_kcal": str(energy),
            "resting_energy_kcal": str(ree),
            "pal": str(pal),
            "input_snapshot": member.snapshot(),
            "source": deepcopy(self._rule["source"]),
            "daily_target_available": False,
            "approved_for_adoption": False,
        }

    def daily_target(self, data: Mapping) -> dict:
        """Return unscaled D_E and D_P; keep original E0 for reproducibility."""
        member = MemberInput.from_mapping(data)
        adjustments = self._rule["goal_adjustments"]
        if adjustments is None and member.goal in ("muscle_gain", "fat_loss"):
            raise TargetInputError("GOAL_RULE_PENDING_CONFIRMATION", "goal", "Muscle gain and fat loss adjustments have not been confirmed.")
        protein_rule = self._rule["protein"]
        if protein_rule is None:
            raise TargetInputError("PROTEIN_RULE_PENDING_CONFIRMATION", "protein_rule", "The general protein coefficient has not been confirmed.")
        if adjustments is None:
            raise TargetInputError("GOAL_RULE_PENDING_CONFIRMATION", "goal", "Goal adjustments have not been confirmed.")
        result = self.energy_baseline(data)
        adjustment = adjustments[member.goal]
        with localcontext() as ctx:
            ctx.prec = max(50, 2 * len(member.weight_kg.as_tuple().digits) + len(str(member.age_years)) + 40)
            ctx.traps[Inexact] = True
            try:
                energy = (
                    Decimal(result["unadjusted_energy_kcal"])
                    * Decimal(adjustment["energy_multiplier"])
                    + Decimal(adjustment["energy_offset_kcal"])
                )
                protein = member.weight_kg * Decimal(protein_rule["g_per_kg"])
            except DecimalException:
                raise TargetInputError("UNSUPPORTED_NUMERIC_PRECISION", "member", "Input cannot be calculated without intermediate rounding.") from None
        if "minimum_daily_energy_kcal" in adjustment and energy < Decimal(adjustment["minimum_daily_energy_kcal"]):
            raise TargetInputError("DAILY_ENERGY_BELOW_MINIMUM", "goal", "Fat loss would produce less than 1200 kcal/day. No automatic clamping or goal substitution is performed.")
        result.update({
            "daily_reference": {"energy_kcal": str(energy), "protein_g": str(protein)},
            "protein_rule_id": protein_rule["rule_id"],
            "goal_adjustment": {"goal": member.goal, **adjustment},
            "adjustment_sources": deepcopy(self._rule.get("adjustment_sources", [])),
            "daily_target_available": True,
        })
        return result

    def meal_target(self, data: Mapping, meal: str) -> dict:
        """Apply only the meal ratio. n belongs to participation aggregation."""
        if not isinstance(meal, str) or meal not in self._rule["meal_ratios"]:
            raise TargetInputError("INVALID_MEAL", "meal", "Choose breakfast, lunch or dinner.")
        result = self.daily_target(data)
        ratio = Decimal(self._rule["meal_ratios"][meal])
        values = [Decimal(value) for value in result["daily_reference"].values()]
        with localcontext() as ctx:
            ctx.prec = max(50, sum(len(v.as_tuple().digits) for v in values) + 20)
            ctx.traps[Inexact] = True
            try:
                result["meal_reference"] = {
                    key: str(Decimal(value) * ratio)
                    for key, value in result["daily_reference"].items()
                }
            except DecimalException:
                raise TargetInputError("UNSUPPORTED_NUMERIC_PRECISION", "member", "Meal baseline cannot be calculated exactly.") from None
        result.update({"meal": meal, "meal_ratio": str(ratio)})
        return result

    def aggregate_meal_targets(self, participations: list) -> dict:
        """Compose Spec targets from dining/bento entries, each scaled exactly once."""
        if not isinstance(participations, list) or not participations:
            raise TargetInputError("INVALID_PARTICIPATIONS", "participations", "Provide at least one dining or bento entry.")
        entries = []
        entry_ids = set()
        members = {}
        for item in participations:
            if not isinstance(item, Mapping) or set(item) != {"participation_id", "kind", "member", "meal", "n"}:
                raise TargetInputError("INVALID_PARTICIPATION", "participations", "Each entry requires participation_id, kind, member, meal and n.")
            entry_id = item["participation_id"]
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise TargetInputError("INVALID_PARTICIPATION", "participation_id", "A nonempty participation identifier is required.")
            if entry_id in entry_ids:
                raise TargetInputError("DUPLICATE_PARTICIPATION", "participation_id", "Participation identifiers must be unique.")
            entry_ids.add(entry_id)
            if item["kind"] not in ("dining", "bento"):
                raise TargetInputError("INVALID_PARTICIPATION_KIND", "kind", "Choose dining or bento.")
            portion = self.validate_portion(item["n"])
            member = MemberInput.from_mapping(item["member"])
            previous = members.get(member.member_id)
            if previous is not None and previous != member:
                raise TargetInputError("MEMBER_SNAPSHOT_CONFLICT", "member", "One member cannot have conflicting inputs in the same meal plan.")
            members[member.member_id] = member
            target = self.meal_target(item["member"], item["meal"])
            entries.append((item, portion, target))
        dates = {member.calculation_date for member in members.values()}
        if len(dates) != 1:
            raise TargetInputError("CALCULATION_DATE_CONFLICT", "calculation_date", "Participations must use one calculation date.")
        # Fraction preserves all finite decimal inputs and reference division exactly.
        # A recurring reference is never rounded and fed back into validation.
        total_n = sum((Fraction(portion) for _, portion, _ in entries), Fraction())
        totals = {"energy_kcal": Fraction(), "protein_g": Fraction()}
        snapshots = []
        for item, portion, target in entries:
            scaled = {
                key: Fraction(Decimal(value)) * Fraction(portion)
                for key, value in target["meal_reference"].items()
            }
            for key, value in scaled.items():
                totals[key] += value
            snapshots.append({
                "participation_id": item["participation_id"], "kind": item["kind"],
                "n": str(portion), "meal": item["meal"],
                "member_id": target["member_id"], "meal_ratio": target["meal_ratio"],
                "input_snapshot": target["input_snapshot"],
                "daily_reference": target["daily_reference"],
                "meal_reference": target["meal_reference"],
                "scaled_target": {key: _fraction_decimal(value, exact=True) for key, value in scaled.items()},
            })
        references = {key: value / total_n for key, value in totals.items()}
        return {
            "rule_version": self.rule_version, "rule_config_sha256": self._rule_sha256,
            "rule_status": self._rule["rule_status"],
            "source_verification": self._rule["source_verification"],
            "product_scope": self._rule["product_scope"],
            "total_n": _fraction_decimal(total_n, exact=True),
            "target": {key: _fraction_decimal(value, exact=True) for key, value in totals.items()},
            "reference_exact": {
                key: {"numerator": str(value.numerator), "denominator": str(value.denominator)}
                for key, value in references.items()
            },
            "reference_display": {key: _fraction_decimal(value, exact=False) for key, value in references.items()},
            "reference_display_precision": 50,
            "participations": snapshots,
            "daily_target_available": True, "approved_for_adoption": False,
            "source": deepcopy(self._rule["source"]),
            "adjustment_sources": deepcopy(self._rule.get("adjustment_sources", [])),
        }


def _fraction_decimal(value: Fraction, *, exact: bool) -> str:
    """Totals are terminating decimals; only explicitly labelled display may round."""
    with localcontext() as ctx:
        ctx.prec = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 20) if exact else 50
        ctx.traps[Inexact] = exact
        try:
            return str(Decimal(value.numerator) / Decimal(value.denominator))
        except DecimalException:
            raise TargetInputError("UNSUPPORTED_NUMERIC_PRECISION", "participations", "A target cannot be represented exactly.") from None
