import AddOutlined from '@mui/icons-material/AddOutlined'
import BreakfastDiningOutlined from '@mui/icons-material/BreakfastDiningOutlined'
import CheckCircleOutlineOutlined from '@mui/icons-material/CheckCircleOutlineOutlined'
import Inventory2Outlined from '@mui/icons-material/Inventory2Outlined'
import RemoveOutlined from '@mui/icons-material/RemoveOutlined'
import RiceBowlOutlined from '@mui/icons-material/RiceBowlOutlined'
import SetMealOutlined from '@mui/icons-material/SetMealOutlined'
import ShoppingBasketOutlined from '@mui/icons-material/ShoppingBasketOutlined'
import SpaOutlined from '@mui/icons-material/SpaOutlined'
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, Paper, Stack, TextField, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import { marketPath, MAX_PACKS, prepareCart, readCart, saveCart } from '../api/simMarket'
import type { MarketCart, MarketCatalog, MarketPlan, MarketProduct } from '../api/simMarket'
import type { Me } from '../api/types'

const marketGreen = '#245643'
const marketPale = '#e7f0e8'
const moneyFormat = new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD' })
const money = (cents: number) => moneyFormat.format(cents / 100)
const categoryIcons = new Map([
  ['蔬果菇類', <SpaOutlined fontSize="large" />],
  ['肉品海鮮', <SetMealOutlined fontSize="large" />],
  ['豆蛋乳品', <BreakfastDiningOutlined fontSize="large" />],
  ['米麵雜糧', <RiceBowlOutlined fontSize="large" />],
])

type Page = 'catalog' | 'checkout'
type CartLine = MarketProduct & { quantity: number }

export default function SimMarketPage({ page }: { page: Page }) {
  const me = useOutletContext<Me>()
  const [params] = useSearchParams()
  const planId = params.get('plan')
  const householdId = me.current_household?.id
  const scope = [me.user.id, householdId]
  const catalog = useQuery({
    queryKey: ['sim-market-catalog', ...scope],
    queryFn: async () => (await api.get<MarketCatalog>('/sim-market/catalog')).data,
  })
  const plan = useQuery({
    queryKey: ['sim-market-plan', ...scope, planId],
    enabled: Boolean(planId),
    queryFn: async () => (await api.get<MarketPlan>(`/meal-plans/${encodeURIComponent(planId ?? '')}`)).data,
    retry: false,
  })

  if (plan.isError) {
    return <Alert severity="error">{errorMessage(plan.error, '無法讀取此家庭的已採用餐單。')}</Alert>
  }
  if (catalog.isError) return <Alert severity="error">{errorMessage(catalog.error, '商品目錄暫時無法載入。')}</Alert>
  if (!catalog.data || (planId && !plan.data)) {
    return <CircularProgress size={28} aria-label="載入模擬超市" />
  }
  if (planId && (plan.data?.status !== 'adopted' || !plan.data.result_version || !plan.data.snapshot_available || !plan.data.prices)) {
    return (
      <Stack spacing={2}>
        <Alert severity="info">這份餐單沒有可用的正式採用缺料快照。</Alert>
        <Button component={Link} to="/sim-market" sx={{ alignSelf: 'start' }}>瀏覽模擬超市</Button>
      </Stack>
    )
  }

  const key = `sim-market:v1:${JSON.stringify([...scope, catalog.data.recipe_release_id, planId])}`
  return <MarketStore key={key} storageKey={key} page={page} catalog={catalog.data} plan={plan.data} />
}

