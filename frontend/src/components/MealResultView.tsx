import CheckCircleOutlineOutlined from '@mui/icons-material/CheckCircleOutlineOutlined'
import {
  Alert, Box, Button, Chip, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { amount } from '../api/nutrition'
import PersonNutrition, { MacroBar } from './PersonNutrition'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Link as RouterLink } from 'react-router'
import { api, errorMessage } from '../api/client'
import type {
  DecisionKind, MealPlanSummary, MealResult, MealResultSummary, NutritionSuggestion,
} from '../api/types'
import { DishIngredients, DishSteps, HighSodiumTag } from './DishDetail'
import { Section, Stat, StatRow } from './ui'
import { tokens } from '../theme'

type Props = { requestId: string; mealDate?: string | null; onChanged: () => void }

/** 顯示用取整：金額到 1 元、克到 1 g、營養到 0.1；計算與判定一律由後端以精確值完成。 */
const money = (v: string | null) => (v === null ? '未知' : `${Math.round(Number(v))} 元`)
const grams = (v: string | null) => (v === null ? '無法換算' : `${Math.round(Number(v))} g`)
const NUTRIENT_LABELS: Record<string, string> = { energy_kcal: '熱量', protein_g: '蛋白質' }
const NUTRIENT_UNITS: Record<string, string> = { energy_kcal: 'kcal', protein_g: 'g' }
/** 調整建議的差額與可調範圍由後端以每 1 份（1n）基準計算；乘上份數 N 換成整餐值，與整餐營養表一致（決策 #53）。 */
const scale = (v: string | null | undefined, n: number) =>
  v === null || v === undefined ? v : String(Number(v) * n)

/** 主食克數未知的原因（unresolved_reason）→ 給使用者看的說明；不是份量問題，而是主食沒有營養資料或不在清單。 */
const STAPLE_NO_DATA = new Set(['no_tfda_cooked_sample', 'staple_without_tfda_sample'])
function stapleProblem(label: string, reason: string | null | undefined): string {
  if (reason === 'staple_not_supported') return `主食「${label}」不在可選清單中，整餐營養無法計算；請改選清單中的主食後重新生成`
  if (reason && STAPLE_NO_DATA.has(reason)) return `主食「${label}」目前沒有營養資料，整餐營養無法計算；請改選其他主食後重新生成`
  return `主食「${label}」無法換算克數，整餐營養無法計算`
}
/** 查價失敗原因 → 使用者看得懂的說明；原始原因放在滑鼠提示，方便追查。 */
const MISSING_PRICE_REASONS: Record<string, string> = {
  NO_PRICE_DATA: '尚無價格資料', UNIT_MISMATCH: '包裝單位無法換算成克', PRICE_EXPIRED: '價格已過期',
  INVALID_PACKAGE: '包裝資訊不完整',
}
const missingReason = (code: string | null) => (code && MISSING_PRICE_REASONS[code]) ?? '賣場找不到相符的商品'
const shortTime = (iso: string) =>
  new Date(iso).toLocaleString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })

const EXCLUDED_FROM: Record<string, string> = { previous_day: '昨天', breakfast: '今天早餐', lunch: '今天午餐', dinner: '今天晚餐' }

const STAPLE_REASONS = new Set([...STAPLE_NO_DATA, 'staple_not_supported'])
const ACTIVE = new Set(['queued', 'running'])

const DECISION_LABELS: Record<DecisionKind, string> = {
  accept_missing_price: '接受缺價例外並繼續',
  continue_missing_nutrition: '繼續查看（仍不可採用）',
}

