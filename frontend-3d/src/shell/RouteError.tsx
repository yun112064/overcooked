/**
 * 畫面壞掉時的替代畫面：房間的程式檔沒載到（網路不穩、網站剛更新），或畫面在畫的時候丟了例外。
 * 沒有它的話會掉到路由器內建的英文錯誤頁，上面沒有任何可以按的東西。
 *
 * - <RouteError>：放在路由的 errorElement。
 * - <CrashBoundary>：包在路由器外面，接住路由器以上的例外（那一層沒有路由可以用）。
 */
import { Component, type ReactNode } from 'react'
import { Link, useLocation, useRouteError } from 'react-router'
import { isChunkLoadError } from './routeHelpers'
import styles from './RouteError.module.css'

type CrashNoticeProps = {
  error: unknown
  /** 「回客廳」的連結；人已經在客廳時不給（按了也還是同一個壞掉的畫面） */
  home?: ReactNode
}

export function CrashNotice({ error, home }: CrashNoticeProps) {
  const chunk = isChunkLoadError(error)
  return (
    <main className={styles.screen}>
      <div className={styles.card} role="alert">
        <h1>{chunk ? '這個房間沒有載入成功' : '這個畫面出了點問題'}</h1>
        <p>
          {chunk
            ? '可能是網路不穩，或網站剛更新過。重新整理通常就會好。'
            : home ? '不是你的操作造成的。重新整理試試看；還是不行的話，先回客廳。' : '不是你的操作造成的。重新整理試試看。'}
        </p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={() => window.location.reload()}>重新整理</button>
          {home}
        </div>
      </div>
    </main>
  )
}

/** 路由的 errorElement：外層的版面（登入檢查、小幫手的對話）都還在，只有壞掉的那一層換成這個畫面。 */
export function RouteError() {
  const error = useRouteError()
  const { pathname } = useLocation()
  return (
    <CrashNotice
      error={error}
      home={pathname === '/today' ? undefined : <Link to="/today" className={styles.link}>回客廳</Link>}
    />
  )
}

type BoundaryState = { failed: boolean; error: unknown }

/** 路由器以上的最後一道：這裡沒有路由，「回客廳」用整頁重新載入。 */
export class CrashBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { failed: false, error: null }

  static getDerivedStateFromError(error: unknown): BoundaryState {
    return { failed: true, error }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <CrashNotice error={this.state.error} home={<a href="/today" className={styles.link}>回客廳</a>} />
  }
}
