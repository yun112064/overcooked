import { useState } from 'react'
import { Alert, Button, Checkbox, FormControlLabel, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { domains, managementError, time } from '../../api/management'
import type { Domain } from '../../api/management'
import { useManagementCommand, useManagementRead } from './hooks'
import { ReadState } from './shared'

type RoleGrant = { grant_id: string; role: string; domain_scope: Domain[]; expires_at: string | null }
type GrantList = { access_revision: number; roles: string[]; domains: Domain[]; items: RoleGrant[] }
const roleNames: Record<string, string> = {
  catalog_viewer: '目錄查閱', recipe_editor: '食譜編輯', ingredient_editor: '食材編輯', price_editor: '價格編輯',
  recipe_reviewer: '食譜審查', ingredient_reviewer: '食材審查', price_reviewer: '價格審查', source_reviewer: '來源審查',
  publisher: '發布管理', operator: '營運管理', access_admin: '權限管理', auditor: '稽核查閱',
}
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

export function RoleManagement({ actor, canManage }: { actor: string; canManage: boolean }) {
  const [input, setInput] = useState('')
  const [user, setUser] = useState('')
  return <Stack spacing={2}>
    <Typography variant="h6">角色權限</Typography>
    <Stack component="form" direction="row" spacing={1} onSubmit={(event) => {
      event.preventDefault()
      if (uuid.test(input.trim())) setUser(input.trim().toLowerCase())
    }}>
      <TextField size="small" fullWidth label="既有帳號識別碼（UUID）" value={input} onChange={(event) => setInput(event.target.value)} />
      <Button type="submit" disabled={!uuid.test(input.trim())}>查詢</Button>
    </Stack>
    {user && <UserGrants key={user} user={user} editable={canManage && user !== actor} />}
  </Stack>
}

function UserGrants({ user, editable }: { user: string; editable: boolean }) {
  const grants = useManagementRead<GrantList>('/role-grants', { user_id: user })
  const [role, setRole] = useState('')
  const [scope, setScope] = useState<Domain[]>([])
  const [reason, setReason] = useState('')
  const [revoke, setRevoke] = useState<RoleGrant>()
  const grantCommand = useManagementCommand('/role-grants', () => setReason(''))
  const revokeCommand = useManagementCommand(`/role-grants/${revoke?.grant_id}/revoke`, () => {
    setRevoke(undefined)
    setReason('')
  })
  const pending = grantCommand.isPending || revokeCommand.isPending
  return <ReadState query={grants}>{(data) => <Stack spacing={1.5}>
    <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>帳號 {user}</Typography>
    {!data.items.length && <Typography>此帳號在授權範圍內沒有有效角色。</Typography>}
    {data.items.map((item) => <Paper key={item.grant_id} variant="outlined" sx={{ p: 1.5 }}>
      <Typography>{roleNames[item.role] ?? item.role} · {item.domain_scope.map((domain) => domains[domain]).join('、')}</Typography>
      <Typography variant="body2">到期 {item.expires_at ? time(item.expires_at) : '未設定'}</Typography>
      {editable && <Button color="warning" onClick={() => { setRevoke(item); setReason(''); revokeCommand.reset() }} disabled={pending}>選擇撤銷</Button>}
    </Paper>)}
    {editable && <>
      {revoke ? <Alert severity="warning">將撤銷「{roleNames[revoke.role] ?? revoke.role}」。</Alert> : <>
        <TextField select size="small" label="要授予的角色" value={role} onChange={(event) => setRole(event.target.value)}>
          <MenuItem value="">選擇角色</MenuItem>
          {data.roles.map((value) => <MenuItem key={value} value={value}>{roleNames[value] ?? value}</MenuItem>)}
        </TextField>
        <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap' }}>
          {data.domains.map((domain) => <FormControlLabel key={domain} label={domains[domain]}
            control={<Checkbox checked={scope.includes(domain)} onChange={(_, checked) =>
              setScope((current) => checked ? [...current, domain] : current.filter((value) => value !== domain))} />} />)}
        </Stack>
      </>}
      <TextField size="small" label="操作原因" value={reason} onChange={(event) => setReason(event.target.value)} />
      <Stack direction="row" spacing={1}>
        {revoke ? <>
          <Button color="warning" variant="contained" disabled={pending || !reason.trim()} onClick={() =>
            revokeCommand.mutate({ expected_access_revision: data.access_revision, reason: reason.trim() })}>確認撤銷</Button>
          <Button disabled={pending} onClick={() => { setRevoke(undefined); setReason(''); revokeCommand.reset() }}>取消</Button>
        </> : <Button variant="contained" disabled={pending || !role || !scope.length || !reason.trim()} onClick={() => grantCommand.mutate({
          user_id: user, role, domain_scope: scope, expires_at: null, expected_access_revision: data.access_revision, reason: reason.trim(),
        })}>授予角色</Button>}
      </Stack>
      {grantCommand.isError && <Alert severity="error">{managementError(grantCommand.error)}</Alert>}
      {revokeCommand.isError && <Alert severity="error">{managementError(revokeCommand.error)}</Alert>}
    </>}
  </Stack>}</ReadState>
}
