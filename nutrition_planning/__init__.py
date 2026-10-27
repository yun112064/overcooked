"""T12 exact baseline alignment, cooking quantities and nutrition validation."""

from .targets import MealTargets, PlanningInputError, targets_from_t03
from .service import (
    CookingEvidence, IngredientQuantity, NutritionPolicy, RecipeCandidate,
    NutritionEstimate, ToleranceResult, AlignmentResult, MealValidationResult,
    calculate_nutrition, check_tolerance, align_baseline, scale_to_cooking_units,
    validate_meal,
)

__all__ = ["MealTargets", "PlanningInputError", "targets_from_t03", "CookingEvidence",
           "IngredientQuantity", "NutritionPolicy", "RecipeCandidate", "NutritionEstimate",
           "ToleranceResult", "AlignmentResult", "MealValidationResult", "calculate_nutrition",
           "check_tolerance", "align_baseline", "scale_to_cooking_units", "validate_meal"]
