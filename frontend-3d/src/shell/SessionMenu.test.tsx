import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { Me } from '../api/types'
import { SessionMenu } from './SessionMenu'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const ME: Me = { user: { id: 'u1', email: 'a@example.com' }, current_household: null }

function render(householdName?: string): string {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(['me'], ME)
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><SessionMenu householdName={householdName} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SessionMenu', () => {
  it('屋內：開關是家庭名稱', () => {
    const markup = render('林家廚房')
    expect(markup).toContain('aria-label="林家廚房，設定選單"')
    expect(markup).toContain('aria-expanded="false"')
  })

  it('玄關（還沒有家庭）：一樣有選單可以登出，開關寫「帳號」', () => {
    const markup = render()
    expect(markup).toContain('aria-label="帳號，設定選單"')
    expect(markup).toMatch(/<button[^>]*aria-expanded="false"/)
  })
})
