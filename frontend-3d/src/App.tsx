/**
 * 路由與進門的條件（DESIGN.md 4.1）。
 *
 *   /login                        大門（已登入就直接進屋）
 *   /households                   玄關（要登入）
 *   以下都要登入，而且已經選好家庭：
 *   /today                        客廳
 *   /plan                         廚房
 *   /plan/:id、/plan/:id/cooking  單板＋鍋（同一個場景，不重新掛載；用網址判斷是不是在生成中）
 *   /plan/:id/result/:version     餐桌
 *   /pick、/pick/:id              食譜書
 *   /records                      冰箱門（?tab=adopted｜nutrition）
 *   /family                       全家福
 *   /assistant                    飲食小幫手展開模式
 *
 * 每個場景用到才載入，預設匯出一個不收 props 的元件，需要的資料自己讀（useParams、useMe…）。
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { Suspense, lazy, type ComponentType } from 'react'
import { Navigate, Outlet, RouterProvider, createBrowserRouter, useSearchParams } from 'react-router'
import AssistantProvider from './assistant/AssistantProvider'
import { DepthProvider } from './depth/DepthProvider'
import { queryClient, useMe } from './session/session'
import { CrashBoundary, RouteError } from './shell/RouteError'
import { legacyRecordsPath, retryOnce } from './shell/routeHelpers'
import styles from './App.module.css'

/** 用到才載入的房間。手機網路閃一下就會載入失敗，所以自動再試一次；還是不行才交給錯誤畫面。 */
function room(load: () => Promise<{ default: ComponentType }>) {
  return lazy(() => retryOnce(load))
}

const DoorScene = room(() => import('./rooms/door/DoorScene'))
const GenkanScene = room(() => import('./rooms/genkan/GenkanScene'))
const LivingScene = room(() => import('./rooms/living/LivingScene'))
const KitchenScene = room(() => import('./rooms/kitchen/KitchenScene'))
const PassboardScene = room(() => import('./rooms/passboard/PassboardScene'))
const DiningScene = room(() => import('./rooms/dining/DiningScene'))
const RecipeBookScene = room(() => import('./rooms/recipe-book/RecipeBookScene'))
const FridgeScene = room(() => import('./rooms/fridge/FridgeScene'))
const PortraitScene = room(() => import('./rooms/portrait/PortraitScene'))
const AssistantPage = room(() => import('./assistant/AssistantPage'))

function Notice({ children }: { children: string }) {
  return <p className={styles.notice} role="status">{children}</p>
}

const loading = <Notice>載入中…</Notice>

/** 大門：已經登入就不用再敲門。 */
function DoorRoute() {
  const me = useMe()
  if (me.isPending) return loading
  if (me.data) return <Navigate to={me.data.current_household ? '/today' : '/households'} replace />
  return <Suspense fallback={loading}><DoorScene /></Suspense>
}

/**
 * 玄關以內：要登入。
 * 小幫手的對話狀態放在這一層（玄關也在裡面）：換房間、回玄關看一眼再進同一個家，對話都還在；
 * 真的換了帳號或家庭時它自己會清空（見 AssistantProvider）。
 */
function SignedIn() {
  const me = useMe()
  if (me.isPending) return loading
  // 只有「完全不知道是誰」才擋住整間屋子。已經進屋後重新確認身分失敗（網路閃了一下）時 me.data 還在，
  // 就沿用它繼續顯示；否則整棵樹被換成錯誤訊息，小幫手的對話和填到一半的表單都會不見
  if (me.isError && me.data === undefined) {
    // 連不上伺服器時整間屋子都進不去：給一顆按鈕重試，不用整頁重新整理
    return (
      <div className={styles.notice} role="alert">
        <p>無法連線到伺服器，請稍後再試。</p>
        <button type="button" className={styles.retry} disabled={me.isFetching} onClick={() => { void me.refetch() }}>
          {me.isFetching ? '連線中…' : '再試一次'}
        </button>
      </div>
    )
  }
  if (!me.data) return <Navigate to="/login" replace />
  return <AssistantProvider><Suspense fallback={loading}><Outlet /></Suspense></AssistantProvider>
}

/** 屋內：還要已經選好家庭。 */
function AtHome() {
  const me = useMe()
  if (!me.data?.current_household) return <Navigate to="/households" replace />
  return <Outlet />
}

/** 舊前端的 /history、/generation-history：轉到冰箱門，網址上的日期一起帶過去。 */
function LegacyRecords({ tab }: { tab: 'adopted' | 'nutrition' }) {
  const [params] = useSearchParams()
  return <Navigate to={legacyRecordsPath(tab, params.get('date'))} replace />
}

const routeError = <RouteError />

const router = createBrowserRouter([
  {
    // 任何一層畫壞了（房間的程式檔沒載到、畫面丟例外）都有中文的錯誤畫面可以落腳。
    // 玄關與屋內另外各有一層：壞的是房間時，外面的登入檢查與小幫手的對話不會跟著卸掉
    errorElement: routeError,
    children: [
      { path: '/login', element: <DoorRoute /> },
      {
        element: <SignedIn />,
        children: [
          { path: '/households', element: <GenkanScene />, errorElement: routeError },
          {
            element: <AtHome />,
            errorElement: routeError,
            children: [
              { path: '/today', element: <LivingScene /> },
              { path: '/plan', element: <KitchenScene /> },
              // 問卷與生成中是同一個場景的兩個網址：寫成子路由，送出時場景不會重新掛載（鍋蓋才能接著蓋上）
              { path: '/plan/:id', element: <PassboardScene />, children: [{ index: true }, { path: 'cooking' }] },
              { path: '/plan/:id/result/:version', element: <DiningScene /> },
              { path: '/pick', element: <RecipeBookScene /> },
              { path: '/pick/:id', element: <RecipeBookScene /> },
              { path: '/records', element: <FridgeScene /> },
              { path: '/family', element: <PortraitScene /> },
              { path: '/assistant', element: <AssistantPage /> },
            ],
          },
        ],
      },
      // 舊前端的網址（書籤、分享過的連結）轉到新的房間
      { path: '/history', element: <LegacyRecords tab="adopted" /> },
      { path: '/generation-history', element: <LegacyRecords tab="nutrition" /> },
      { path: '/members', element: <Navigate to="/family" replace /> },
      { path: '*', element: <Navigate to="/today" replace /> },
    ],
  },
])

export default function App() {
  return (
    <CrashBoundary>
      <QueryClientProvider client={queryClient}>
        <DepthProvider>
          <RouterProvider router={router} />
        </DepthProvider>
      </QueryClientProvider>
    </CrashBoundary>
  )
}
