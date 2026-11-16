/**
 * 「一鍵購入」：請後端發一張短效的交接連結，另開分頁到模擬超市（和舊前端的 MarketCheckoutButton 相同）。
 * 整個冰箱門共用一個，所以同一時間只會有一筆在開；pendingId／errorId 說明是哪一份餐單。
 */
import { useMutation } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'

export type MarketCheckout = {
  open: (planId: string) => void
  /** 正在開啟超市的那份餐單 */
  pendingId: string | null
  /** 上一次失敗的那份餐單與原因（文字） */
  errorId: string | null
  errorText: string | null
}

export function useMarketCheckout(): MarketCheckout {
  const handoff = useMutation({
    mutationFn: async (planId: string) => {
      const result = (await api.post<{ checkout_url: string }>('/sim-market/handoffs', { plan_id: planId })).data
      const url = new URL(result.checkout_url)
      // 只接受一般網址，後端回了別的協定（javascript: 之類）就當作失敗
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid checkout URL')
      return url.href
    },
  })

  const open = (planId: string) => {
    if (handoff.isPending) return
    // 要在點擊的當下同步開分頁，等 API 回來才開會被瀏覽器當成彈出視窗擋掉
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    // 用 mutateAsync：就算使用者這時離開冰箱門，開好的空白分頁也會被帶去超市或關掉
    handoff.mutateAsync(planId).then(
      (url) => {
        if (tab && !tab.closed) tab.location.replace(url)
        else window.location.assign(url)
      },
      () => tab?.close(),
    )
  }

  return {
    open,
    pendingId: handoff.isPending ? handoff.variables ?? null : null,
    errorId: handoff.isError ? handoff.variables ?? null : null,
    errorText: handoff.isError ? errorMessage(handoff.error, '暫時無法開啟模擬超市，請重試。') : null,
  }
}
