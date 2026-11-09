import AddOutlined from '@mui/icons-material/AddOutlined'
import ArrowBackOutlined from '@mui/icons-material/ArrowBackOutlined'
import ArrowForwardOutlined from '@mui/icons-material/ArrowForwardOutlined'
import CheckCircleOutlineOutlined from '@mui/icons-material/CheckCircleOutlineOutlined'
import Inventory2Outlined from '@mui/icons-material/Inventory2Outlined'
import RemoveOutlined from '@mui/icons-material/RemoveOutlined'
import SearchOutlined from '@mui/icons-material/SearchOutlined'
import ShoppingBagOutlined from '@mui/icons-material/ShoppingBagOutlined'
import ShoppingBasketOutlined from '@mui/icons-material/ShoppingBasketOutlined'
import TuneOutlined from '@mui/icons-material/TuneOutlined'
import {
  Alert, Badge, Box, Button, CircularProgress, Divider, IconButton, InputAdornment, Paper, Stack, TextField, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import { marketPath, MAX_PACKS, prepareCart, readCart, saveCart } from '../api/simMarket'
import type { MarketCart, MarketCatalog, MarketPlan, MarketProduct } from '../api/simMarket'
import type { Me } from '../api/types'
import heroImage from '../assets/sim-market-hero.webp'
import categoryImage from '../assets/sim-market-categories.webp'

const marketInk = '#20201E'
const marketLine = '#DFDEDB'
const marketPaper = '#F5F4F0'
const marketButton = { bgcolor: marketInk, color: '#fff', borderRadius: 0, '&:hover': { bgcolor: '#3C3B37' } }
const marketOutline = { borderColor: marketInk, color: marketInk, borderRadius: 0, '&:hover': { borderColor: marketInk, bgcolor: marketPaper } }
const brandFont = 'Georgia, "Times New Roman", serif'
const moneyFormat = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 })
const money = (cents: number) => `NT$${moneyFormat.format(cents / 100)}`
const categoryImagePositions = new Map([
  ['蔬果菇類', 'left top'], ['肉品海鮮', 'right top'],
  ['豆蛋乳品', 'left bottom'], ['米麵雜糧', 'right bottom'],
])
const contentPadding = { xs: 2, sm: 4, lg: 6 }

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
    return <Alert severity="error" sx={{ m: 2 }}>{errorMessage(plan.error, '無法讀取此家庭的已採用餐單。')}</Alert>
  }
  if (catalog.isError) return <Alert severity="error" sx={{ m: 2 }}>{errorMessage(catalog.error, '商品目錄暫時無法載入。')}</Alert>
  if (!catalog.data || (planId && !plan.data)) {
    return <CircularProgress size={28} aria-label="載入模擬超市" sx={{ m: 3 }} />
  }
  if (planId && (plan.data?.status !== 'adopted' || !plan.data.snapshot_available
    || !(plan.data.snapshot?.market_shortages || (plan.data.result_version && plan.data.prices)))) {
    return (
      <Stack spacing={2} sx={{ p: contentPadding }}>
        <Alert severity="info">這份餐單沒有可用的採用缺料快照。</Alert>
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
  const [filtersOpen, setFiltersOpen] = useState(false)
  const planId = plan?.id ?? null
  const checkoutPath = marketPath('checkout', planId)
  const catalogPath = marketPath('catalog', planId)
  const mealPath = plan?.snapshot?.market_shortages ? '/pick' : '/plan'
  const returnPath = plan?.meal_request_id ? `${mealPath}/${encodeURIComponent(plan.meal_request_id)}` : mealPath

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
  const categoryCounts = new Map<string, number>()
  for (const product of catalog.products) {
    categoryCounts.set(product.category, (categoryCounts.get(product.category) ?? 0) + 1)
  }
  const chooseCategory = (value: string | null) => {
    setCategory(value)
    setFiltersOpen(false)
  }

  return (
    <Box sx={{ color: marketInk, pb: { xs: page === 'catalog' ? 10 : 3, md: 6 } }}>
      <MarketMasthead page={page} catalogPath={catalogPath} checkoutPath={checkoutPath} count={count}
        category={category} categories={[...categoryCounts.keys()]} onCategory={chooseCategory} />
      {page === 'catalog' && <MarketHero />}
      <Stack spacing={page === 'catalog' ? 3 : { xs: 2, md: 3 }}
        sx={{ px: contentPadding, pt: { xs: page === 'catalog' ? 3 : 2, md: 4 } }}>
        {page === 'checkout' && (
          <Box component="header" sx={{ borderBottom: 1, borderColor: marketLine, pb: { xs: 1.5, md: 2 } }}>
            <Button component={Link} to={cart.orderId ? '/sim-market' : catalogPath} startIcon={<ArrowBackOutlined />}
              sx={{ color: marketInk, px: 0.5, mb: 0.75 }}>繼續選購</Button>
            <Typography component="h1" variant="h4" sx={{ color: marketInk }}>
              {cart.orderId && !releaseIssue ? '模擬訂單已完成' : '模擬結帳'}
            </Typography>
          </Box>
        )}

        {catalog.recipe_release_id === null
          ? <Alert severity="info">目前沒有可用的 T01 商品目錄。</Alert>
          : catalog.products.length === 0 && <Alert severity="info">此 T01 版本目前沒有可展示的商品。</Alert>}
        {releaseIssue && (
          <Alert severity="warning" action={<Button component={Link} to="/sim-market" size="small">瀏覽目前目錄</Button>}>
            {releaseIssue}。此餐單的食材無法自動加入購物車。
          </Alert>
        )}

        {page === 'catalog' ? (
          <Box id="market-products" sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: '210px minmax(0, 1fr)' },
            gap: { xs: 2, md: 4 }, scrollMarginTop: '84px' }}>
            <Button variant="contained" startIcon={<TuneOutlined />} onClick={() => setFiltersOpen(!filtersOpen)}
              aria-expanded={filtersOpen} aria-controls="market-filters"
              sx={{ ...marketButton, display: { xs: 'inline-flex', md: 'none' }, minHeight: 44 }}>
              商品分類{category ? ` · ${category}` : ''}
            </Button>
            <Box component="nav" id="market-filters" aria-label="商品分類"
              sx={{ display: { xs: filtersOpen ? 'block' : 'none', md: 'block' }, borderTop: `1px solid ${marketLine}` }}>
              <Typography component="h2" variant="subtitle2" sx={{ py: 1.75, px: 1.5, fontWeight: 700 }}>商品分類</Typography>
              {[[null, catalog.products.length] as const, ...categoryCounts].map(([item, count]) => (
                <Button key={item ?? 'all'} fullWidth onClick={() => chooseCategory(item)} aria-pressed={category === item}
                  sx={{ color: marketInk, borderRadius: 0, minHeight: 44, px: 1.5, justifyContent: 'space-between',
                    borderTop: `1px solid ${marketLine}`, bgcolor: category === item ? marketPaper : 'transparent',
                    fontWeight: category === item ? 700 : 500 }}>
                  {item ?? '全部商品'} <Typography component="span" variant="caption">{count}</Typography>
                </Button>
              ))}
            </Box>
            <Stack component="section" aria-labelledby="market-products-heading" spacing={2.25}>
              <Stack direction={{ xs: 'column', sm: 'row' }} sx={{ gap: 1.5, alignItems: { sm: 'center' },
                justifyContent: 'space-between', pb: 1.5, borderBottom: `1px solid ${marketLine}` }}>
                <Box>
                  <Typography id="market-products-heading" component="h2" variant="h5">{category ?? '食材目錄'}</Typography>
                  <Typography variant="caption" aria-live="polite">{products.length} 項商品 · 圖片為類別示意</Typography>
                </Box>
                <TextField fullWidth size="small" label="搜尋食材" type="search" value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment> } }}
                  sx={{ maxWidth: { sm: 290 }, '& .MuiOutlinedInput-root': { bgcolor: '#fff', borderRadius: 0 } }} />
              </Stack>
              {products.length > 0 ? (
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: 'repeat(3, minmax(0, 1fr))' }, gap: { xs: 2, md: 3 } }}>
                  {products.map((product) => (
                    <ProductCard key={product.food_id} product={product} quantity={cart.quantities[product.food_id] ?? 0}
                      disabled={Boolean(releaseIssue || cart.orderId)} onAdd={() => change(product.food_id, 1)} />
                  ))}
                </Box>
              ) : catalog.products.length > 0 && (
                <Paper square variant="outlined" sx={{ p: 3, textAlign: 'center', bgcolor: '#fff', borderColor: marketLine }}>
                  <Typography color="text.secondary">找不到符合條件的商品。</Typography>
                  <Button onClick={() => { setSearch(''); chooseCategory(null) }}>清除篩選</Button>
                </Paper>
              )}
            </Stack>
            <MobileCart checkoutPath={checkoutPath} count={count} totalCents={totalCents} />
          </Box>
        ) : (
          <>
            {plan && (
              <Alert severity="info">
                來自已採用菜單 · {plan.meal_date}{plan.dishes.length > 0 && ` · ${plan.dishes.map((dish) => dish.dish_name).join('、')}`}
              </Alert>
            )}
            {cart.orderId && !releaseIssue && (
              <Alert icon={<CheckCircleOutlineOutlined />} severity="success">模擬訂單編號 {cart.orderId}</Alert>
            )}
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: 'minmax(0, 1fr) 300px' }, gap: 3 }}>
              <Paper square variant="outlined" sx={{ p: { xs: 2, sm: 3 }, bgcolor: '#fff', borderColor: marketLine }}>
                <Typography component="h2" variant="h6" sx={{ mb: 2, color: marketInk }}>購物車品項</Typography>
                {lines.length === 0 ? (
                  <Stack spacing={1} sx={{ alignItems: 'center', py: 3, color: 'text.secondary', textAlign: 'center' }}>
                    <ShoppingBasketOutlined sx={{ fontSize: 36, color: marketInk }} />
                    <Typography>目前沒有可結帳的商品。</Typography>
                  </Stack>
                ) : (
                  <Stack component="ul" spacing={1.5} divider={<Divider flexItem />} sx={{ listStyle: 'none', p: 0, m: 0 }}>
                    {lines.map((line) => (
                      <Stack key={line.food_id} component="li" direction={{ xs: 'column', sm: 'row' }} spacing={1}
                        sx={{ justifyContent: 'space-between', alignItems: { xs: 'start', sm: 'center' } }}>
                        <Box>
                          <Typography sx={{ fontWeight: 700, overflowWrap: 'anywhere' }}>{line.name}</Typography>
                          <Typography variant="body2" color="text.secondary">
                            {shortages.has(line.food_id) && `菜單缺量 ${shortages.get(line.food_id)} g · `}
                            {line.package_grams} g／包 · 購買 {line.quantity * line.package_grams} g
                          </Typography>
                          <Typography variant="caption" color="text.secondary">模擬單價 {money(line.price_cents)}／包</Typography>
                        </Box>
                        <Stack direction="row" sx={{ alignItems: 'center', gap: 0.5, flexWrap: 'wrap', fontVariantNumeric: 'tabular-nums' }}>
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
              <Paper component="aside" square variant="outlined" sx={{ p: { xs: 2, sm: 3 }, alignSelf: 'start', bgcolor: marketPaper, borderColor: marketLine }}>
                <Stack spacing={{ xs: 1.5, sm: 2 }}>
                  <Typography component="h2" variant="h6" sx={{ color: marketInk }}>金額摘要</Typography>
                  <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 1 }} aria-live="polite">
                    <Typography variant="body2">模擬商品總額 · {count} 包</Typography>
                    <Typography sx={{ color: marketInk, fontWeight: 700, fontSize: '1.3rem' }}>{money(totalCents)}</Typography>
                  </Stack>
                  <Typography variant="caption" color="text.secondary">此頁僅供模擬，不會付款或配送。</Typography>
                  {!cart.orderId && <Button variant="contained" size="large" sx={marketButton} onClick={confirmOrder} disabled={count === 0}>確認模擬訂單</Button>}
                  {cart.orderId && !plan && <Button onClick={() => setCart({ quantities: {}, orderId: null })}>開始新購物車</Button>}
                </Stack>
              </Paper>
            </Box>
            {unavailable.length > 0 && (
              <Paper square variant="outlined" sx={{ p: { xs: 2, sm: 3 }, bgcolor: '#fff', borderColor: marketLine }}>
                <Typography component="h2" variant="h6">未加入的食材</Typography>
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
    </Box>
  )
}

function MarketMasthead({ page, catalogPath, checkoutPath, count, category, categories, onCategory }: {
  page: Page; catalogPath: string; checkoutPath: string; count: number;
  category: string | null; categories: string[]; onCategory: (value: string | null) => void;
}) {
  return (
    <Box component="header" sx={{ bgcolor: '#fff', borderBottom: `2px solid ${marketInk}` }}>
      <Typography component="p" variant="caption" sx={{ py: 0.75, px: 2, bgcolor: marketInk, color: '#fff', textAlign: 'center' }}>
        食譜食材選購 · 模擬超市
      </Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 0.5,
        px: contentPadding, minHeight: { xs: page === 'catalog' ? 64 : 52, md: 88 } }}>
        <Typography variant="caption" sx={{ display: { xs: 'none', sm: 'block' } }}>RECIPE MARKET</Typography>
        <Typography component={page === 'catalog' ? 'h1' : 'div'} sx={{ gridColumn: 2, fontFamily: brandFont,
          fontSize: 'clamp(1.2rem, 4.5vw, 2.35rem)', lineHeight: 1.1, letterSpacing: '-0.035em', textAlign: 'center' }}>
          <Box component={Link} to={catalogPath} sx={{ color: 'inherit', textDecoration: 'none' }}>Sim’s Supermarket</Box>
        </Typography>
        <IconButton component={Link} to={checkoutPath} aria-label={`查看購物車，共 ${count} 包`}
          sx={{ color: marketInk, justifySelf: 'end' }}>
          <Badge badgeContent={count} sx={{ '& .MuiBadge-badge': { bgcolor: marketInk, color: '#fff' } }}>
            <ShoppingBagOutlined />
          </Badge>
        </IconButton>
      </Box>
      {page === 'catalog' && (
        <Stack component="nav" aria-label="快速選購" direction="row"
          sx={{ display: { xs: 'none', md: 'flex' }, justifyContent: 'center', gap: { md: 1, lg: 2 }, px: 2 }}>
          {[null, ...categories].map((item) => (
            <Button key={item ?? 'all'} href="#market-products" onClick={() => onCategory(item)}
              aria-current={category === item ? 'true' : undefined}
              sx={{ borderRadius: 0, color: marketInk, borderBottom: `2px solid ${category === item ? marketInk : 'transparent'}` }}>
              {item ?? '全部商品'}
            </Button>
          ))}
        </Stack>
      )}
    </Box>
  )
}

function MarketHero() {
  return (
    <Box component="section" aria-labelledby="market-hero" sx={{ display: 'grid',
      gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 1.55fr) minmax(0, 1fr)' }, bgcolor: marketPaper }}>
      <Box component="img" src={heroImage} alt="" width={1536} height={1024}
        sx={{ width: '100%', height: '100%', maxHeight: { xs: 230, sm: 385 }, objectFit: 'cover', aspectRatio: { xs: '16 / 9', sm: '2 / 1' } }} />
      <Stack sx={{ justifyContent: 'center', alignItems: { xs: 'start', sm: 'center' }, textAlign: { sm: 'center' },
        px: contentPadding, py: { xs: 3, sm: 4 } }}>
        <Typography variant="overline" sx={{ color: marketInk }}>THE DAILY PANTRY</Typography>
        <Typography id="market-hero" component="h2" variant="h4" sx={{ mt: 0.5, fontSize: { xs: '1.6rem', md: '2.3rem' } }}>
          從這一餐開始
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.25 }}>從食譜挑食材，為下一餐備好剛剛好的份量。</Typography>
        <Button href="#market-products" variant="outlined" endIcon={<ArrowForwardOutlined />}
          sx={{ ...marketOutline, mt: 2 }}>瀏覽食材</Button>
      </Stack>
    </Box>
  )
}

