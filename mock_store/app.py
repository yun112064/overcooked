from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .db import Base, create_database
from .schemas import AddCartItemInput, CheckoutInput, ImportShortagesInput, UpdateCartItemInput
from .service import (
    add_product,
    advance_order,
    cart_payload,
    checkout,
    import_shortages,
    load_cart,
    load_order,
    order_payload,
    products,
    seed_products,
    update_item,
)


def create_app(database_url: str | None = None) -> FastAPI:
    engine, SessionLocal = create_database(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            seed_products(db)
        yield
        engine.dispose()

    app = FastAPI(title="FreshDash Mock Supermarket", version="0.1.0", lifespan=lifespan)

    def get_db():
        with SessionLocal() as db:
            yield db

    @app.get("/api/store/products")
    def list_products(db: Session = Depends(get_db)):
        return [
            {
                "id": item.id, "ingredient_id": item.ingredient_id, "name": item.name,
                "category": item.category, "emoji": item.emoji,
                "package_weight_g": item.package_weight_g,
                "price_twd": item.price_twd, "stock": item.stock,
            }
            for item in products(db)
        ]

    @app.get("/api/store/carts/{cart_id}")
    def get_cart(cart_id: str, db: Session = Depends(get_db)):
        return cart_payload(load_cart(db, cart_id))

    @app.post("/api/store/carts/items")
    def post_cart_item(payload: AddCartItemInput, db: Session = Depends(get_db)):
        return add_product(db, payload.cart_id, payload.product_id, payload.quantity)

    @app.patch("/api/store/carts/{cart_id}/items/{product_id}")
    def patch_cart_item(cart_id: str, product_id: str, payload: UpdateCartItemInput,
                        db: Session = Depends(get_db)):
        return update_item(db, cart_id, product_id, payload.quantity)

    @app.post("/api/store/carts/import-shortages")
    def post_shortages(payload: ImportShortagesInput, db: Session = Depends(get_db)):
        return import_shortages(db, payload)

    @app.post("/api/store/orders")
    def post_order(payload: CheckoutInput, db: Session = Depends(get_db)):
        return checkout(db, payload)

    @app.get("/api/store/orders/{order_id}")
    def get_order(order_id: str, db: Session = Depends(get_db)):
        return order_payload(load_order(db, order_id))

    @app.post("/api/store/orders/{order_id}/advance")
    def post_advance(order_id: str, db: Session = Depends(get_db)):
        return advance_order(db, order_id)

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "dist", html=True), name="site")
    return app


app = create_app()
