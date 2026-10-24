from __future__ import annotations

import math
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Cart,
    CartItem,
    DeliveryEvent,
    DeliveryOrder,
    ImportCommand,
    OrderItem,
    Product,
)

DELIVERY_FEE = 49
ORDER_FLOW = (
    ("placed", "訂單已送出"),
    ("accepted", "門市已接單"),
    ("picking", "店員正在備貨"),
    ("delivering", "外送夥伴配送中"),
    ("delivered", "商品已送達"),
)

SEED_PRODUCTS = (
    ("prd-cabbage", "cabbage", "高麗菜", "蔬果", "🥬", 600, 55, 24),
    ("prd-tomato", "tomato", "牛番茄", "蔬果", "🍅", 500, 79, 18),
    ("prd-carrot", "carrot", "履歷紅蘿蔔", "蔬果", "🥕", 600, 49, 20),
    ("prd-egg", "egg", "洗選鮮雞蛋", "蛋奶", "🥚", 600, 88, 30),
    ("prd-chicken", "chicken_thigh", "去骨雞腿肉", "肉品", "🍗", 300, 135, 12),
    ("prd-pork", "ground_pork", "台灣豬絞肉", "肉品", "🥩", 300, 109, 15),
    ("prd-rice", "rice", "台灣優質米", "主食", "🍚", 2000, 199, 40),
    ("prd-milk", "milk", "全脂鮮乳", "蛋奶", "🥛", 936, 92, 20),
)


def seed_products(db: Session) -> None:
    if db.scalar(select(Product.id).limit(1)) is not None:
        return
    db.add_all(
        [
            Product(
                id=row[0], ingredient_id=row[1], name=row[2], category=row[3],
                emoji=row[4], package_weight_g=row[5], price_twd=row[6], stock=row[7]
            )
            for row in SEED_PRODUCTS
        ]
    )
    db.commit()


def products(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).order_by(Product.category, Product.name)))


def get_or_create_cart(db: Session, cart_id: str | None = None) -> Cart:
    cart = db.get(Cart, cart_id) if cart_id else None
    if cart is not None and cart.status == "open":
        return cart
    cart = Cart(id=str(uuid.uuid4()), status="open")
    db.add(cart)
    db.flush()
    return cart


def load_cart(db: Session, cart_id: str) -> Cart:
    cart = db.scalar(
        select(Cart)
        .where(Cart.id == cart_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    if cart is None:
        raise HTTPException(404, "找不到購物車")
    return cart


def cart_payload(cart: Cart) -> dict:
    items = [
        {
            "product_id": item.product_id,
            "name": item.product.name,
            "emoji": item.product.emoji,
            "package_weight_g": item.product.package_weight_g,
            "unit_price_twd": item.product.price_twd,
            "quantity": item.quantity,
            "line_total_twd": item.quantity * item.product.price_twd,
            "stock": item.product.stock,
        }
        for item in cart.items
    ]
    return {
        "id": cart.id,
        "status": cart.status,
        "items": items,
        "subtotal_twd": sum(item["line_total_twd"] for item in items),
        "item_count": sum(item["quantity"] for item in items),
    }


def _set_cart_item(db: Session, cart: Cart, product: Product, quantity: int) -> None:
    item = db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == product.id
        )
    )
    if item is None:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity))
    else:
        item.quantity = quantity


def add_product(db: Session, cart_id: str | None, product_id: str, quantity: int) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "找不到商品")
    cart = get_or_create_cart(db, cart_id)
    existing = db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == product.id
        )
    )
    wanted = quantity + (existing.quantity if existing else 0)
    if wanted > product.stock:
        raise HTTPException(409, f"{product.name} 庫存不足")
    _set_cart_item(db, cart, product, wanted)
    db.commit()
    return cart_payload(load_cart(db, cart.id))