function MobileCart({ checkoutPath, count, totalCents }: {
  checkoutPath: string; count: number; totalCents: number
}) {
  return (
    <Paper component="aside" aria-label="購物車摘要" square variant="outlined" sx={{
      display: { xs: 'block', md: 'none' }, position: 'fixed', left: 12, right: 12,
      bottom: 'calc(64px + env(safe-area-inset-bottom))', zIndex: 2, p: 1.25, bgcolor: '#fff',
      borderColor: marketLine, boxShadow: '0 4px 16px #00000014',
    }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', gap: 1 }}>
        <Stack aria-live="polite">
          <Typography variant="caption">{count} 包 · 模擬總額</Typography>
          <Typography sx={{ color: marketInk, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{money(totalCents)}</Typography>
        </Stack>
        <Button variant="contained" component={Link} to={checkoutPath} endIcon={<ArrowForwardOutlined />}
          sx={{ ...marketButton, flexShrink: 0, minHeight: 44 }}>查看購物車</Button>
      </Stack>
    </Paper>
  )
}

function ProductCard({ product, quantity, disabled, onAdd }: {
  product: MarketProduct; quantity: number; disabled: boolean; onAdd: () => void
}) {
  const imagePosition = categoryImagePositions.get(product.category)
  return (
    <Stack component="article" sx={{ minWidth: 0, pb: 2, borderBottom: `1px solid ${marketLine}` }}>
      <Box aria-hidden="true" sx={{ display: 'grid', placeItems: 'center', aspectRatio: '1',
        bgcolor: '#fff', color: '#77746D',
        ...(imagePosition && { backgroundImage: `url(${categoryImage})`, backgroundSize: '200% 200%', backgroundPosition: imagePosition }) }}>
        {!imagePosition && <Inventory2Outlined sx={{ fontSize: 46 }} />}
      </Box>
      <Stack sx={{ gap: 0.5, flex: 1 }}>
        <Typography variant="caption">{product.category} · {product.package_grams} g／包</Typography>
        <Typography component="h3" variant="body1" sx={{ fontWeight: 700, overflowWrap: 'anywhere', lineHeight: 1.45 }}>{product.name}</Typography>
        <Typography variant="body2" sx={{ mt: 'auto', pt: 0.5, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
          {money(product.price_cents)}
          {quantity > 0 && <Typography component="span" variant="caption" sx={{ ml: 1 }}>已選 {quantity} 包</Typography>}
        </Typography>
        <Button variant="outlined" startIcon={<AddOutlined />} aria-label={`加入${product.name}`}
          disabled={disabled || quantity >= MAX_PACKS} onClick={onAdd}
          sx={{ ...marketOutline, minHeight: 44, px: 1, mt: 0.75 }}>加入購物車</Button>
      </Stack>
    </Stack>
  )
}
