/**
 * 團隊電腦上的服務（蔬菜辨識、營養知識）是否在線：GET /health/team（和舊前端同一個 query key）。
 * 離線時生成仍可用，只是辨識與知識說明暫時無法使用。查詢本體在 session/queries.ts，各房間共用快取。
 */
import { useQuery } from '@tanstack/react-query'
import { teamStatusQuery, type ServiceState } from '../../session/queries'

export type { ServiceState }

export const SERVICE_LABEL: Record<ServiceState['status'], string> = { online: '在線', offline: '離線', not_configured: '未設定' }

export function useTeamStatus() {
  return useQuery(teamStatusQuery)
}