def import_shortages(db: Session, payload) -> dict:
    previous = db.get(ImportCommand, payload.idempotency_key)
    if previous is not None:
        return cart_payload(load_cart(db, previous.cart_id))
    cart = get_or_create_cart(db, payload.cart_id)
    for shortage in payload.items:
        product = db.scalar(
            select(Product).where(Product.ingredient_id == shortage.ingredient_id)
        )
        if product is None:
            continue
        packages = math.ceil(shortage.missing_grams / product.package_weight_g)
        if packages > product.stock:
            raise HTTPException(409, f"{product.name} 庫存不足")
        current = db.scalar(
            select(CartItem).where(
                CartItem.cart_id == cart.id, CartItem.product_id == product.id
            )
        )
        _set_cart_item(db, cart, product, max(current.quantity if current else 0, packages))
    db.add(ImportCommand(idempotency_key=payload.idempotency_key, cart_id=cart.id))
    db.commit()
    return cart_payload(load_cart(db, cart.id))


def update_item(db: Session, cart_id: str, product_id: str, quantity: int) -> dict:
    cart = load_cart(db, cart_id)
    if cart.status != "open":
        raise HTTPException(409, "此購物車已結帳")
    item = db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart_id, CartItem.product_id == product_id
        )
    )
    if item is None:
        raise HTTPException(404, "購物車內沒有這項商品")
    if quantity == 0:
        db.delete(item)
    elif quantity > item.product.stock:
        raise HTTPException(409, f"{item.product.name} 庫存不足")
    else:
        item.quantity = quantity
    db.commit()
    return cart_payload(load_cart(db, cart_id))


def checkout(db: Session, payload) -> dict:
    cart = load_cart(db, payload.cart_id)
    if cart.status != "open" or not cart.items:
        raise HTTPException(409, "購物車是空的或已結帳")
    for item in cart.items:
        if item.quantity > item.product.stock:
            raise HTTPException(409, f"{item.product.name} 庫存不足")
    subtotal = sum(item.quantity * item.product.price_twd for item in cart.items)
    order = DeliveryOrder(
        id=str(uuid.uuid4()), cart_id=cart.id, recipient=payload.recipient,
        address=payload.address, subtotal_twd=subtotal,
        delivery_fee_twd=DELIVERY_FEE, total_twd=subtotal + DELIVERY_FEE,
        status="placed", eta_minutes=35,
    )
    db.add(order)
    for item in cart.items:
        item.product.stock -= item.quantity
        order.items.append(
            OrderItem(
                product_id=item.product.id, product_name=item.product.name,
                package_weight_g=item.product.package_weight_g,
                unit_price_twd=item.product.price_twd, quantity=item.quantity,
            )
        )
    order.events.append(DeliveryEvent(status="placed", label="訂單已送出"))
    cart.status = "checked_out"
    db.commit()
    return order_payload(load_order(db, order.id))


def load_order(db: Session, order_id: str) -> DeliveryOrder:
    order = db.scalar(
        select(DeliveryOrder)
        .where(DeliveryOrder.id == order_id)
        .options(selectinload(DeliveryOrder.items), selectinload(DeliveryOrder.events))
    )
    if order is None:
        raise HTTPException(404, "找不到訂單")
    return order


def order_payload(order: DeliveryOrder) -> dict:
    return {
        "id": order.id,
        "status": order.status,
        "recipient": order.recipient,
        "address": order.address,
        "subtotal_twd": order.subtotal_twd,
        "delivery_fee_twd": order.delivery_fee_twd,
        "total_twd": order.total_twd,
        "eta_minutes": order.eta_minutes,
        "items": [
            {"name": item.product_name, "quantity": item.quantity,
             "unit_price_twd": item.unit_price_twd}
            for item in order.items
        ],
        "events": [
            {"status": event.status, "label": event.label,
             "happened_at": event.happened_at.isoformat()}
            for event in order.events
        ],
    }


def advance_order(db: Session, order_id: str) -> dict:
    order = load_order(db, order_id)
    statuses = [status for status, _ in ORDER_FLOW]
    index = statuses.index(order.status)
    if index < len(ORDER_FLOW) - 1:
        status, label = ORDER_FLOW[index + 1]
        order.status = status
        order.eta_minutes = max(0, order.eta_minutes - 9)
        order.events.append(DeliveryEvent(status=status, label=label))
        db.commit()
    return order_payload(load_order(db, order_id))
