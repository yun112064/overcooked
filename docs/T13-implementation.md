# Ticket 13 implementation handoff

Ticket 13 implements missing-ingredient detection, full package spend,
proportional meal cost, and budget validation on top of Ticket 02 retail data.

## Code

- `backend/app/services/meal_cost.py`: shortage, package count, dual totals and budget status.
- `backend/app/pricing_sources/repository.py`: read-only selection of verified, active and non-expired Ticket 02 snapshots.
- `backend/app/pricing_sources/quotes.py`: traceable quote contract.
- `tests/test_t13_meal_cost.py`: AC14, AC15 and missing-data boundary tests.

No new table is added and source price rows are never modified. The meal workflow
must establish household authorization before it calls `compute_meal_cost_from_db`.
The frontend should render only `missing_items`; owned ingredients still retain
their recipe grams for the independent nutrition calculation.
