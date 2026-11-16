/**
 * 屋內每個場景共用的 HUD（DESIGN.md 4.3）。一定要透過 <Room hud={…}> 放進去，因為它要問房間現在是不是手機版面。
 *
 *   <Room … hud={<SceneHud tab="plan" />}>
 *
 * - tab：目前在哪個分頁（會標 aria-current）。子畫面沿用上一層的分頁，例如餐桌與單板都是 'plan'。
 * - nav：'tabs'（預設）顯示五個分頁——手機是底部分頁，桌機與平板是左側的浮動側欄；'none' 不顯示導覽。
 * - actions：底部動作列的內容（放 <Button>）。手機上它取代分頁；桌機上浮在底部中央，側欄照舊。
 * - actionsLabel：動作列給讀屏的名稱。
 * - menu：右上角的家庭選單（景深、換家、登出）；預設顯示。玄關不用 SceneHud，自己放 <SessionMenu />。
 * - assistant：吉祥物（飲食小幫手）；預設顯示並沿著底部來回跑。'parked'＝站在右下角不跑（主要按鈕就在跑道上的場景用），false＝不顯示。
 * - children：其他固定在畫面上的東西（<Panel>、<ConfirmDialog>…）。
 *
 * HUD 佔掉的高度會自動告訴房間，物件列和吉祥物跑道會排在分頁／動作列的上方，場景不用自己算。
 */
import { useLayoutEffect, type ReactNode } from 'react'
import { Link } from 'react-router'
import { AssistantLauncher } from '../assistant/AssistantLauncher'
import { useRoom } from '../camera/Room'
import { useMe } from '../session/session'
import { HUD_TABS, hudInsets, type HudTab } from './hudLayout'
import { SessionMenu } from './SessionMenu'
import styles from './SceneHud.module.css'

type SceneHudProps = {
  tab?: HudTab
  nav?: 'tabs' | 'none'
  actions?: ReactNode
  actionsLabel?: string
  menu?: boolean
  assistant?: boolean | 'parked'
  children?: ReactNode
}

// 線條圖示（24×24）：沙發、鍋、書、月曆、人
const ICONS: Record<HudTab, ReactNode> = {
  today: <path d="M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3M3 12.5a2 2 0 0 1 4 0V15h10v-2.5a2 2 0 0 1 4 0V18H3zM6 18v2M18 18v2" />,
  plan: <path d="M4 10h16v4a6 6 0 0 1-6 6h-4a6 6 0 0 1-6-6zM2 10h20M9.5 6.5c0-1.2 1-1.3 1-2.5M14 6.5c0-1.2 1-1.3 1-2.5" />,
  pick: <path d="M12 6.5C10.5 5 8 4.5 4 4.5v13c4 0 6.5.5 8 2 1.5-1.5 4-2 8-2v-13c-4 0-6.5.5-8 2zM12 6.5v13" />,
  records: <path d="M5 5.5h14v14H5zM5 10h14M9 3.5v4M15 3.5v4M9 14h2M14 14h1" />,
  family: <path d="M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3.5 19.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5M16 11.5a2.5 2.5 0 1 0-.8-4.87M17 14.7c2.1.5 3.5 2.2 3.5 4.8" />,
}

export function SceneHud({
  tab, nav = 'tabs', actions, actionsLabel = '這個畫面的主要動作', menu = true, assistant = true, children,
}: SceneHudProps) {
  const { phone, reserveHud } = useRoom()
  const household = useMe().data?.current_household
  const showTabs = nav === 'tabs'
  const hasActions = actions !== undefined && actions !== null && actions !== false
  const { bottom, left } = hudInsets(phone, showTabs, hasActions)

  // 畫面畫出來之前就讓房間知道底部被佔掉多少，物件列才不會先畫在分頁後面再跳上來
  useLayoutEffect(() => {
    reserveHud({ bottom, left })
    return () => reserveHud({})
  }, [reserveHud, bottom, left])

  return (
    <>
      {menu && household && <SessionMenu householdName={household.name} />}

      {showTabs && !(phone && hasActions) && (
        <nav className={phone ? styles.tabs : styles.rail} aria-label="主要導覽" data-no-swipe>
          {HUD_TABS.map((item) => (
            <Link
              key={item.id} to={item.to} className={styles.item} draggable={false}
              aria-current={item.id === tab ? 'page' : undefined}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[item.id]}</svg>
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
      )}

      {hasActions && (
        <div className={styles.actions} role="group" aria-label={actionsLabel} data-no-swipe>{actions}</div>
      )}

      {assistant && <AssistantLauncher parked={assistant === 'parked'} />}
      {children}
    </>
  )
}
