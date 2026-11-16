/**
 * 已採用之後，問卷最後一步列出的菜單：只列採用當下保存的那筆紀錄（GET /meal-plans?date=，和冰箱門讀的是同一份），
 * 不再查即時的資料庫菜單、不重算營養、也不產生小知識。
 * 問卷採用的可能是餐桌上的生成結果（甚至 AI 菜單），現在重新查到的資料庫菜單不一定是被採用的那一份；
 * 已採用的內容也不該因為之後資料更新而跟著變。完整的配方、份量與每人營養在已採用紀錄裡（同一份快照）。
 *
 * 菜單只在按「開始煮」之後端上餐桌才看得到；問卷還沒採用時這裡什麼都不擺。
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api, errorMessage } from '../../api/client'
import type { MealRequest } from '../../api/types'
import { Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { plansOnDateQuery } from '../../session/queries'
import { Tag } from './fields'
import styles from './Paper.module.css'

/** 一鍵購入：採用後把缺的食材帶到模擬超市（另開分頁）。 */
function MarketCheckoutButton({ planId }: { planId: string }) {
  const handoff = useMutation({
    mutationFn: async () => {
      const result = (await api.post<{ checkout_url: string }>('/sim-market/handoffs', { plan_id: planId })).data
      const url = new URL(result.checkout_url)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid checkout URL')
      return url.href
    },
  })
  async function openMarket() {
    // 先同步開分頁，等 API 回來才導過去；不然瀏覽器會把它當成彈出視窗擋掉
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const url = await handoff.mutateAsync()
      if (tab && !tab.closed) tab.location.replace(url)
      else window.location.assign(url)
    } catch {
      tab?.close()
    }
  }
  return (
    <>
      <button type="button" className={styles.smallButton} disabled={handoff.isPending} onClick={() => { void openMarket() }}>
        {handoff.isPending ? '開啟超市中…' : '一鍵購入'}
      </button>
      {handoff.isError && <Notice tone="error">{errorMessage(handoff.error, '暫時無法開啟模擬超市，請重試。')}</Notice>}
    </>
  )
}

export function AdoptedMenu({ row }: { row: MealRequest }) {
  const date = row.draft.meal_date
  const plans = useQuery({ ...plansOnDateQuery(date ?? ''), enabled: date !== null })
  const plan = plans.data?.find((p) => p.meal_request_id === row.id) ?? null
  const day = plan?.meal_date ?? date
  return (
    <section className={styles.stack} aria-label="已採用的菜單">
      <div className={styles.sectionHead}>
        <h4>這一餐的菜單</h4>
        <Tag tone="ok">已採用</Tag>
      </div>
      <Notice tone="success" title="已採用">
        已保存為已採用餐食紀錄（不代表實際已食用）。
        <span className={styles.inlineActions}>
          <Link className={`${styles.smallButton} ${styles.smallPrimary}`} to="/today">回客廳</Link>
          <Link className={styles.smallButton} to={day ? `/records?tab=adopted&date=${day}` : '/records?tab=adopted'}>查看已採用紀錄</Link>
          {plan?.status === 'adopted' && plan.market_shortages_available && <MarketCheckoutButton planId={plan.id} />}
        </span>
      </Notice>
      {!plan && plans.isFetching && <Spinner label="正在讀取已採用紀錄…" />}
      {!plan && plans.isError && <Notice tone="error">{errorMessage(plans.error)}</Notice>}
      {plan && plan.dishes.length > 0 && (
        <ul className={styles.menuNames}>
          {plan.dishes.map((d, i) => <li key={`${d.recipe_id ?? d.dish_name}-${i}`}>{d.dish_name}</li>)}
        </ul>
      )}
      <p className={styles.fieldHint}>
        這裡列的是採用當下保存的菜，之後資料更新也不會變；配方、份量與每人營養請看已採用紀錄。
      </p>
    </section>
  )
}
