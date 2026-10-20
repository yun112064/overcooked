import {
  Alert, Box, Button, Chip, Divider, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Link as RouterLink } from 'react-router'
import { api, errorMessage } from '../api/client'
import type {
  DecisionKind, MealPlanSummary, MealResult, MealResultSummary, NutritionSuggestion, ResultIngredient,
} from '../api/types'

type Props = { requestId: string; mealDate?: string | null; onChanged: () => void }

/** 顯示用取整：金額到 1 元、克到 1 g、營養到 0.1；計算與判定一律由後端以精確值完成。 */
const money = (v: string | null) => (v === null ? '未知' : `${Math.round(Number(v))} 元`)
const grams = (v: string | null) => (v === null ? '無法換算' : `${Math.round(Number(v))} g`)
const amount = (v: string | null | undefined, unit: string) =>
  v === null || v === undefined ? '未知' : `${Number(v).toFixed(1)} ${unit}`
/** 後端已取整的畫面用量（克到 1 g、容量單位到 0.5）；沒有數量時顯示原文 */
const shown = (i: ResultIngredient) =>
  i.display_amount != null && i.display_unit
    ? `${Number(i.display_amount)} ${i.display_unit}`
    : i.display_text ?? grams(i.display_grams)
/** decisions #19：LLM 推估的克數或烹調係數要標示出來 */
const estimateTag = (i: ResultIngredient) => (i.llm_estimated ? `（${i.estimate_label ?? 'LLM 推估'}）` : '')
const NUTRIENT_LABELS: Record<string, string> = { energy_kcal: '熱量', protein_g: '蛋白質' }
const NUTRIENT_UNITS: Record<string, string> = { energy_kcal: 'kcal', protein_g: 'g' }
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
  const pricePending = el.pending_decisions.some((d) => d.kind === 'accept_missing_price')
  const confirmed = r.request_status === 'confirmed'
  const priceRetry = r.retries?.price ?? null
  const knowledgeRetry = r.retries?.knowledge ?? null
  const priceRetrying = !!priceRetry && ACTIVE.has(priceRetry.status)
  const knowledgeRetrying = !!knowledgeRetry && ACTIVE.has(knowledgeRetry.status)
  const dayConflict = r.previous_day_check?.status === 'conflict'
  const suggestions: NutritionSuggestion[] = r.nutrition.suggestions ?? []

  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h6">生成結果（第 {r.result_version} 版）</Typography>
        <Chip color={r.mode === 'ai_generated_menu' ? 'secondary' : 'default'} label={r.mode_label} />
        {r.derived_from_result_version !== null && (
          <Chip size="small" variant="outlined" label={`由第 ${r.derived_from_result_version} 版${
            r.knowledge_retry ? '重試知識說明' : '重新查價'}`} />
        )}
      </Box>
      {r.fallback_message && <Alert severity="info">{r.fallback_message}</Alert>}

      {/* 採用資格：由後端判定；result_ready 不代表可採用 */}
      <Alert severity={el.adoptable ? 'success' : 'warning'}>
        {el.adoptable
          ? `符合採用條件${el.price_exception_accepted ? '（已接受缺價例外，預算無法完整驗證）' : ''}。`
          : '目前不可採用：'}
        {!el.adoptable && (
          <Box component="ul" sx={{ m: 0, pl: 3 }}>
            {el.blocks.map((b) => <li key={b.code}>{b.message}</li>)}
          </Box>
        )}
      </Alert>
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
      {priceRetrying && <Alert severity="info">正在向價格來源查詢缺價食材（低頻率查詢，可能需要幾分鐘）；完成後會出現新版本。</Alert>}
      {priceRetry?.status === 'failed' && (
        <Alert severity="warning">上次重新查價失敗：{priceRetry.error?.message ?? '原因未知'}。原結果不變，可再試或接受缺價例外。</Alert>
      )}
      {(decide.isError || retry.isError) && <Alert severity="error">{errorMessage(decide.error ?? retry.error)}</Alert>}
      {dayConflict && (
        <Alert severity="error">
          {r.previous_day_check?.message}（重複：{r.previous_day_check?.conflicting_dishes?.join('、')}）
        </Alert>
      )}
      {confirmed ? (
        <Alert severity="success">
          此問卷已採用並保存為已採用餐食紀錄（不代表實際已食用）。
          {' '}<Link component={RouterLink} to={mealDate ? `/history?date=${mealDate}` : '/history'}>查看已採用紀錄</Link>
        </Alert>
      ) : el.adoptable && (
        <Box>
          <Button variant="contained" color="primary" disabled={adopt.isPending || dayConflict}
            onClick={() => adopt.mutate(r)}>
            採用此菜單（第 {r.result_version} 版）
          </Button>
        </Box>
      )}
      {adopt.isError && <Alert severity="error">{errorMessage(adopt.error)}</Alert>}

      <Divider />
      <Typography variant="subtitle1">全家配方（整餐用量）</Typography>
      {r.dishes.map((d) => (
        <Box key={d.standard_dish_id}>
          <Typography sx={{ fontWeight: 'bold' }}>
            {d.dish_name}{d.specified && '（指定菜）'}{d.cooking_method && ` · ${d.cooking_method}`}
          </Typography>
          <Typography variant="body2">
            {d.ingredients.map((i) => `${i.name} ${shown(i)}${estimateTag(i)}`).join('、')}
          </Typography>
          <Box component="ol" sx={{ my: 0.5, pl: 3 }}>
            {d.steps.map((s, idx) => <li key={idx}><Typography variant="body2">{s}</Typography></li>)}
          </Box>
        </Box>
      ))}

      {r.staple && (
        <Box>
          <Typography sx={{ fontWeight: 'bold' }}>主食：{r.staple.label}</Typography>
          <Typography variant="body2">
            {r.staple.display_amount != null
              ? `${r.staple.tfda_name ?? r.staple.label} ${Number(r.staple.display_amount)} ${r.staple.display_unit ?? ''}`
                + (r.staple.llm_estimated ? '（LLM 推估）' : '')
              : '無法換算營養（缺 TFDA 樣品），不可採用'}
            （計入營養與 ±10% 驗證，不計入價格{r.staple.review_status === 'pending_review' ? '；份量設定待營養審核' : ''}）
          </Typography>
        </Box>
      )}

      <Divider />
      <Typography variant="subtitle1">營養估算</Typography>
      {r.nutrition.quantities_verified === false && (
        <Alert severity="warning">以上用量尚未通過營養驗證，僅供查看與估算成本，不可採用。</Alert>
      )}
      {suggestions.length > 0 && (
        <Alert severity="info">
          <Typography variant="body2" sx={{ fontWeight: 'bold' }}>調整建議</Typography>
          <Box component="ul" sx={{ m: 0, pl: 3 }}>
            {suggestions.map((s, idx) => (
              <li key={idx}>
                <Typography variant="body2">
                  {s.code === 'NUTRITION_OUT_OF_RANGE' && s.nutrient
                    ? `${NUTRIENT_LABELS[s.nutrient]}需${s.direction === 'increase' ? '增加' : '減少'}約 ${amount(s.needed_change, NUTRIENT_UNITS[s.nutrient])}；`
                      + (s.ingredients.length
                        ? s.ingredients.map((i) => `${i.name}${i.direction === 'increase' ? '增加' : '減少'}`
                          + (i.adjustment_approved
                            ? `（可調 ${grams(i.adjustable_min ?? null)}–${grams(i.adjustable_max ?? null)}，最多${amount(i.max_change, NUTRIENT_UNITS[s.nutrient!])}）`
                            : '（未審核可調範圍）')).join('、')
                        : '沒有可調整的食材')
                      + (s.within_adjustable_ranges ? '。已審核範圍內補得足，但與另一項營養無法同時達成。' : '。已審核範圍內補不足。')
                    : `${s.action ?? ''}：${s.ingredients.map((i) => i.name).join('、')}`}
                </Typography>
              </li>
            ))}
          </Box>
        </Alert>
      )}
      {r.nutrition.llm_estimates?.used && (
        <Alert severity="info" icon={false}>
          <Chip size="small" color="info" label={r.nutrition.llm_estimates.label ?? 'LLM 推估'} sx={{ mr: 1 }} />
          <Typography variant="body2" component="span">
            {(r.nutrition.llm_estimates.nutrients.filter((k) => k in NUTRIENT_LABELS)
              .map((k) => NUTRIENT_LABELS[k]).join('、') || '部分數值')}
            含 LLM 推估的換算係數（缺 TFDA 實測的產率／保留率或「匙」等單位克數；已標示的食材）。
            TFDA 實測與人工審核值優先，推估值可由管理者覆核。
          </Typography>
        </Alert>
      )}
      <Table size="small">
        <TableHead><TableRow><TableCell>項目</TableCell><TableCell>目標</TableCell><TableCell>估算</TableCell><TableCell>差距</TableCell></TableRow></TableHead>
        <TableBody>
          <TableRow>
            <TableCell>熱量</TableCell><TableCell>{amount(r.nutrition.target?.energy_kcal, 'kcal')}</TableCell>
            <TableCell>{amount(r.nutrition.energy_kcal, 'kcal')}</TableCell><TableCell>{amount(r.nutrition.gap.energy_kcal, 'kcal')}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>蛋白質</TableCell><TableCell>{amount(r.nutrition.target?.protein_g, 'g')}</TableCell>
            <TableCell>{amount(r.nutrition.protein_g, 'g')}</TableCell><TableCell>{amount(r.nutrition.gap.protein_g, 'g')}</TableCell>
          </TableRow>
        </TableBody>
      </Table>

      <Divider />
      <Typography variant="subtitle1">需補買食材</Typography>
      {r.prices.status !== 'computed' ? (
        <Typography color="text.secondary">營養計算未完成，無法計算缺料價格。</Typography>
      ) : r.prices.items.length === 0 ? (
        <Typography color="text.secondary">食材都已在家中，不需補買（成本 0 元）。</Typography>
      ) : (
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>食材</TableCell><TableCell>缺量</TableCell><TableCell>包裝</TableCell>
                <TableCell>採購價</TableCell><TableCell>成本價</TableCell><TableCell>來源／時間</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {r.prices.items.map((i) => (
                <TableRow key={i.food_id}>
                  <TableCell>{i.name}</TableCell>
                  <TableCell>{grams(i.shortage_grams)}</TableCell>
                  <TableCell>{i.price_status === 'known' ? `${i.package_description ?? ''} × ${i.packs}` : '—'}</TableCell>
                  <TableCell>{money(i.purchase_price)}</TableCell>
                  <TableCell>{money(i.cost)}</TableCell>
                  <TableCell>
                    {i.price_status === 'unknown'
                      ? `查無價格（${i.missing_reason ?? '原因未知'}）`
                      : <>
                          {i.product_url ? <Link href={i.product_url} target="_blank" rel="noreferrer">{i.source_name ?? i.retailer ?? i.source}</Link> : (i.source_name ?? i.retailer ?? i.source)}
                          {i.queried_at && ` · 查詢於 ${new Date(i.queried_at).toLocaleString()}`}
                        </>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      {r.prices.status === 'computed' && (
        <Typography variant="body2">
          合計：採購價 {r.prices.purchase_total !== null ? money(r.prices.purchase_total) : `未知（已知 ${money(r.prices.known_purchase_total)}）`}
          ；成本價 {r.prices.cost_total !== null ? money(r.prices.cost_total) : `未知（已知 ${money(r.prices.known_cost_total)}）`}
          ；預算 {money(r.prices.budget)}
          {pricePending && '（部分價格未知，預算無法完整驗證）'}
        </Typography>
      )}

      <Divider />
      <Typography variant="subtitle1">昨日菜色比對</Typography>
      {!r.previous_day.has_records ? (
        <Typography variant="body2">{r.previous_day.previous_date} 沒有已採用餐食紀錄，未排除任何菜。</Typography>
      ) : (
        <Typography variant="body2">
          已比對 {r.previous_day.previous_date} 的已採用餐食紀錄；
          {r.previous_day.excluded_dishes.length > 0
            ? `已排除：${r.previous_day.excluded_dishes.map((d) => d.dish_name).join('、')}。`
            : '沒有需要排除的菜。'}
        </Typography>
      )}
      {r.previous_day.specified_repeats.length > 0 && (
        <Alert severity="info">
          {r.previous_day.specified_repeats.join('、')} 與昨日重複，因為是你指定的菜所以保留；其他限制（過敏、營養等）仍照常檢查。
        </Alert>
      )}

      {r.knowledge.status !== 'not_found' && <Divider />}
      {r.knowledge.status === 'unavailable' && (
        <Alert severity="warning" action={r.knowledge.retry_available && !confirmed ? (
          <Button size="small" disabled={retryKnowledge.isPending || knowledgeRetrying}
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
        <Box>
          <Typography variant="subtitle1">食材營養知識</Typography>
          {r.knowledge.items.map((k) => (
            <Box key={k.ingredient_id} sx={{ mb: 1 }}>
              <Typography variant="body2"><b>{k.ingredient_name ?? k.ingredient_id}</b>：{k.explanation}</Typography>
              <Typography variant="caption" color="text.secondary">
                {k.citations.map((c, idx) => (
                  <span key={c.knowledge_id}>
                    {idx > 0 && '、'}
                    {c.attribution ? (
                      <>
                        {c.attribution}{' '}
                        {c.source_url && <Link href={c.source_url} target="_blank" rel="noreferrer">{c.source_url}</Link>}
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
        </Box>
      )}
    </Stack>
  )
}
