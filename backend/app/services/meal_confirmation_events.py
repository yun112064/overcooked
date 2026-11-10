"""Record only milestones represented by the confirmed meal snapshot."""
from sqlalchemy.orm import Session

from app.db.models import AdoptedMealPlan, MealConfirmationEvent, utcnow


def record(db: Session, plan: AdoptedMealPlan) -> None:
    """Called once for a new adoption, inside the same transaction."""
    snapshot = plan.snapshot
    result = snapshot.get("result") or {}
    calculation = result.get("calculation") or {}
    ingredients = calculation.get("ingredients") or any(
        dish.get("ingredients") for dish in snapshot.get("dishes") or []
    )
    nutrition = calculation.get("final") or (snapshot.get("nutrition") or {}).get("totals") or {}
    cost = result.get("cost") or {}

    stages = ["confirmed", "snapshot_saved", "history_saved"]
    if ingredients:
        stages.append("ingredients_saved")
    if any(nutrition.get(key) is not None for key in ("energy_kcal", "protein_g")):
        stages.append("nutrition_saved")
    if cost.get("status") == "computed":
        stages.append("cost_saved")

    recorded_at = utcnow()
    db.add_all([
        MealConfirmationEvent(plan_id=plan.id, sequence=sequence, stage=stage, occurred_at=recorded_at)
        for sequence, stage in enumerate(stages, start=1)
    ])
