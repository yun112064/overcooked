"""T12 exact baseline alignment, cooking quantities and nutrition validation."""

from .targets import MealTargets, PlanningInputError, targets_from_t03
from .service import (
    CookingEvidence, IngredientQuantity, NutritionPolicy, RecipeCandidate,
    NutritionEstimate, ToleranceResult, AlignmentResult, MealValidationResult,
    calculate_nutrition, check_tolerance, align_baseline, scale_to_cooking_units,
    validate_meal,
)
from .integration import (
    RecipeDataUnavailable, RecipeRows, InputIssue, RecipeLineProfile, PreparedRecipe,
    DatabaseMenuResult, load_database_recipe_rows, recipe_line_digest,
    prepare_database_recipe, validate_database_menu,
)
from .t04_bridge import T04CookingResult, apply_t04_results

__all__ = ["MealTargets", "PlanningInputError", "targets_from_t03", "CookingEvidence",
           "IngredientQuantity", "NutritionPolicy", "RecipeCandidate", "NutritionEstimate",
           "ToleranceResult", "AlignmentResult", "MealValidationResult", "calculate_nutrition",
           "check_tolerance", "align_baseline", "scale_to_cooking_units", "validate_meal",
           "RecipeDataUnavailable", "RecipeRows", "InputIssue", "RecipeLineProfile", "PreparedRecipe",
           "DatabaseMenuResult", "load_database_recipe_rows", "recipe_line_digest",
           "prepare_database_recipe", "validate_database_menu", "T04CookingResult", "apply_t04_results"]
