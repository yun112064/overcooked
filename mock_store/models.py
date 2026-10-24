from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "store_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ingredient_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), index=True)
    emoji: Mapped[str] = mapped_column(String(12))
    package_weight_g: Mapped[int] = mapped_column(Integer)
    price_twd: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)


class Cart(Base):
    __tablename__ = "shopping_carts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "product_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cart_id: Mapped[str] = mapped_column(ForeignKey("shopping_carts.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("store_products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class ImportCommand(Base):
    __tablename__ = "store_import_commands"

    idempotency_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    cart_id: Mapped[str] = mapped_column(ForeignKey("shopping_carts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeliveryOrder(Base):
    __tablename__ = "delivery_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cart_id: Mapped[str] = mapped_column(ForeignKey("shopping_carts.id"), unique=True)
    recipient: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(Text)
    subtotal_twd: Mapped[int] = mapped_column(Integer)
    delivery_fee_twd: Mapped[int] = mapped_column(Integer)
    total_twd: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="placed", index=True)
    eta_minutes: Mapped[int] = mapped_column(Integer, default=35)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[OrderItem]] = relationship(cascade="all, delete-orphan")
    events: Mapped[list[DeliveryEvent]] = relationship(
        cascade="all, delete-orphan", order_by="DeliveryEvent.id"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("delivery_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(36))
    product_name: Mapped[str] = mapped_column(String(120))
    package_weight_g: Mapped[int] = mapped_column(Integer)
    unit_price_twd: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("delivery_orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(80))
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
