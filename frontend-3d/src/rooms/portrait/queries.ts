/** 只有全家福用到的查詢。成員清單是共用的，在 src/session/queries.ts。 */
import { queryOptions } from '@tanstack/react-query'
import { api, errorStatus } from '../../api/client'
import type { IngredientRef, Member, MemberOptions } from '../../api/types'

/** 表單選項與規則版本（key 和舊前端相同）；這一趟瀏覽內不會變。 */
export const memberOptionsQuery = queryOptions({
  queryKey: ['member-options'],
  queryFn: async () => (await api.get<MemberOptions>('/members/options')).data,
  staleTime: Infinity,
})

/** GET /members/{id}/target：今日的每日基準（還沒乘餐別比例與食量 n）。數字是字串小數。 */
export type MemberTarget = {
  member_id: string
  rule_version: string
  daily_reference: { energy_kcal: string | null; protein_g: string | null }
  approved_for_adoption: boolean
}

/**
 * 成員的每日基準。key 帶 version：成員資料一改就會重新問。
 * 設定算不出目標時後端回 422（附原因），那是答案不是故障，所以 4xx 不重試。
 */
export function memberTargetQuery(member: Pick<Member, 'id' | 'version'>) {
  return queryOptions({
    queryKey: ['member-target', member.id, member.version],
    queryFn: async () => (await api.get<MemberTarget>(`/members/${member.id}/target`)).data,
    staleTime: 60_000,
    // 已經知道算不出來就不要每開一次面板又重問：重問的那一下畫裡的標記會閃掉。成員資料改了 key 會換，自然重問
    retryOnMount: false,
    retry: (count, err) => {
      const status = errorStatus(err)
      return (status === undefined || status >= 500) && count < 2
    },
  })
}

/** 後端明確說「這位成員的設定算不出每日目標」。連不上或其他錯誤不算——那是不知道。 */
export function targetUnavailable(error: unknown): boolean {
  return errorStatus(error) === 422
}

// 服務狀態的查詢在 session/queries.ts（單板、食譜書共用同一份快取）
export { teamStatusQuery, type ServiceState, type TeamStatus } from '../../session/queries'

/** 從標準食材字典搜尋（key、參數與舊前端相同）。 */
export function ingredientsQuery(query: string) {
  return queryOptions({
    queryKey: ['ingredients', query],
    queryFn: async () =>
      (await api.get<{ ingredients: IngredientRef[] }>('/ingredients', { params: { q: query } })).data.ingredients,
    enabled: query.length > 0,
    staleTime: 60_000,
  })
}
