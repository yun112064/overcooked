import { Alert, Paper, Stack, Typography } from '@mui/material'

export type StepStyleChecks = {
  style_version: string
  checked_count: number
  findings: {
    step_no: number
    original: string
    suggestion: string | null
    status: 'needs_review' | 'suggested'
    reasons: string[]
  }[]
}

export function StepStyleReview({ checks }: { checks: StepStyleChecks }) {
  const reviewCount = checks.findings.filter((step) => step.status === 'needs_review').length
  return <Stack spacing={1}>
    <Typography variant="subtitle1">步驟用詞檢查</Typography>
    <Alert severity={reviewCount ? 'warning' : 'info'}>
      檢查 {checks.checked_count} 步，{checks.findings.length - reviewCount} 步有改寫建議，{reviewCount} 步待人工確認。儲存草稿時會保留原步驟。
    </Alert>
    {checks.findings.map((step) => <Paper variant="outlined" sx={{ p: 1.5 }} key={step.step_no}>
      <Stack spacing={0.5}>
        <Typography variant="subtitle2">第 {step.step_no} 步 · {step.status === 'needs_review' ? '待人工確認' : '建議改寫'}</Typography>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>原文 {step.original}</Typography>
        {step.suggestion && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>建議 {step.suggestion}</Typography>}
        {step.reasons.map((reason) => <Typography key={reason} variant="body2" color="text.secondary">{reason}</Typography>)}
      </Stack>
    </Paper>)}
  </Stack>
}
