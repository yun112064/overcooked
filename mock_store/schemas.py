from __future__ import annotations

from pydantic import BaseModel, Field


class ShortageItem(BaseModel):
    ingredient_id: str = Field(min_length=1, max_length=64)
    missing_grams: int = Field(gt=0, le=100_000)


class ImportShortagesInput(BaseModel):
    cart_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)
    items: list[ShortageItem] = Field(min_length=1, max_length=50)


class AddCartItemInput(BaseModel):
    cart_id: str | None = None
    product_id: str
    quantity: int = Field(default=1, ge=1, le=99)


class UpdateCartItemInput(BaseModel):
    quantity: int = Field(ge=0, le=99)


class CheckoutInput(BaseModel):
    cart_id: str
    recipient: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=6, max_length=300)
