import { QueryClient, useQuery } from '@tanstack/react-query'
import { api, errorStatus } from '../api/client'
import type { Me } from '../api/types'

/**
 * 查詢失敗要不要再試。只有「再試一次可能會好」的才重試：連不上（沒有狀態碼）、伺服器錯誤（5xx）、逾時（408）、太頻繁（429）。
 * 其餘 4xx 是答案不是故障（401 沒登入、404 找不到、409 還沒選家庭、422 算不出來），
 * 馬上交給畫面處理；重試只會讓「找不到」和回玄關的轉址晚三秒才出現。
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  const status = errorStatus(error)
  const transient = status === undefined || status >= 500 || status === 408 || status === 429
  return transient && failureCount < 2
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
  },
})

/** 目前登入的人；沒登入回傳 null（不是錯誤）。 */
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return (await api.get<Me>('/auth/me')).data
      } catch (err) {
        if (errorStatus(err) === 401) return null
        throw err
      }
    },
  })
}

// 與目前家庭無關的快取；換家庭時其餘的都要清掉，避免短暫看到上一個家庭的資料
// （team-status 是生成面板、team-health 是飲食小幫手，兩個都讀 GET /health/team，key 沿用舊前端）
const GLOBAL_QUERY_KEYS = new Set(['me', 'households', 'staple-options', 'team-status', 'team-health', 'ingredients', 'member-options'])

/** 只留和家庭無關的快取。 */
function dropHouseholdData(client: QueryClient): void {
  client.removeQueries({ predicate: (q) => !GLOBAL_QUERY_KEYS.has(String(q.queryKey[0])) })
}

export async function afterHouseholdChange(client: QueryClient): Promise<void> {
  dropHouseholdData(client)
  await client.invalidateQueries({ queryKey: ['me'] })
  await client.invalidateQueries({ queryKey: ['households'] })
}

/**
 * 登入或註冊成功：把回傳的身分放進快取。
 * 上一個人若是登入逾期被請出門（沒有按登出），他的家庭資料還留在快取裡；換人進門前先清掉，連家庭清單也是。
 */
export function afterLogin(client: QueryClient, me: Me): void {
  dropHouseholdData(client)
  client.removeQueries({ queryKey: ['households'] })
  client.setQueryData(['me'], me)
}

/** 登出：不論伺服器有沒有回應，本機的資料全部清掉。 */
export function afterLogout(client: QueryClient): void {
  client.clear()
}
