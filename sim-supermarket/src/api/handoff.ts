import type { MarketCatalog, MarketPlan } from './simMarket'

type Checkout = { handoff_id: string; expires_at: number; catalog: MarketCatalog; plan: MarketPlan; order?: { order_id: string; lines: { food_id: string; quantity: number }[] } | null }
let token: string | null | undefined

export async function submitOrder(quantities: Record<string, number>, quote_ids: Record<string, number>) {
  const response = await fetch('/market-api/orders', {
    method: 'POST', credentials: 'omit', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: checkoutToken(), quantities, quote_ids }),
  })
  if (!response.ok) throw new Error('結帳未完成，請確認購物連結與報價有效後重試。')
  return response.json() as Promise<{ order_id: string }>
}

export function checkoutToken() {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const incoming = fragment.get('handoff')
  if (incoming) {
    token = incoming
    // Fragments do not reach the server. Remove it before rendering any links.
    history.replaceState(null, '', window.location.pathname)
    try { sessionStorage.setItem('sim-checkout-grant', token) } catch { /* in-memory works */ }
  } else if (token === undefined) {
    try { token = sessionStorage.getItem('sim-checkout-grant') } catch { token = null }
  }
  return token
}

export async function loadCheckout(grant: string): Promise<Checkout> {
  const response = await fetch('/market-api/checkout', {
    method: 'POST', credentials: 'omit', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: grant }),
  })
  if (!response.ok) {
    if (response.status === 410) {
      try { sessionStorage.removeItem('sim-checkout-grant') } catch { /* no storage */ }
      throw new Error('購物連結已失效，請回主程式重新點選一鍵購入。')
    }
    throw new Error('暫時無法載入購物清單，請回主程式重新開啟超市。')
  }
  return response.json()
}
