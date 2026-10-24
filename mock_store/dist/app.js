const state = {
  products: [],
  category: '全部',
  cart: null,
  order: null,
}

const $ = (selector) => document.querySelector(selector)
const money = (value) => `NT$ ${Number(value).toLocaleString('zh-TW')}`

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `操作失敗（${response.status}）`)
  }
  return response.json()
}

function toast(message) {
  const node = $('#toast')
  node.textContent = message
  node.classList.add('show')
  window.setTimeout(() => node.classList.remove('show'), 2200)
}

function saveCart(cart) {
  state.cart = cart
  localStorage.setItem('freshdash-cart', cart.id)
  renderCart()
}

function renderFilters() {
  const categories = ['全部', ...new Set(state.products.map((item) => item.category))]
  $('#filters').innerHTML = categories.map((category) =>
    `<button class="filter ${category === state.category ? 'active' : ''}" data-category="${category}">${category}</button>`
  ).join('')
}

function renderProducts() {
  const shown = state.category === '全部'
    ? state.products
    : state.products.filter((item) => item.category === state.category)
  $('#product-grid').innerHTML = shown.map((item) => `
    <article class="product-card">
      <div class="product-visual"><span>${item.emoji}</span><span class="stock">庫存 ${item.stock}</span></div>
      <div class="product-info">
        <small>${item.category}</small><h3>${item.name}</h3><p>${item.package_weight_g}g／包</p>
        <div class="product-bottom"><span class="price">${money(item.price_twd)}</span><button class="add-button" data-add="${item.id}" aria-label="加入 ${item.name}">＋</button></div>
      </div>
    </article>`).join('')
}

function renderCart() {
  const cart = state.cart || { items: [], item_count: 0, subtotal_twd: 0 }
  $('#cart-count').textContent = cart.item_count
  $('#cart-subtotal').textContent = money(cart.subtotal_twd)
  $('#cart-items').innerHTML = cart.items.length ? cart.items.map((item) => `
    <div class="cart-item">
      <span class="cart-emoji">${item.emoji}</span>
      <div><strong>${item.name}</strong><small>${money(item.unit_price_twd)} × ${item.quantity}</small></div>
      <div class="qty"><button data-qty="${item.product_id}" data-value="${item.quantity - 1}">−</button><span>${item.quantity}</span><button data-qty="${item.product_id}" data-value="${item.quantity + 1}">＋</button></div>
    </div>`).join('') : '<div class="cart-empty">購物車還是空的。<br>從冰箱匯入缺料，或挑幾樣今晚需要的食材。</div>'
}

function openCart() {
  $('#cart-drawer').classList.add('open')
  $('#cart-drawer').setAttribute('aria-hidden', 'false')
  $('#backdrop').classList.add('show')
}

function closeCart() {
  $('#cart-drawer').classList.remove('open')
  $('#cart-drawer').setAttribute('aria-hidden', 'true')
  $('#backdrop').classList.remove('show')
}

async function addProduct(productId) {
  const cart = await api('/api/store/carts/items', {
    method: 'POST',
    body: JSON.stringify({ cart_id: state.cart?.id || null, product_id: productId, quantity: 1 }),
  })
  saveCart(cart)
  toast('已加入購物車')
}

async function importShortages() {
  const key = `fridge-demo-${new Date().toISOString().slice(0, 10)}`
  const cart = await api('/api/store/carts/import-shortages', {
    method: 'POST',
    body: JSON.stringify({
      cart_id: state.cart?.id || null,
      idempotency_key: key,
      items: [
        { ingredient_id: 'cabbage', missing_grams: 700 },
        { ingredient_id: 'chicken_thigh', missing_grams: 450 },
        { ingredient_id: 'egg', missing_grams: 300 },
      ],
    }),
  })
  saveCart(cart)
  openCart()
  toast('缺料已換算包數並加入購物車')
}

async function updateQuantity(productId, quantity) {
  const cart = await api(`/api/store/carts/${state.cart.id}/items/${productId}`, {
    method: 'PATCH', body: JSON.stringify({ quantity }),
  })
  saveCart(cart)
}

const flow = ['placed', 'accepted', 'picking', 'delivering', 'delivered']
const labels = { placed: '訂單已送出', accepted: '門市已接單', picking: '店員正在備貨', delivering: '外送夥伴配送中', delivered: '商品已送達' }

function renderOrder() {
  if (!state.order) return
  const current = flow.indexOf(state.order.status)
  $('#order-preview').innerHTML = `
    <small>訂單 ${state.order.id.slice(0, 8).toUpperCase()}</small>
    <strong>${labels[state.order.status]} · ${state.order.eta_minutes ? `約 ${state.order.eta_minutes} 分鐘` : '已完成'}</strong>
    <div class="timeline">${flow.map((status, index) => `<div class="timeline-item ${index <= current ? 'done' : ''}"><span class="timeline-dot"></span><span>${labels[status]}</span></div>`).join('')}</div>
    ${state.order.status !== 'delivered' ? '<button class="advance" id="advance-order">模擬下一個配送狀態 →</button>' : '<small>謝謝使用 FreshDash 模擬配送！</small>'}`
}

async function checkout(event) {
  event.preventDefault()
  if (!state.cart?.items.length) return toast('購物車還沒有商品')
  const form = new FormData(event.currentTarget)
  state.order = await api('/api/store/orders', {
    method: 'POST',
    body: JSON.stringify({ cart_id: state.cart.id, recipient: form.get('recipient'), address: form.get('address') }),
  })
  localStorage.removeItem('freshdash-cart')
  state.cart = null
  renderCart()
  renderOrder()
  closeCart()
  $('#delivery').scrollIntoView({ behavior: 'smooth' })
  toast('訂單已送出，門市準備接單')
}

async function advanceOrder() {
  state.order = await api(`/api/store/orders/${state.order.id}/advance`, { method: 'POST' })
  renderOrder()
}

async function init() {
  try {
    state.products = await api('/api/store/products')
    const cartId = localStorage.getItem('freshdash-cart')
    if (cartId) state.cart = await api(`/api/store/carts/${cartId}`).catch(() => null)
    renderFilters(); renderProducts(); renderCart()
  } catch (error) { toast(error.message) }
}

document.addEventListener('click', (event) => {
  const add = event.target.closest('[data-add]')
  const qty = event.target.closest('[data-qty]')
  const filter = event.target.closest('[data-category]')
  if (add) addProduct(add.dataset.add).catch((error) => toast(error.message))
  if (qty) updateQuantity(qty.dataset.qty, Number(qty.dataset.value)).catch((error) => toast(error.message))
  if (filter) { state.category = filter.dataset.category; renderFilters(); renderProducts() }
  if (event.target.closest('#advance-order')) advanceOrder().catch((error) => toast(error.message))
})
$('#open-cart').addEventListener('click', openCart)
$('#close-cart').addEventListener('click', closeCart)
$('#backdrop').addEventListener('click', closeCart)
$('#import-shortages').addEventListener('click', () => importShortages().catch((error) => toast(error.message)))
$('#checkout-form').addEventListener('submit', (event) => checkout(event).catch((error) => toast(error.message)))
init()
