import type { MarketCatalog, MarketPlan } from './simMarket'

type Checkout = { handoff_id: string; expires_at: number; catalog: MarketCatalog; plan: MarketPlan }
let token: string | null | undefined

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
