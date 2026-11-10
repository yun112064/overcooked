import { useState } from 'react'
import { Alert, Box, Button, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material'
import { managementError, time } from '../../api/management'
import type { Proposal } from '../../api/management'
import { useManagementCommand, useManagementRead } from './hooks'
import { ReadState } from './shared'
import { StepStyleReview } from './StepStyleReview'
import type { StepStyleChecks } from './StepStyleReview'

const path = '/recipe-candidates/generated'
type Selection = { result_id: string; dish_index: number }
type Choice = Selection & { result_version: number; created_at: string; name: string }
type Snapshot = {
  source: { result_id: string; result_version: number; dish_index: number; created_at: string }
  name: string
  ingredients: { name: string; food_id: string; grams_per_n: string }[]
  steps: string[]
}
type Preview = { snapshot: Snapshot; snapshot_sha256: string; existing_proposal_id: string | null; step_checks: StepStyleChecks }

function PerPortionAmounts({ snapshot }: { snapshot: Snapshot }) {
  return <TableContainer><Table size="small" aria-label="AI 每份用量">
    <TableHead><TableRow>
      <TableCell>食材</TableCell><TableCell>Food ID</TableCell><TableCell>原始每 n=1 用量</TableCell>
    </TableRow></TableHead>
    <TableBody>{snapshot.ingredients.map((line, index) => <TableRow key={index}>
      <TableCell>{line.name}</TableCell><TableCell>{line.food_id}</TableCell><TableCell>{line.grams_per_n} g</TableCell>
    </TableRow>)}</TableBody>
  </Table></TableContainer>
}

export function GeneratedRecipeImport({ openProposal }: { openProposal: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(0)
  const choices = useManagementRead<{ items: Choice[]; has_next: boolean }>(path + '/choices', { page }, expanded)
  const preview = useManagementCommand<Preview>(path + '/preview')
  const save = useManagementCommand<{ proposal: Proposal }>(path + '/drafts', ({ proposal }) => {
    setExpanded(false)
    preview.reset()
    openProposal(proposal.proposal_id)
  })
  const pending = preview.isPending || save.isPending
  const data = preview.data
  function changePage(next: number) {
    preview.reset()
    save.reset()
    setPage(next)
  }

  return <Stack spacing={1}>
    <Button variant="outlined" sx={{ alignSelf: 'start' }} disabled={pending} onClick={() => {
      if (expanded) changePage(0)
      setExpanded(!expanded)
    }}>{expanded ? '收合 AI 菜色匯入' : '匯入已生成的 AI 菜色'}</Button>
    {expanded && <Paper variant="outlined" sx={{ p: 2 }}><Stack spacing={2}>
      <Alert severity="info">從目前家庭的 AI 生成結果挑選一道菜。確認儲存後，這道菜的配方與步驟會成為食譜管理人員可查閱的草稿。</Alert>
      <ReadState query={choices}>{(data) => <Stack spacing={1}>
        {data.items.length === 0 && <Typography>這一頁沒有可匯入的 AI 菜色。</Typography>}
        {data.items.map((choice) => <Stack key={choice.result_id + ':' + choice.dish_index}
          direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' } }}>
          <Typography sx={{ flex: 1 }}>{choice.name} · 結果第 {choice.result_version} 版 · {time(choice.created_at)}</Typography>
          <Button disabled={pending} aria-label={`預覽 ${choice.name}`} onClick={() => {
            save.reset()
            preview.mutate({ result_id: choice.result_id, dish_index: choice.dish_index })
          }}>預覽</Button>
        </Stack>)}
        {(page > 0 || data.has_next) && <Stack direction="row" spacing={1}>
          <Button disabled={pending || page === 0} onClick={() => changePage(page - 1)}>上一頁</Button>
          <Button disabled={pending || !data.has_next} onClick={() => changePage(page + 1)}>下一頁</Button>
        </Stack>}
      </Stack>}</ReadState>
      {preview.isError && <Alert severity="error">{managementError(preview.error, '無法預覽 AI 菜色。')}</Alert>}
      {data && <Stack spacing={1.5}>
        <Typography variant="h6">{data.snapshot.name}</Typography>
        <Typography variant="body2">來源結果第 {data.snapshot.source.result_version} 版 · {time(data.snapshot.source.created_at)}</Typography>
        <Alert severity="info">每 n=1 的克數會原樣保存。人份與整份用量留白，儲存草稿後可補填。</Alert>
        <PerPortionAmounts snapshot={data.snapshot} />
        <Typography variant="subtitle1">步驟</Typography>
        <Box component="ol" aria-label="AI 預覽步驟" sx={{ pl: 3, m: 0 }}>
          {data.snapshot.steps.map((step, index) => <li key={index}>{step}</li>)}
        </Box>
        <StepStyleReview checks={data.step_checks} />
        {data.existing_proposal_id ? <Button sx={{ alignSelf: 'start' }} onClick={() => {
          if (data.existing_proposal_id) openProposal(data.existing_proposal_id)
          setExpanded(false)
        }}>查看此菜色的既有提案</Button> :
          <Button variant="contained" sx={{ alignSelf: 'start' }} disabled={pending} onClick={() => save.mutate({
            result_id: data.snapshot.source.result_id, dish_index: data.snapshot.source.dish_index,
            snapshot_sha256: data.snapshot_sha256,
          })}>確認並儲存 AI 草稿</Button>}
        {save.isError && <Alert severity="error">{managementError(save.error, '無法儲存 AI 菜色草稿。')}</Alert>}
      </Stack>}
    </Stack></Paper>}
  </Stack>
}

export function GeneratedRecipeEvidence({ id }: { id: string }) {
  const query = useManagementRead<Snapshot>(`${path}/${id}/source`)
  return <ReadState query={query}>{(snapshot) => <Stack spacing={1}>
    <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
      AI 來源結果 {snapshot.source.result_id} · 第 {snapshot.source.result_version} 版 · 第 {snapshot.source.dish_index + 1} 道菜
    </Typography>
    <PerPortionAmounts snapshot={snapshot} />
  </Stack>}</ReadState>
}
