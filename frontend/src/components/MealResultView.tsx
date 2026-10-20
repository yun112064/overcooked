import {
  Alert, Box, Button, Chip, Divider, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { api, errorMessage } from '../api/client'
import type { DecisionKind, MealResult, MealResultSummary } from '../api/types'

type Props = { requestId: string; onChanged: () => void }

/** 顯示用取整：金額到 1 元、克到 1 g、營養到 0.1；計算與判定一律由後端以精確值完成。 */
const money = (v: string | null) => (v === null ? '未知' : `${Math.round(Number(v))} 元`)
const grams = (v: string | null) => (v === null ? '無法換算' : `${Math.round(Number(v))} g`)
const amount = (v: string | null | undefined, unit: string) =>
  v === null || v === undefined ? '未知' : `${Number(v).toFixed(1)} ${unit}`

const DECISION_LABELS: Record<DecisionKind, string> = {
  accept_missing_price: '接受缺價例外並繼續',
  continue_missing_nutrition: '繼續查看（仍不可採用）',
}

/** T15 生成結果：全家配方、缺料雙價格、數值營養、知識說明、昨日比對與後端判定的採用資格。 */
export default function MealResultView({ requestId, onChanged }: Props) {
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
  })

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

  if (list.isError || result.isError) return <Alert severity="error">{errorMessage(list.error ?? result.error)}</Alert>
  if (list.data && list.data.length === 0) return null
  const r = result.data
  if (!r) return <Typography color="text.secondary">讀取結果中…</Typography>
  const el = r.eligibility
  const pricePending = el.pending_decisions.some((d) => d.kind === 'accept_missing_price')

  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h6">生成結果（第 {r.result_version} 版）</Typography>
        <Chip color={r.mode === 'ai_generated_menu' ? 'secondary' : 'default'} label={r.mode_label} />
        {r.derived_from_result_version !== null && (
          <Chip size="small" variant="outlined" label={`由第 ${r.derived_from_result_version} 版重新查價`} />
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
                    <Button size="small" variant="outlined" disabled={retry.isPending} onClick={() => retry.mutate(r)}>
                      重新查價
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
      {(decide.isError || retry.isError) && <Alert severity="error">{errorMessage(decide.error ?? retry.error)}</Alert>}

      <Divider />
      <Typography variant="subtitle1">全家配方（整餐用量）</Typography>
      {r.dishes.map((d) => (
        <Box key={d.standard_dish_id}>
          <Typography sx={{ fontWeight: 'bold' }}>
            {d.dish_name}{d.specified && '（指定菜）'}{d.cooking_method && ` · ${d.cooking_method}`}
          </Typography>
          <Typography variant="body2">
            {d.ingredients.map((i) => `${i.name} ${grams(i.display_grams)}`).join('、')}
          </Typography>
          <Box component="ol" sx={{ my: 0.5, pl: 3 }}>
            {d.steps.map((s, idx) => <li key={idx}><Typography variant="body2">{s}</Typography></li>)}
          </Box>
        </Box>
      ))}

      <Divider />
      <Typography variant="subtitle1">營養估算</Typography>
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
                          {i.product_url ? <Link href={i.product_url} target="_blank" rel="noreferrer">{i.retailer ?? i.source}</Link> : (i.retailer ?? i.source)}
                          {i.queried_at && ` · ${new Date(i.queried_at).toLocaleString()}`}
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
        <Alert severity="warning">營養知識服務暫時無法使用，知識說明稍後可重試；菜單與採用資格不受影響。</Alert>
      )}
      {r.knowledge.status === 'available' && (
        <Box>
          <Typography variant="subtitle1">食材營養知識</Typography>
          {r.knowledge.items.map((k) => (
            <Box key={k.ingredient_id} sx={{ mb: 1 }}>
              <Typography variant="body2"><b>{k.ingredient_name ?? k.ingredient_id}</b>：{k.explanation}</Typography>
              <Typography variant="caption" color="text.secondary">
                來源：{k.citations.map((c, idx) => (
                  <span key={c.knowledge_id}>
                    {idx > 0 && '、'}
                    {c.source_url ? <Link href={c.source_url} target="_blank" rel="noreferrer">{c.knowledge_id}</Link> : c.knowledge_id}
                    （{c.source_version}）
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