/** T15 生成結果：全家配方、缺料雙價格、數值營養、知識說明、昨日比對與後端判定的採用資格。 */
export default function MealResultView({ requestId, mealDate, onChanged }: Props) {
  const queryClient = useQueryClient()
  // 每個（結果版本、命令）固定一把冪等鍵：重複點擊或網路重送只產生一筆決定
  const keys = useRef(new Map<string, string>())
  const keyFor = (name: string) => {
    if (!keys.current.has(name)) keys.current.set(name, crypto.randomUUID())
    return keys.current.get(name)!
  }

  const list = useQuery({
    queryKey: ['meal-results', requestId],
    queryFn: async () =>
      (await api.get<{ results: MealResultSummary[] }>(`/meal-requests/${requestId}/results`)).data.results,
  })
  const latestVersion = list.data?.[0]?.result_version
  const result = useQuery({
    queryKey: ['meal-result', requestId, latestVersion],
    enabled: latestVersion !== undefined,
    queryFn: async () =>
      (await api.get<MealResult>(`/meal-requests/${requestId}/results/${latestVersion}`)).data,
    // 重試工作（查價中／說明產生中）進行時定期更新，完成後會出現新版本
    refetchInterval: (query) => {
      const retries = query.state.data?.retries
      return retries && [retries.price, retries.knowledge].some((s) => s && ACTIVE.has(s.status)) ? 2000 : false
    },
  })
  const retrying = result.data?.retries
  useEffect(() => {
    // 重試完成後重新讀取版本清單，畫面切到新版本
    if ([retrying?.price, retrying?.knowledge].some((s) => s?.status === 'succeeded' && s.produced_result_version
        && s.produced_result_version !== latestVersion)) {
      void queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
    }
  }, [retrying, latestVersion, queryClient, requestId])

  const refreshAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
    await queryClient.invalidateQueries({ queryKey: ['meal-result', requestId] })
    onChanged()
  }

  const decide = useMutation({
    mutationFn: async ({ r, kind }: { r: MealResult; kind: DecisionKind }) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/decisions`,
        { kind, request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:${kind}`) } }),
    onSuccess: refreshAll,
  })
  const retry = useMutation({
    mutationFn: async (r: MealResult) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/price-retry`,
        { request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:retry`) } }),
    onSuccess: refreshAll,
  })
  const retryKnowledge = useMutation({
    mutationFn: async (r: MealResult) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/knowledge-retry`,
        { request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:knowledge-retry`) } }),
    onSuccess: refreshAll,
  })
  // T16 採用：前端只負責送出指定版本；資格、版本、缺價接受與昨日歷史一律由後端在交易內重驗
  const adopt = useMutation({
    mutationFn: async (r: MealResult) => {
      const accepted = r.eligibility.price_exception_accepted
        ? r.eligibility.decisions.find((d) => d.kind === 'accept_missing_price')?.id
        : undefined
      return (await api.post<{ meal_plan: MealPlanSummary }>('/meal-plans', {
        meal_request_id: requestId, result_version: r.result_version, request_version: r.request_version,
        ...(accepted ? { price_exception_decision_id: accepted } : {}),
      }, { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:adopt`) } })).data.meal_plan
    },
    onSuccess: refreshAll,
  })

  if (list.isError || result.isError) return <Alert severity="error">{errorMessage(list.error ?? result.error)}</Alert>
  if (list.data && list.data.length === 0) return null
  const r = result.data
  if (!r) return <Typography color="text.secondary">讀取結果中…</Typography>
  const el = r.eligibility
  // 缺價不擋採用（2026-10-26）：只提示哪些食材查無價格，可重新查價
  const pricePending = r.prices.budget_status === 'PRICE_INCOMPLETE'
  const missingPriceNames = r.prices.items.filter((i) => i.price_status === 'unknown').map((i) => i.name)
  const confirmed = r.request_status === 'confirmed'
  const priceRetry = r.retries?.price ?? null
  const knowledgeRetry = r.retries?.knowledge ?? null
  const priceRetrying = !!priceRetry && ACTIVE.has(priceRetry.status)
  const knowledgeRetrying = !!knowledgeRetry && ACTIVE.has(knowledgeRetry.status)
  const dayConflict = r.previous_day_check?.status === 'conflict'
  const suggestions: NutritionSuggestion[] = r.nutrition.suggestions ?? []
  const totalN = Number(r.nutrition.total_n ?? 1) || 1
  // decisions #42：推估／對照／近似等來源只留在追溯資料（payload、trace），畫面不標示
  const num = (v: string | null | undefined) => (v === null || v === undefined ? null : Number(v))
  const energy = num(r.nutrition.energy_kcal)
  const energyTarget = num(r.nutrition.target?.energy_kcal)
  const protein = num(r.nutrition.protein_g)
  const proteinTarget = num(r.nutrition.target?.protein_g)
  const ratioNote = (v: number | null, target: number | null, unit: string) => {
    if (v === null || !target) return { note: target ? `目標 ${Math.round(target)} ${unit}` : undefined, tone: 'default' as const }
    const diff = v / target - 1
    return { note: `目標 ${Math.round(target)} ${unit}・${diff >= 0 ? '+' : ''}${Math.round(diff * 100)}%`,
      tone: Math.abs(diff) <= 0.1 ? 'good' as const : 'warn' as const }
  }
  const energyInfo = ratioNote(energy, energyTarget, 'kcal')
  const proteinInfo = ratioNote(protein, proteinTarget, 'g')
  const computed = r.prices.status === 'computed'
  const cost = computed ? num(r.prices.cost_total) : null
  const budget = num(r.prices.budget)
  const highSodium = r.dishes.filter((d) => d.high_sodium)
  const sodiumThreshold = Number(r.nutrition.daily_reference?.high_sodium_dish_mg ?? 1000)

  return (
    <Stack spacing={3}>
      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h5" component="h2" sx={{ flexGrow: 1 }}>這一餐的菜單</Typography>
        <Chip size="small" color={r.mode === 'ai_generated_menu' ? 'secondary' : 'default'}
          variant={r.mode === 'ai_generated_menu' ? 'outlined' : 'filled'} label={r.mode_label} />
        <Chip size="small" label={`第 ${r.result_version} 版`} />
        {r.derived_from_result_version !== null && (
          <Chip size="small" variant="outlined" label={`由第 ${r.derived_from_result_version} 版${
            r.knowledge_retry ? '重試知識說明' : '重新查價'}`} />
        )}
      </Box>
      {r.fallback_message && <Alert severity="info">{r.fallback_message}</Alert>}

      <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, bgcolor: '#FFFDF9', p: { xs: 2, sm: 3 } }}>
        <StatRow>
          <Stat label="整餐熱量" value={energy === null ? '未知' : Math.round(energy)} unit={energy === null ? undefined : 'kcal'}
            note={energyInfo.note} tone={energyInfo.tone} />
          <Stat label="整餐蛋白質" value={protein === null ? '未知' : protein.toFixed(1)} unit={protein === null ? undefined : 'g'}
            note={proteinInfo.note} tone={proteinInfo.tone} />
          <Stat label="成本價" value={cost === null ? '未知' : Math.round(cost)} unit={cost === null ? undefined : '元'}
            note={[cost === null && computed ? `已知 ${money(r.prices.known_cost_total)}` : null,
              budget === null ? null : `預算 ${Math.round(budget)} 元`].filter(Boolean).join('・') || undefined}
            tone={cost === null || budget === null ? 'default' : cost <= budget ? 'good' : 'bad'} />
          <Stat label="菜數" value={r.dishes.length} unit="道" note={r.staple ? `主食：${r.staple.label}` : undefined} />
        </StatRow>
        <Box sx={{ mt: 3 }}><MacroBar nutrition={r.nutrition} /></Box>
        <Box sx={{ mt: 3, pt: 2.5, borderTop: `1px solid ${tokens.line}`, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          {confirmed ? (
            <>
              <CheckCircleOutlineOutlined sx={{ color: '#5E7B5A' }} />
              <Typography variant="body2" sx={{ flexGrow: 1 }}>
                已採用並保存為已採用餐食紀錄（不代表實際已食用）。
              </Typography>
              <Button variant="outlined" component={RouterLink} to={mealDate ? `/history?date=${mealDate}` : '/history'}>
                查看紀錄
              </Button>
            </>
          ) : (
            <>
              <Typography variant="body2" sx={{ flexGrow: 1, color: el.adoptable ? '#3E5539' : tokens.inkSoft }}>
                {el.adoptable
                  ? `符合採用條件${el.price_exception_accepted ? '（部分價格未知，成本只計已知部分）' : ''}。`
                  : '目前還不能採用，原因見下方。'}
              </Typography>
              {el.adoptable && (
                <Button variant="contained" size="large" disabled={adopt.isPending || dayConflict}
                  onClick={() => adopt.mutate(r)}>
                  {adopt.isPending ? '採用中…' : '採用這份菜單'}
                </Button>
              )}
            </>
          )}
        </Box>
      </Box>

      {highSodium.length > 0 && (
        <Alert severity="warning">
          <b>{highSodium.map((d) => d.dish_name).join('、')}</b> 鈉含量偏高：每人份就超過
          {' '}{sodiumThreshold} mg，約等於一整餐的建議鈉量。
          {!confirmed && '這只是提醒，仍可照常採用；如果想換菜，可以回到問卷調整（例如指定其他菜色）後重新生成。'}
        </Alert>
      )}

      {/* 採用資格：由後端判定；result_ready 不代表可採用 */}
      {!el.adoptable && !confirmed && el.blocks.length > 0 && (
        <Alert severity="warning">
          <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
            {el.blocks.map((b) => <li key={b.code}>{b.message}</li>)}
          </Box>
        </Alert>
      )}
      {el.pending_decisions.length > 0 && (
        <Alert severity="info">
          <Stack spacing={1}>
            {el.pending_decisions.map((d) => (
              <Box key={d.kind}>
                <Typography variant="body2">{d.message}</Typography>
                <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap' }}>
                  {d.kind === 'accept_missing_price' && (
                    <Button size="small" variant="outlined" disabled={retry.isPending || priceRetrying}
                      onClick={() => retry.mutate(r)}>
                      {priceRetrying ? '查價中…' : '重新查價'}
                    </Button>
                  )}
                  <Button size="small" variant="contained" disabled={decide.isPending}
                    onClick={() => decide.mutate({ r, kind: d.kind })}>
                    {DECISION_LABELS[d.kind]}
                  </Button>
                </Box>
              </Box>
            ))}
          </Stack>
        </Alert>
      )}
      {pricePending && !confirmed && !priceRetrying && (
        <Alert severity="info" action={
          <Button size="small" color="inherit" disabled={retry.isPending} onClick={() => retry.mutate(r)}>重新查價</Button>
        }>
          {missingPriceNames.join('、')} 查無價格，成本只計已知部分；不影響採用，也可以重新查價。
        </Alert>
      )}
      {priceRetrying && <Alert severity="info">正在向價格來源查詢缺價食材（低頻率查詢，可能需要幾分鐘）；完成後會出現新版本。</Alert>}
      {priceRetry?.status === 'failed' && (
        <Alert severity="warning">上次重新查價沒有完成：{priceRetry.error?.message ?? '原因未知'}。原結果不變，可以直接採用或稍後再試。</Alert>
      )}
      {(decide.isError || retry.isError) && <Alert severity="error">{errorMessage(decide.error ?? retry.error)}</Alert>}
      {dayConflict && (
        <Alert severity="error">
          {r.previous_day_check?.message}（重複：{r.previous_day_check?.conflicting_dishes?.join('、')}）
        </Alert>
      )}
      {adopt.isError && <Alert severity="error">{errorMessage(adopt.error)}</Alert>}

      <Section title="全家配方" aside="整餐用量">
        <Stack spacing={3} divider={<Box sx={{ borderTop: `1px solid ${tokens.line}` }} />}>
          {r.dishes.map((d, idx) => (
            <Box key={d.standard_dish_id} sx={{ display: 'flex', gap: 2 }}>
              <Typography sx={{ fontFamily: tokens.serif, color: tokens.brick, fontSize: '0.95rem', pt: 0.4, width: 22, flexShrink: 0 }}>
                {String(idx + 1).padStart(2, '0')}
              </Typography>
              <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
                  <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>{d.dish_name}</Typography>
                  {d.cooking_method && <Typography variant="caption">{d.cooking_method}</Typography>}
                  {d.specified && <Chip size="small" variant="outlined" color="primary" label="指定菜" />}
                  <HighSodiumTag high={d.high_sodium} sodium={d.sodium_mg_per_serving} />
                </Box>
                <DishIngredients ingredients={d.ingredients} />
                <DishSteps steps={d.steps} display={d.steps_display} />
              </Box>
            </Box>
          ))}
          {r.staple && (
            <Box sx={{ display: 'flex', gap: 2 }}>
              <Typography sx={{ fontFamily: tokens.serif, color: tokens.inkMuted, fontSize: '0.95rem', pt: 0.4, width: 22, flexShrink: 0 }}>主</Typography>
              <Box>
                <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>{r.staple.label}</Typography>
                <Typography variant="body2" color={r.staple.display_amount != null ? 'text.primary' : 'error'}>
                  {r.staple.display_amount != null
                    ? `${r.staple.tfda_name ?? r.staple.label} ${Number(r.staple.display_amount)} ${r.staple.display_unit ?? ''}`
                    : `${stapleProblem(r.staple.label, r.staple.unresolved_reason)}，不可採用`}
                </Typography>
                <Typography variant="caption">
                  計入營養與 ±10% 驗證，不計入價格{r.staple.review_status === 'pending_review' ? '；份量設定待營養審核' : ''}
                </Typography>
              </Box>
            </Box>
          )}
        </Stack>
      </Section>

      <Section title="每人營養" aside="這一餐吃到的量／每日建議攝取量">
        <Stack spacing={2}>
          <PersonNutrition nutrition={r.nutrition} />
          {r.nutrition.quantities_verified === false && (
            <Alert severity="warning">以上用量尚未通過營養驗證，僅供查看與估算成本，不可採用。</Alert>
          )}
          {suggestions.length > 0 && (
            <Alert severity="info">
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                調整建議（整餐，共 {totalN} 份；數值已由每份換算為整餐）
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {suggestions.map((s, idx) => (
                  <li key={idx}>
                    <Typography variant="body2">
                      {s.code === 'NUTRITION_OUT_OF_RANGE' && s.nutrient
                        ? `${NUTRIENT_LABELS[s.nutrient]}需${s.direction === 'increase' ? '增加' : '減少'}約 ${amount(scale(s.needed_change, totalN), NUTRIENT_UNITS[s.nutrient])}；`
                          + (s.ingredients.length
                            ? s.ingredients.map((i) => `${i.name}${i.direction === 'increase' ? '增加' : '減少'}`
                              + (i.adjustment_approved
                                ? `（可調 ${grams(scale(i.adjustable_min, totalN) ?? null)}–${grams(scale(i.adjustable_max, totalN) ?? null)}，最多${amount(scale(i.max_change, totalN), NUTRIENT_UNITS[s.nutrient!])}）`
                                : '（未審核可調範圍）')).join('、')
                            : '沒有可調整的食材')
                          + (s.within_adjustable_ranges ? '。已審核範圍內補得足，但與另一項營養無法同時達成。' : '。已審核範圍內補不足。')
                        : [
                            ...s.ingredients.filter((i) => i.reason && STAPLE_REASONS.has(i.reason))
                              .map((i) => stapleProblem(i.name, i.reason)),
                            ...(s.ingredients.some((i) => !(i.reason && STAPLE_REASONS.has(i.reason)))
                              ? [`${s.action ?? ''}：${s.ingredients.filter((i) => !(i.reason && STAPLE_REASONS.has(i.reason))).map((i) => i.name).join('、')}`]
                              : []),
                          ].join('；')}
                    </Typography>
                  </li>
                ))}
              </Box>
            </Alert>
          )}
        </Stack>
      </Section>

      <Section title="需補買食材" aside={computed && r.prices.items.length > 0 ? '調味料與家中已有的不列入' : undefined}>
        {!computed ? (
          <Typography variant="body2" color="text.secondary">營養計算未完成，無法計算缺料價格。</Typography>
        ) : r.prices.items.length === 0 ? (
          <Typography variant="body2" color="text.secondary">食材都已在家中，不需補買（成本 0 元）。</Typography>
        ) : (
          <Box sx={{ overflowX: 'auto', mx: { xs: -2, sm: 0 } }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>食材</TableCell><TableCell align="right">缺量</TableCell><TableCell>包裝</TableCell>
                  <TableCell align="right">採購價</TableCell><TableCell align="right">成本價</TableCell><TableCell>來源</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {r.prices.items.map((i) => (
                  <TableRow key={i.food_id}>
                    <TableCell sx={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{i.name}</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{grams(i.shortage_grams)}</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{i.price_status === 'known' ? `${i.package_description ?? ''} × ${i.packs}` : '—'}</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{money(i.purchase_price)}</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{money(i.cost)}</TableCell>
                    <TableCell sx={{ minWidth: 140 }}>
                      {i.price_status === 'unknown'
                        ? <Typography variant="caption" color="error" title={i.missing_reason ?? undefined}>查無價格・{missingReason(i.missing_reason)}</Typography>
                        : <>
                            {i.product_url ? <Link href={i.product_url} target="_blank" rel="noreferrer">{i.source_name ?? i.retailer ?? i.source}</Link> : (i.source_name ?? i.retailer ?? i.source)}
                            {i.queried_at && <Typography variant="caption" component="p">{shortTime(i.queried_at)} 查詢</Typography>}
                          </>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
        {computed && (
          <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', justifyContent: 'flex-end', mt: 2 }}>
            <Typography variant="body2">
              採購價 <b>{r.prices.purchase_total !== null ? money(r.prices.purchase_total) : `未知（已知 ${money(r.prices.known_purchase_total)}）`}</b>
            </Typography>
            <Typography variant="body2">
              成本價 <b>{r.prices.cost_total !== null ? money(r.prices.cost_total) : `未知（已知 ${money(r.prices.known_cost_total)}）`}</b>
            </Typography>
            <Typography variant="body2" color="text.secondary">預算 {money(r.prices.budget)}</Typography>
          </Box>
        )}
        {computed && pricePending && (
          <Typography variant="caption" component="p" sx={{ textAlign: 'right', mt: 0.5 }}>部分價格未知，成本只計已知部分</Typography>
        )}
      </Section>

      <Section title="避免重複的菜">
        <Stack spacing={1.5}>
          {!r.previous_day.has_records ? (
            <Typography variant="body2">昨天（{r.previous_day.previous_date}）與今天其他餐都沒有已採用紀錄，未排除任何菜。</Typography>
          ) : (
            <Typography variant="body2">
              已比對昨天（{r.previous_day.previous_date}）與今天其他餐的已採用紀錄；
              {r.previous_day.excluded_dishes.length > 0
                ? `已排除：${r.previous_day.excluded_dishes.map((d) => `${d.dish_name}（${EXCLUDED_FROM[d.from ?? 'previous_day'] ?? '昨天'}）`).join('、')}。`
                : '沒有需要排除的菜。'}
            </Typography>
          )}
          {r.previous_day.specified_repeats.length > 0 && (
            <Alert severity="info">
              {r.previous_day.specified_repeats.join('、')} 與昨天或今天其他餐重複，因為是你指定的菜所以保留；其他限制（過敏、營養等）仍照常檢查。
            </Alert>
          )}
        </Stack>
      </Section>

      {r.knowledge.status === 'unavailable' && (
        <Alert severity="warning" action={r.knowledge.retry_available && !confirmed ? (
          <Button size="small" color="inherit" disabled={retryKnowledge.isPending || knowledgeRetrying}
            onClick={() => retryKnowledge.mutate(r)}>
            {knowledgeRetrying ? '重試中…' : '只重試知識說明'}
          </Button>) : undefined}>
          {r.knowledge.reason === 'BUDGET_EXHAUSTED'
            ? '本月外部服務額度不足，知識說明暫時無法產生；'
            : '營養知識服務暫時無法使用；'}
          菜單、份量與採用資格不受影響。可「只重試知識說明」（不會重新生成菜單，結果存成新版本）。
          {knowledgeRetry?.status === 'failed' && ` 上次重試失敗：${knowledgeRetry.error?.message ?? '原因未知'}`}
        </Alert>
      )}
      {retryKnowledge.isError && <Alert severity="error">{errorMessage(retryKnowledge.error)}</Alert>}
      {r.knowledge.status === 'available' && (
        <Section title="食材小知識" aside="資料來源：健康醫療網">
          <Stack spacing={2.5}>
            {r.knowledge.items.map((k) => (
              <Box key={k.ingredient_id} sx={{ pl: 2, borderLeft: `2px solid ${tokens.sand}` }}>
                <Typography sx={{ fontWeight: 500, letterSpacing: '0.08em', mb: 0.5 }}>{k.ingredient_name ?? k.ingredient_id}</Typography>
                <Typography variant="body2">{k.explanation}</Typography>
                <Typography variant="caption" component="p" sx={{ mt: 0.5 }}>
                  {k.citations.map((c, idx) => (
                    <span key={c.knowledge_id}>
                      {idx > 0 && '、'}
                      {c.attribution ? (
                        <>
                          {c.attribution}{' '}
                          {c.source_url && <Link href={c.source_url} target="_blank" rel="noreferrer">原文</Link>}
                        </>
                      ) : (
                        <>
                          來源：
                          {c.source_url ? <Link href={c.source_url} target="_blank" rel="noreferrer">{c.knowledge_id}</Link> : c.knowledge_id}
                          （{c.source_version}）
                        </>
                      )}
                    </span>
                  ))}
                </Typography>
              </Box>
            ))}
          </Stack>
        </Section>
      )}
    </Stack>
  )
}
