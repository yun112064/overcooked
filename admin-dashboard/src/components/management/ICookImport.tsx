import { useState } from 'react'
import { Alert, Box, Button, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material'
import { managementError } from '../../api/management'
import type { Proposal, Recipe } from '../../api/management'
import { useManagementCommand } from './hooks'
import { StepStyleReview } from './StepStyleReview'
import type { StepStyleChecks } from './StepStyleReview'

type Preview = {
  step_checks: StepStyleChecks
  preview_id: string
  existing_proposal_id: string | null
  servings_count: string | null
  source: { url: string }
  tables: {
    recipes: { recipe_name: string; servings_raw: string | null }[]
    recipe_ingredients: { sequence: number; raw_text: string; canonical_name: string | null
      amount: string | null; unit: string | null; weight_g: string | null }[]
    recipe_steps: Recipe['steps']
  }
}

export function ICookImport({ openProposal }: { openProposal: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [url, setUrl] = useState('')
  const preview = useManagementCommand<Preview>('/recipe-candidates/icook/preview')
  const save = useManagementCommand<{ proposal: Proposal }>('/recipe-candidates/icook/drafts', ({ proposal }) => {
    setExpanded(false)
    setUrl('')
    preview.reset()
    openProposal(proposal.proposal_id)
  })
  const data = preview.data
  const pending = preview.isPending || save.isPending

  return <Stack spacing={1}>
    <Button variant="outlined" sx={{ alignSelf: 'start' }} disabled={pending} onClick={() => setExpanded(!expanded)}>
      {expanded ? '收合 iCook 匯入' : '從 iCook 匯入'}
    </Button>
    {expanded && <Paper variant="outlined" sx={{ p: 2 }}><Stack spacing={2}>
      <Box component="form" onSubmit={(event) => {
        event.preventDefault()
        if (url.trim() && !pending) {
          save.reset()
          preview.mutate({ url: url.trim() })
        }
      }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
          <TextField fullWidth size="small" label="iCook 食譜網址" value={url} disabled={pending}
            onChange={(event) => { setUrl(event.target.value); preview.reset(); save.reset() }} />
          <Button type="submit" variant="contained" disabled={!url.trim() || pending} sx={{ flexShrink: 0 }}>
            {preview.isPending ? '正在預覽' : '預覽食譜'}
          </Button>
        </Stack>
      </Box>
      {preview.isError && <Alert severity="error">{managementError(preview.error, '無法預覽 iCook 食譜。')}</Alert>}
      {data && <Stack spacing={1.5}>
        <Typography variant="h6">{data.tables.recipes[0].recipe_name}</Typography>
        <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{data.source.url}</Typography>
        <TextField size="small" label="人份" value={data.servings_count ?? ''} sx={{ maxWidth: 180 }}
          helperText={data.servings_count === null ? '來源未提供可用的人份，儲存草稿後可補填。' : undefined}
          slotProps={{ input: { readOnly: true }, inputLabel: { shrink: true } }} />
        {data.tables.recipes[0].servings_raw && <Typography variant="body2">來源份量原文 {data.tables.recipes[0].servings_raw}</Typography>}
        <Typography variant="subtitle1">食材</Typography>
        <TableContainer><Table size="small" aria-label="iCook 預覽食材">
          <TableHead><TableRow>
            <TableCell>來源原文</TableCell><TableCell>清整名稱</TableCell><TableCell>用量</TableCell><TableCell>單位</TableCell>
          </TableRow></TableHead>
          <TableBody>{data.tables.recipe_ingredients.map((line) => {
            const grams = line.weight_g !== '0' ? line.weight_g : null
            const ml = line.unit === 'ml' && line.amount !== '0' ? line.amount : null
            return <TableRow key={line.sequence}>
              <TableCell>{line.raw_text}</TableCell><TableCell>{line.canonical_name}</TableCell>
              <TableCell>{grams ?? ml}</TableCell><TableCell>{grams ? 'g' : ml ? 'ml' : ''}</TableCell>
            </TableRow>
          })}</TableBody>
        </Table></TableContainer>
        <Typography variant="subtitle1">步驟</Typography>
        <Box component="ol" aria-label="iCook 預覽步驟" sx={{ pl: 3, m: 0 }}>
          {data.tables.recipe_steps.map((step) => <li key={step.step_no}>{step.instruction}</li>)}
        </Box>
        <StepStyleReview checks={data.step_checks} />
        {data.existing_proposal_id ? <Button variant="outlined" sx={{ alignSelf: 'start' }} onClick={() => {
          if (data.existing_proposal_id) openProposal(data.existing_proposal_id)
          setExpanded(false)
        }}>查看此網址的既有提案</Button> :
          <Button variant="contained" sx={{ alignSelf: 'start' }} disabled={pending}
            onClick={() => save.mutate({ preview_id: data.preview_id })}>
            {save.isPending ? '正在儲存' : '確認並儲存為待審草稿'}
          </Button>}
        {save.isError && <Alert severity="error">{managementError(save.error, '無法儲存 iCook 草稿。')}</Alert>}
      </Stack>}
    </Stack></Paper>}
  </Stack>
}
