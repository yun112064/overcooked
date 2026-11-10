import { Alert, Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material'
import { MEAL_LABELS, type MealResult, type NutritionPreview } from '../api/types'
import { toNumber } from '../api/nutrition'
import { Section } from './ui'

type Row = { id: string; name: string; kind: string; n: string; share: string; staple?: string | null }
function percent(raw: string) {
  const n = toNumber(raw)
  return n === null ? '未知' : `${(n * 100).toFixed(1)}%`
}
function PortionsTable({ rows }: { rows: Row[] }) {
  return <Section title="全家分餐" aside="用餐與便當分開盛裝">
    {rows.length === 0 ? <Alert severity="info">目前還沒有可用的個人分餐資料。</Alert> : <>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>依照每位成員的餐份比例分配各道菜，便當另外盛裝。</Typography>
      <Box sx={{ overflowX: 'auto' }}><Table size="small">
        <caption style={{ textAlign: 'left', padding: '12px 0' }}>各餐份占整餐的分配比例</caption>
        <TableHead><TableRow><TableCell>成員</TableCell><TableCell>餐份</TableCell><TableCell>食量</TableCell><TableCell>分配比例</TableCell><TableCell>主食</TableCell></TableRow></TableHead>
        <TableBody>{rows.map(row => <TableRow key={row.id}>
          <TableCell sx={{ whiteSpace: 'nowrap' }}>{row.name}</TableCell><TableCell sx={{ whiteSpace: 'nowrap' }}>{row.kind}</TableCell>
          <TableCell>{row.n}n</TableCell><TableCell>{percent(row.share)}</TableCell>
          <TableCell>{row.staple == null ? '—' : `${row.staple} g`}</TableCell>
        </TableRow>)}</TableBody>
      </Table></Box>
    </>}
  </Section>
}

export function GeneratedPortions({ result }: { result: MealResult }) {
  const rows: Row[] = []
  for (const person of result.nutrition.per_person ?? []) {
    if (person.dining ?? true) rows.push({ id: `${person.member_id}-dining`, name: person.name ?? '已刪除成員',
      kind: person.dining === undefined && person.kinds.includes('bento') ? '用餐與便當（合併紀錄）' : '當餐用餐', n: person.n, share: person.share })
    if (person.bento) rows.push({ id: `${person.member_id}-bento`, name: person.name ?? '已刪除成員',
      kind: `${MEAL_LABELS[person.bento.meal]}便當`, n: person.bento.n, share: person.bento.share })
  }
  // 正式結果沒有每人主食重量，不從整餐重量或示範熟食量推算。
  return <PortionsTable rows={rows} />
}

export function PreviewPortions({ nutrition, names }: { nutrition: NutritionPreview | undefined; names: Map<string, string> }) {
  return <PortionsTable rows={(nutrition?.per_person ?? []).map(person => ({
    id: person.participation_id, name: names.get(person.member_id) ?? '已刪除成員',
    kind: `${MEAL_LABELS[person.meal]}${person.kind === 'bento' ? '便當' : '用餐'}`,
    n: person.n, share: person.dish_share,
    staple: person.staple_g === null ? null : String(toNumber(person.staple_g) ?? person.staple_g),
  }))} />
}
