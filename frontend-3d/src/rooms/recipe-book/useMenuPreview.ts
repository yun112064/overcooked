/**
 * 「結果」那一步的資料：資料庫菜單 → 營養計算 → 營養小知識，以及採用／換一份。
 * 端點、query key、送出的內容和舊前端的 MenuPreviewPanel 相同。
 *
 * active：只有翻到結果那一步才查（舊前端是那一步才把面板掛上去）。
 * 小知識是雲端付費呼叫：營養通過後自動產生，同一版問卷與菜單在頁面開著時只呼叫一次。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { api } from '../../api/client'
import { invalidateAdoptedPlans } from '../../session/queries'
import type { KnowledgeTips, MealPlanSummary, MealRequest, NutritionPreview, RecipeRetrieval } from '../../api/types'
import { newKey } from './idempotency'

export function useMenuPreview(row: MealRequest | null, active: boolean, onAdopted: () => void) {
  const queryClient = useQueryClient()
  const id = row?.id ?? ''
  const version = row?.version ?? 0
  // 還沒挑完就沒有菜單可看；已採用的單永遠看得到採用當下的那一份
  const available = !!row && (row.complete || (row.draft.mode === 'picker' && row.status === 'confirmed'))

  const menu = useQuery({
    queryKey: ['meal-request-menu', id, version],
    queryFn: async () => (await api.get<RecipeRetrieval>(`/meal-requests/${id}/recipes`)).data,
    enabled: active && available,
    retry: false,
  })
  const selected = menu.data?.selected ?? []
  const recipeIds = selected.map((r) => r.recipe_id)

  const nutrition = useQuery({
    queryKey: ['meal-request-nutrition', id, version, recipeIds],
    queryFn: async () =>
      (await api.post<NutritionPreview>(`/meal-requests/${id}/nutrition-preview`, { recipe_ids: recipeIds })).data,
    enabled: active && recipeIds.length > 0,
    retry: false,
  })
  const tips = useQuery({
    queryKey: ['meal-request-tips', id, version, recipeIds],
    queryFn: async () =>
      (await api.post<KnowledgeTips>(`/meal-requests/${id}/knowledge-tips`, { recipe_ids: recipeIds })).data,
    enabled: active && nutrition.data?.status === 'nutrition_passed',
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
  })

  // 同一份菜單固定一把冪等鍵：重複點擊或網路重送只採用一次
  const adoptKeys = useRef(new Map<string, string>())
  const adopt = useMutation({
    // 參數是按下當時的問卷版本，用來判斷錯誤訊息是不是還屬於眼前這一版
    mutationFn: async (requestVersion: number) => {
      const name = `${requestVersion}:${recipeIds.join(',')}`
      const key = adoptKeys.current.get(name) ?? newKey()
      adoptKeys.current.set(name, key)
      return (await api.post<{ meal_plan: MealPlanSummary }>('/meal-plans',
        { meal_request_id: id, request_version: requestVersion, recipe_ids: recipeIds },
        { headers: { 'Idempotency-Key': key } })).data.meal_plan
    },
    onSuccess: async () => {
      onAdopted()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['meal-request-menu', id] }),
        // 客廳、廚房便利貼、冰箱門讀的已採用紀錄與趨勢一起更新
        invalidateAdoptedPlans(queryClient),
      ])
    },
  })
  const reject = useMutation({
    mutationFn: async (requestVersion: number) =>
      api.post(`/meal-requests/${id}/menu-rejections`, { version: requestVersion, recipe_ids: recipeIds }),
    onSuccess: () => menu.refetch(),
  })

  const n = nutrition.data
  const adopted = adopt.data ?? menu.data?.adopted_plan ?? null
  const passed = n?.status === 'nutrition_passed'
  const busy = adopt.isPending || reject.isPending
  const status = row?.status

  return {
    available,
    menu,
    nutrition,
    tips,
    selected,
    adopted,
    passed,
    /** 採用／換一份這兩顆按鈕只在營養算出來、而且還沒採用時出現（和舊前端相同） */
    showActions: !!n && !adopted,
    canAdopt: !!n && passed && !n.adoption_blocked && !busy
      && status !== 'queued' && status !== 'running' && status !== 'confirmed',
    canReject: !busy && selected.length > 0,
    adopting: adopt.isPending,
    rejecting: reject.isPending,
    adopt: () => adopt.mutate(version),
    reject: () => reject.mutate(version),
    // 換了版本（重新挑菜）之後，上一版的錯誤訊息不再顯示
    adoptError: adopt.isError && adopt.variables === version ? adopt.error : null,
    rejectError: reject.isError && reject.variables === version ? reject.error : null,
  }
}

export type MenuPreview = ReturnType<typeof useMenuPreview>
