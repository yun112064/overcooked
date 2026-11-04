"""Whole-dish portion adjustment for database menus.

A recipe serving is a stand-alone portion, so three dishes plus rice often
exceed one person's meal target (2026-11-02 decision, 0.5–1.0).  Since
2026-11-03 portions follow the participants' targets: when the unchanged menu
cannot meet the T12 tolerance, the whole menu is first scaled by one factor
(0.01–2.0 recipe serving, 0.01 steps, closest to 1 first) with the staple
filling the rest; only then may dishes differ (0.1–2.0, 0.1 steps, smallest
spread first).  A dish is scaled as a whole: every ingredient line of the dish gets
the same factor, so the recipe's proportions never change.  Reviewed
adjustable lines (the staple) keep their own reviewed range.

Since 2026-11-04 the tolerance is one-sided: energy at most 110% of the target
(no lower limit) and protein at least 90% (no upper limit), see
``service.TOLERANCE_RATIOS``.  Candidates found here are re-validated by
``validate_meal`` (exact Fraction arithmetic, cooking units) before use.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from itertools import product
from typing import Iterator, Mapping

from .service import NutritionPolicy, RecipeCandidate, calculate_nutrition, tolerance_bounds
from .targets import MealTargets, exact

POLICY_VERSION = 'dish-portion-2026-11-04-v3'
UNIFORM_FACTORS = tuple(Fraction(k, 100) for k in range(1, 201))  # 0.01, …, 2.00
FACTORS = tuple(Fraction(k, 10) for k in range(1, 21))  # 0.1, 0.2, …, 2.0
FACTOR_RANGE = ('0.01', '2.0')
FACTOR_STEP = {'same_for_all_dishes': '0.01', 'per_dish': '0.1'}
MAX_DISHES = 6
# Above this many dishes only equal factors are searched (20^5 combinations is too many).
MAX_GRID_DISHES = 4
MAX_CANDIDATES = 40


def _per_gram(recipe: RecipeCandidate, line, policy: NutritionPolicy) -> tuple[Fraction, Fraction] | None:
    one = calculate_nutrition(replace(recipe, ingredients=(replace(line, amount_g=Fraction(1)),)), policy=policy)
    energy, protein = one.totals.get('energy_kcal'), one.totals.get('protein_g')
    if one.missing or energy is None or protein is None:
        return None
    return energy, protein


_UNBOUNDED = Fraction(10 ** 9)


def _interval(total: Fraction, target: Fraction, coefficient: Fraction,
              key: str) -> tuple[Fraction, Fraction] | None:
    """Grams g keeping total + g·coefficient inside the T12 tolerance for ``key``."""
    bound_low, bound_high = tolerance_bounds(target, key)
    low = bound_low - total if bound_low is not None else -_UNBOUNDED
    high = bound_high - total if bound_high is not None else _UNBOUNDED
    if coefficient == 0:
        return (-_UNBOUNDED, _UNBOUNDED) if low <= 0 <= high else None
    return low / coefficient, high / coefficient


def candidate_factors(recipe: RecipeCandidate, line_dish: Mapping[str, str], targets: MealTargets,
                      policy: NutritionPolicy) -> Iterator[dict[str, Fraction]]:
    """Yield per-dish factors predicted to fit, smallest change first."""
    dishes: dict[str, list[Fraction]] = {}
    adjustable = []
    for line in recipe.ingredients:
        servings = exact(line.original_servings) if line.original_servings is not None else None
        if servings is None or servings <= 0:
            return
        coefficient = _per_gram(recipe, line, policy)
        if coefficient is None:
            return
        if line.adjustment_status == 'approved':
            if line.adjustable_min_g is None or line.adjustable_max_g is None:
                return
            adjustable.append((exact(line.adjustable_min_g) / servings, exact(line.adjustable_max_g) / servings,
                               coefficient))
            continue
        dish = line_dish.get(line.line_id)
        if dish is None:
            return
        per_serving = exact(line.amount_g) / servings
        vector = dishes.setdefault(dish, [Fraction(0), Fraction(0)])
        vector[0] += per_serving * coefficient[0]
        vector[1] += per_serving * coefficient[1]
    if not dishes or len(dishes) > MAX_DISHES or len(adjustable) > 1:
        return
    names = sorted(dishes)
    reference = targets.reference
    uniform = [(f,) * len(names) for f in sorted(UNIFORM_FACTORS, key=lambda f: (abs(1 - f), -f))]
    # Per-dish factors only when every dish cannot share one; smallest spread first.
    mixed = [] if len(names) > MAX_GRID_DISHES else sorted(
        (fs for fs in product(FACTORS, repeat=len(names)) if max(fs) != min(fs)),
        key=lambda fs: (max(fs) - min(fs), sum(abs(1 - f) for f in fs), [abs(1 - f) for f in fs], [-f for f in fs]))
    combos = uniform + mixed
    yielded = 0
    for factors in combos:
        if all(f == 1 for f in factors):
            continue
        energy = sum((f * dishes[n][0] for f, n in zip(factors, names)), Fraction())
        protein = sum((f * dishes[n][1] for f, n in zip(factors, names)), Fraction())
        if adjustable:
            low, high, (ce, cp) = adjustable[0]
            spans = [_interval(energy, reference['energy_kcal'], ce, 'energy_kcal'),
                     _interval(protein, reference['protein_g'], cp, 'protein_g')]
            if None in spans:
                continue
            lo = max([low] + [s[0] for s in spans if s])
            hi = min([high] + [s[1] for s in spans if s])
            if lo > hi:
                continue
        elif not (_interval(energy, reference['energy_kcal'], Fraction(0), 'energy_kcal')
                  and _interval(protein, reference['protein_g'], Fraction(0), 'protein_g')):
            continue
        yield dict(zip(names, factors))
        yielded += 1
        if yielded >= MAX_CANDIDATES:
            return


def scale_dishes(recipe: RecipeCandidate, line_dish: Mapping[str, str],
                 factors: Mapping[str, Fraction]) -> RecipeCandidate:
    rows = []
    for line in recipe.ingredients:
        factor = factors.get(line_dish.get(line.line_id, ''), None)
        rows.append(line if factor is None or line.adjustment_status == 'approved'
                    else replace(line, amount_g=exact(line.amount_g) * factor))
    return replace(recipe, ingredients=tuple(rows))
