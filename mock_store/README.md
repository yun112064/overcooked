# FreshDash mock supermarket

An independent demo for importing meal-planner shortages into a supermarket cart
and following a delivery order. It does not create real purchases or payments.

## Run

From the repository root:

```powershell
$env:PYTHONPATH='.'
python -m uvicorn mock_store.app:app --reload --port 8013
```

Open `http://127.0.0.1:8013`. The default database is
`mock_store.sqlite3`; set `MOCK_STORE_DATABASE_URL` to use another SQLAlchemy URL.

## Integration contract

`POST /api/store/carts/import-shortages` accepts canonical ingredient IDs,
missing grams, a cart ID and an idempotency key. The backend converts grams to
whole packages and never adds the same import twice. Checkout validates and
decrements stock in one transaction, then stores immutable order-item price
snapshots and delivery events.
