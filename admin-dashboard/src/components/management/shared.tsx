import { Alert, Button, CircularProgress, Stack, Typography } from '@mui/material'
import type { UseQueryResult } from '@tanstack/react-query'
import { managementError } from '../../api/management'

export function ReadState<T>({ query, unavailable, children }: {
  query: UseQueryResult<T>; unavailable?: string; children: (data: T) => React.ReactNode
}) {
  if (query.isPending) return <CircularProgress size={22} aria-label="載入中" />
  if (query.isError) {
    return <Alert severity="warning" action={<Button onClick={() => void query.refetch()}>重試</Button>}>
      {managementError(query.error, unavailable)}
    </Alert>
  }
  return children(query.data)
}

export function ResultPage({ empty, next, advance, reset, children }: {
  empty: boolean; next: string | null; advance: (cursor: string) => void
  reset?: () => void; children: React.ReactNode
}) {
  return <Stack spacing={1.5}>
    {empty ? <Typography color="text.secondary">沒有符合條件的紀錄。</Typography> : children}
    <Stack direction="row" spacing={1}>
      {reset && <Button onClick={reset}>第一頁</Button>}
      {next && <Button onClick={() => advance(next)}>下一頁</Button>}
    </Stack>
  </Stack>
}
