import ShoppingBasketOutlined from '@mui/icons-material/ShoppingBasketOutlined'
import { Alert, Button, Stack } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { api, errorMessage } from '../api/client'

export default function MarketCheckoutButton({ planId }: { planId: string }) {
  const handoff = useMutation({
    mutationFn: async () => {
      const result = (await api.post<{ checkout_url: string }>('/sim-market/handoffs', { plan_id: planId })).data
      const url = new URL(result.checkout_url)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid checkout URL')
      return url.href
    },
  })
  async function openMarket() {
    // Open synchronously so browsers do not classify the completed API call as a popup.
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const url = await handoff.mutateAsync()
      if (tab && !tab.closed) tab.location.replace(url)
      else window.location.assign(url)
    } catch {
      tab?.close()
    }
  }
  return <Stack spacing={1}>
    <Button variant="contained" startIcon={<ShoppingBasketOutlined />} disabled={handoff.isPending}
      onClick={() => { void openMarket() }}>
      {handoff.isPending ? '開啟超市中…' : '一鍵購入'}
    </Button>
    {handoff.isError && <Alert severity="error">{errorMessage(handoff.error, '暫時無法開啟模擬超市，請重試。')}</Alert>}
  </Stack>
}
