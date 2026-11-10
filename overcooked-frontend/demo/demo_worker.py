"""Use the existing persistent job pipeline with a synthetic calculator only."""
import os
import time
from decimal import Decimal
from sqlalchemy import select
from app.db.session import SessionLocal, engine
from app.core.schema_gate import main as schema_gate
from app.db.models import Member
from app.services import meal_request, generation
from app.services.meal_calculation import IngredientNutrition
from app.jobs.worker import run_once

if os.environ.get('DEMO_DATABASE_ONLY') != 'true' or engine.url.database != 'meal_planner_1102_v3_ui_demo_test':
    raise RuntimeError('Refusing to run the demo worker against another database')
if schema_gate() != 0:
    raise SystemExit(1)

def generate(ctx):
    draft = meal_request.load(ctx.input_payload())
    with ctx.factory() as db:
        members = {p.id: p for p in db.scalars(select(Member).where(Member.household_id == ctx.job.household_id))}
        target = meal_request.target_preview(draft, members)
    # Synthetic fixtures are calibrated to show the interface, never used as
    # food composition data or exposed outside this marked demo environment.
    n = Decimal(target['total_n'])
    mass = Decimal(100 * (draft.dish_count or 1))
    e = Decimal(target['target']['energy_kcal']) / n / mass
    p = Decimal(target['target']['protein_g']) / n / mass
    def calculator(rows):
        return [IngredientNutrition({'energy_kcal': e * grams, 'protein_g': p * grams},
            {'sample_id': 'DEMO-SYNTHETIC', 'provider': 'synthetic-ui-demo'}) for _, grams in rows]
    handler = generation.make_generation_handler(responder=None, knowledge_client=None, embedder=None, calculator=calculator)
    return handler(ctx)

generate.persist = generation.persist_result
while True:
    job = run_once(SessionLocal, 'isolated-ui-demo', {'meal_generation': generate})
    time.sleep(0.5 if job is None else 0.1)