function MarketStore({ storageKey, page, catalog, plan }: {
  storageKey: string; page: Page; catalog: MarketCatalog; plan?: MarketPlan
}) {
  const { quantities, shortages, unavailable, releaseIssue } = prepareCart(plan, catalog)
  const [cart, setCart] = useState<MarketCart>(() => readCart(storageKey, catalog.products, quantities))
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const planId = plan?.id ?? null
  const checkoutPath = marketPath('checkout', planId)
  const catalogPath = marketPath('catalog', planId)
  const returnPath = plan?.meal_request_id ? `/plan/${encodeURIComponent(plan.meal_request_id)}` : '/plan'

  useEffect(() => { saveCart(storageKey, cart) }, [storageKey, cart])

  // A new release can invalidate a plan after its cart was opened. Keep its saved edits private and block checkout.
  const lines: CartLine[] = releaseIssue ? [] : catalog.products.flatMap((product) => {
    const quantity = cart.quantities[product.food_id]
    return quantity > 0 ? [{ ...product, quantity }] : []
  })
  const count = lines.reduce((total, line) => total + line.quantity, 0)
  const totalCents = lines.reduce((total, line) => total + line.price_cents * line.quantity, 0)

  function change(foodId: string, delta: number) {
    if (releaseIssue) return
    setCart((previous) => {
      if (previous.orderId) return previous
      const next = { ...previous.quantities }
      const quantity = Math.min(MAX_PACKS, (next[foodId] ?? 0) + delta)
      if (quantity > 0) next[foodId] = quantity
      else delete next[foodId]
      return { quantities: next, orderId: null }
    })
  }

  function confirmOrder() {
    if (!count) return
    const orderId = `SIM-${crypto.randomUUID().slice(0, 8).toUpperCase()}`
    setCart((previous) => previous.orderId ? previous : { ...previous, orderId })
  }

  const products = catalog.products.filter((product) =>
    (!category || product.category === category) && product.name.toLowerCase().includes(search.trim().toLowerCase()))
  const categories = [...new Set(catalog.products.map((product) => product.category))]

  return (
    <Stack spacing={3} sx={{ pb: { xs: page === 'catalog' ? 9 : 1, md: 1 } }}>
      <Paper component="header" elevation={0} sx={{ borderRadius: 3, overflow: 'hidden', bgcolor: marketGreen, color: '#fff', px: { xs: 2.5, sm: 5 }, py: { xs: 3, sm: 4 } }}>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <Box sx={{ display: 'grid', placeItems: 'center', flexShrink: 0, width: { xs: 42, sm: 56 }, height: { xs: 42, sm: 56 }, borderRadius: '50%', bgcolor: '#ffffff22' }}>
            <ShoppingBasketOutlined />
          </Box>
          <Box>
            <Typography variant="overline" sx={{ color: '#d2e7d7' }}>SIM MARKET · 模擬超市</Typography>
            <Typography component="h1" variant="h4" sx={{ color: 'inherit', fontSize: { xs: '1.38rem', sm: '2rem' }, letterSpacing: 0 }}>Sim’s Supermarket</Typography>
          </Box>
        </Stack>
        <Typography sx={{ mt: 2, color: '#edf4ef' }}>從食譜用到的食材開始，為這一餐挑選要補買的商品。</Typography>
      </Paper>

      {catalog.recipe_release_id === null
        ? <Alert severity="info">目前沒有可用的 T01 商品目錄。</Alert>
        : catalog.products.length === 0 && <Alert severity="info">此 T01 版本目前沒有可展示的商品。</Alert>}
      {releaseIssue && (
        <Alert severity="warning" action={<Button component={Link} to="/sim-market" size="small">瀏覽目前目錄</Button>}>
          {releaseIssue}。此餐單的食材無法自動加入購物車。
        </Alert>
      )}

      {page === 'catalog' ? (
        <>
          <TextField fullWidth size="small" label="搜尋食材" value={search} onChange={(event) => setSearch(event.target.value)}
            sx={{ bgcolor: 'background.paper', maxWidth: 480 }} />
          <Stack component="nav" aria-label="商品分類" direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
            {[null, ...categories].map((item) => (
              <Chip key={item ?? 'all'} label={item ?? '全部商品'} onClick={() => setCategory(item)}
                aria-pressed={category === item}
                sx={category === item ? { bgcolor: marketPale, color: marketGreen, fontWeight: 700 } : undefined} />
            ))}
          </Stack>
          {products.length > 0 ? (
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', md: 'repeat(3, minmax(0, 1fr))' }, gap: 2 }}>
              {products.map((product) => (
                <ProductCard key={product.food_id} product={product} quantity={cart.quantities[product.food_id] ?? 0}
                  disabled={Boolean(releaseIssue || cart.orderId)} onAdd={() => change(product.food_id, 1)} />
              ))}
            </Box>
          ) : catalog.products.length > 0 && <Typography color="text.secondary">找不到符合條件的商品。</Typography>}
          <Paper component="aside" elevation={2} sx={{
            position: { xs: 'fixed', md: 'sticky' }, left: { xs: 12, md: 'auto' }, right: { xs: 12, md: 'auto' },
            bottom: { xs: 'calc(64px + env(safe-area-inset-bottom))', md: 16 },
            zIndex: 2, p: 1.5, borderRadius: 3, bgcolor: 'background.paper',
          }}>
            <Stack direction="row" spacing={1.5} sx={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography sx={{ fontWeight: 700 }} aria-live="polite">{count} 包 · {money(totalCents)}</Typography>
              <Button variant="contained" component={Link} to={checkoutPath}>查看購物車</Button>
            </Stack>
          </Paper>
        </>
      ) : (
        <>
          <Stack direction="row" sx={{ gap: 1, justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography component="h2" variant="h5">{cart.orderId && !releaseIssue ? '模擬訂單已完成' : '模擬結帳'}</Typography>
            <Button component={Link} to={cart.orderId ? '/sim-market' : catalogPath}>繼續選購</Button>
          </Stack>
          {plan && (
            <Alert severity="info">
              來自已採用菜單 · {plan.meal_date}{plan.dishes.length > 0 && ` · ${plan.dishes.map((dish) => dish.dish_name).join('、')}`}
            </Alert>
          )}
          {cart.orderId && !releaseIssue && (
            <Alert icon={<CheckCircleOutlineOutlined />} severity="success">模擬訂單編號 {cart.orderId}</Alert>
          )}
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 2fr) minmax(240px, 1fr)' }, gap: 2 }}>
            <Paper variant="outlined" sx={{ borderRadius: 3, p: { xs: 2, sm: 3 } }}>
              <Typography component="h3" variant="h6" sx={{ mb: 2 }}>購物車品項</Typography>
              {lines.length === 0 ? <Typography color="text.secondary">目前沒有可結帳的商品。</Typography> : (
                <Stack component="ul" spacing={1.5} divider={<Divider flexItem />} sx={{ listStyle: 'none', p: 0, m: 0 }}>
                  {lines.map((line) => (
                    <Stack key={line.food_id} component="li" direction={{ xs: 'column', sm: 'row' }} spacing={1}
                      sx={{ justifyContent: 'space-between', alignItems: { xs: 'start', sm: 'center' } }}>
                      <Box>
                        <Typography sx={{ fontWeight: 700 }}>{line.name}</Typography>
                        <Typography variant="body2" color="text.secondary">
                          {shortages.has(line.food_id) && `菜單缺量 ${shortages.get(line.food_id)} g · `}
                          {line.package_grams} g／包 · 購買 {line.quantity * line.package_grams} g
                        </Typography>
                        <Typography variant="caption" color="text.secondary">模擬單價 {money(line.price_cents)}／包</Typography>
                      </Box>
                      <Stack direction="row" sx={{ alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
                        {!cart.orderId && <IconButton aria-label={`減少${line.name}包數`} onClick={() => change(line.food_id, -1)}><RemoveOutlined /></IconButton>}
                        <Typography aria-live="polite">{line.quantity} 包</Typography>
                        {!cart.orderId && <IconButton aria-label={`增加${line.name}包數`} onClick={() => change(line.food_id, 1)}
                          disabled={line.quantity >= MAX_PACKS}><AddOutlined /></IconButton>}
                        <Typography sx={{ fontWeight: 700, ml: 1 }}>{money(line.price_cents * line.quantity)}</Typography>
                        {!cart.orderId && <Button size="small" aria-label={`移除${line.name}`} onClick={() => change(line.food_id, -line.quantity)}>移除</Button>}
                      </Stack>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Paper>
            <Paper variant="outlined" sx={{ borderRadius: 3, p: { xs: 2, sm: 3 }, alignSelf: 'start' }}>
              <Stack spacing={2}>
                <Typography component="h3" variant="h6">金額摘要</Typography>
                <Stack direction="row" sx={{ justifyContent: 'space-between' }} aria-live="polite">
                  <Typography>模擬商品總額</Typography>
                  <Typography sx={{ fontWeight: 700 }}>{money(totalCents)}</Typography>
                </Stack>
                <Typography variant="caption" color="text.secondary">此頁僅供模擬，不會付款或配送。</Typography>
                {!cart.orderId && <Button variant="contained" onClick={confirmOrder} disabled={count === 0}>確認模擬訂單</Button>}
                {cart.orderId && !plan && <Button onClick={() => setCart({ quantities: {}, orderId: null })}>開始新購物車</Button>}
              </Stack>
            </Paper>
          </Box>
          {unavailable.length > 0 && (
            <Paper variant="outlined" sx={{ borderRadius: 3, p: { xs: 2, sm: 3 } }}>
              <Typography component="h3" variant="h6">未加入的食材</Typography>
              <Box component="ul" sx={{ mb: 0 }}>
                {unavailable.map((item) => (
                  <Typography key={item.foodId} component="li" variant="body2">
                    {item.name}{item.grams && `（缺量 ${item.grams} g）`} · {item.reason}
                  </Typography>
                ))}
              </Box>
            </Paper>
          )}
          {plan && <Button component={Link} to={returnPath} sx={{ alignSelf: 'start' }}>返回餐單</Button>}
        </>
      )}
    </Stack>
  )
}

function ProductCard({ product, quantity, disabled, onAdd }: {
  product: MarketProduct; quantity: number; disabled: boolean; onAdd: () => void
}) {
  return (
    <Paper component="article" variant="outlined" sx={{ display: 'flex', flexDirection: 'column', minWidth: 0, borderRadius: 3, overflow: 'hidden' }}>
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 85, bgcolor: marketPale, color: marketGreen }}>
        {categoryIcons.get(product.category) ?? <Inventory2Outlined fontSize="large" />}
      </Box>
      <Stack spacing={0.5} sx={{ flex: 1, p: 1.75, minWidth: 0 }}>
        <Typography component="h2" sx={{ fontWeight: 700, overflowWrap: 'anywhere' }}>{product.name}</Typography>
        <Typography variant="body2" color="text.secondary">{product.category} · {product.package_grams} g／包</Typography>
        <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', pt: 0.5, mt: 'auto' }}>
          <Box>
            <Typography sx={{ fontWeight: 700 }}>{money(product.price_cents)}</Typography>
            <Typography variant="caption" color="text.secondary">模擬售價{quantity > 0 && ` · 已選 ${quantity} 包`}</Typography>
          </Box>
          <IconButton aria-label={`加入${product.name}`} disabled={disabled || quantity >= MAX_PACKS}
            onClick={onAdd} sx={{ bgcolor: marketPale, color: marketGreen }}><AddOutlined /></IconButton>
        </Stack>
      </Stack>
    </Paper>
  )
}
