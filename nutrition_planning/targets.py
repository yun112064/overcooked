"""Consume the T03 exact target contract without recalculating daily rules."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

REQUIRED_TARGETS = ("energy_kcal", "protein_g")


class PlanningInputError(ValueError):
    """Invalid inputs, distinct from an incomplete or infeasible candidate."""

    code = "INVALID_INPUT"


def exact(value: object) -> Fraction:
    """Reject binary floats; finite Decimal, decimal strings and rationals are exact."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, Fraction)):
        raise PlanningInputError("Use finite decimal strings, Decimal, integers or Fraction.")
    try:
        return Fraction(Decimal(value)) if isinstance(value, str) else Fraction(value)
    except (ValueError, TypeError, ArithmeticError):
        raise PlanningInputError("A finite number is required.") from None


@dataclass(frozen=True)
class MealTargets:
    total_n: Fraction
    reference: Mapping[str, Fraction]
    target: Mapping[str, Fraction]
    snapshot: dict[str, Any]
    approved_for_adoption: bool


def targets_from_t03(aggregate: Mapping[str, Any]) -> MealTargets:
    total_n = exact(aggregate["total_n"])
    target = {key: exact(aggregate["target"][key]) for key in REQUIRED_TARGETS}
    reference = {
        key: Fraction(int(aggregate["reference_exact"][key]["numerator"]),
                      int(aggregate["reference_exact"][key]["denominator"]))
        for key in REQUIRED_TARGETS
    }
    if total_n <= 0 or any(value <= 0 for value in target.values()):
        raise PlanningInputError("Targets and total N must be positive.")
    if any(reference[key] * total_n != target[key] for key in REQUIRED_TARGETS):
        raise PlanningInputError("T03 target and exact reference disagree.")
    return MealTargets(total_n, reference, target, deepcopy(dict(aggregate)),
                       aggregate.get("approved_for_adoption") is True)
