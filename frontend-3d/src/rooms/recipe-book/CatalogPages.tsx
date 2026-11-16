/**
 * 第二步「挑菜」的畫面：左頁清單（一頁 10 道）、右頁細節、右緣書籤、托盤、分類面板。
 * 規則和舊前端的 RecipeCatalogPicker 相同：同一個分類內符合任一個即可、不同分類要同時符合；
 * 含成員過敏或不吃的食材的菜不能選；最多 10 道。狀態都在 RecipeBookScene，這裡只負責畫。
 */
import type { ReactNode } from 'react'
import type { CatalogDish, CatalogFacet, RecipeCatalog } from '../../api/types'
import { Placed } from '../../camera/Room'
import { Button } from '../../components/Button'
import { ErrorNotice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import {
  FACETS, MAX_DISHES, PAGE_SIZE, canToggle, clampPage, dishTags, filterCount, pageCount, pageOf,
  type FacetKey, type Filters,
} from './bookLogic'
import { TABS } from './bookFraming'
import styles from './RecipeBookScene.module.css'

type ListPageProps = {
  loading: boolean
  error: unknown
  onRetry: () => void
  retrying: boolean
  /** 「類型」的選項 */
  categories: readonly CatalogFacet[]
  /** 右緣的書籤看不到（手機、平板、視窗太扁）：改在頁首放一排類型按鈕 */
  inlineCategories: boolean
  filters: Filters
  onToggleFilter: (key: FacetKey, value: string) => void
  /** 「全部」：只清掉類型，其他分類的勾選不動 */
  onAllCategories: () => void
  onClearFilters: () => void
  onOpenFilters: () => void
  keyword: string
  onKeyword: (value: string) => void
  /** 篩選後的全部菜（還沒分頁） */
  shown: readonly CatalogDish[]
  page: number
  onPage: (page: number) => void
  chosen: readonly string[]
  viewingId: string | null
  onView: (dish: CatalogDish) => void
  onToggle: (dish: CatalogDish) => void
  readOnly: boolean
}

export function ListPage({
  loading, error, onRetry, retrying, categories, inlineCategories, filters,
  onToggleFilter, onAllCategories, onClearFilters, onOpenFilters,
  keyword, onKeyword, shown, page, onPage, chosen, viewingId, onView, onToggle, readOnly,
}: ListPageProps) {
  const current = clampPage(page, shown.length)
  const pages = pageCount(shown.length)
  const rows = pageOf(shown, current)
  const active = filterCount(filters)

  return (
    <>
      <header className={styles.listHead}>
        <div className={styles.searchRow}>
          <label className={styles.search}>
            <span className="visually-hidden">搜尋菜名或食材</span>
            <input
              type="search" value={keyword} placeholder="搜尋菜名或食材" enterKeyHint="search"
              onChange={(event) => onKeyword(event.target.value)}
            />
          </label>
          <button
            type="button" className={styles.ink} onClick={onOpenFilters}
            aria-label={active > 0 ? `分類，已選 ${active} 項` : '分類'}
          >
            分類{active > 0 && <em aria-hidden="true">{active}</em>}
          </button>
        </div>
        {inlineCategories && categories.length > 0 && (
          <div className={styles.filterRow} role="group" aria-label="類型">
            <button type="button" aria-pressed={filters.category.length === 0} onClick={onAllCategories}>全部</button>
            {categories.map((facet) => (
              <button
                key={facet.value} type="button" aria-pressed={filters.category.includes(facet.value)}
                onClick={() => onToggleFilter('category', facet.value)}
              >
                {facet.value}<small>{facet.count}</small>
              </button>
            ))}
          </div>
        )}
        <p className={styles.count} aria-live="polite">
          符合 {shown.length} 道・已選 {chosen.length}／{MAX_DISHES} 道{readOnly && '・只能看，不能改'}
          {active > 0 && <button type="button" className={styles.textButton} onClick={onClearFilters}>清除分類</button>}
        </p>
      </header>

      <div className={styles.scroll}>
        {loading && <Spinner label="正在翻食譜書…" />}
        {!!error && <ErrorNotice error={error} onRetry={onRetry} retrying={retrying} />}
        {!loading && !error && shown.length === 0 && (
          <p className={styles.paperNote}>沒有符合的菜，請減少勾選的分類，或換個關鍵字。</p>
        )}
        <ol className={styles.rows} start={current * PAGE_SIZE + 1}>
          {rows.map((dish, index) => {
            const checked = chosen.includes(dish.dish_name)
            const blocked = dish.conflicts.length > 0
            return (
              <li
                key={dish.standard_dish_id}
                className={`${styles.row} ${checked ? styles.rowPicked : ''} ${blocked ? styles.rowBlocked : ''}`}
              >
                <label className={styles.rowCheck}>
                  <input
                    type="checkbox" checked={checked} aria-label={`${dish.dish_name}，夾進托盤`}
                    disabled={readOnly || !canToggle(dish, checked, chosen.length)}
                    onChange={() => onToggle(dish)}
                  />
                </label>
                <button
                  type="button" className={styles.rowMain} onClick={() => onView(dish)}
                  aria-current={viewingId === dish.standard_dish_id ? 'true' : undefined}
                  aria-label={`看${dish.dish_name}的細節`}
                >
                  <span className={styles.rowNo} aria-hidden="true">{current * PAGE_SIZE + index + 1}</span>
                  <b>{dish.dish_name}</b>
                  {blocked
                    ? <small className={styles.rowWarn}>不能選：含{dish.conflicts.join('、')}</small>
                    : <small>{[dish.cuisine, dish.category].filter(Boolean).join('・')}</small>}
                </button>
              </li>
            )
          })}
        </ol>
      </div>

      <footer className={`${styles.pageFoot} ${styles.pager}`}>
        <button type="button" className={styles.ink} disabled={current === 0} onClick={() => onPage(current - 1)}>
          <span aria-hidden="true">‹ </span>上一頁
        </button>
        <span aria-live="polite">第 {current + 1}／{pages} 頁</span>
        <button type="button" className={styles.ink} disabled={current >= pages - 1} onClick={() => onPage(current + 1)}>
          下一頁<span aria-hidden="true"> ›</span>
        </button>
      </footer>
    </>
  )
}

type DetailPageProps = {
  dish: CatalogDish | null
  checked: boolean
  chosenCount: number
  readOnly: boolean
  onToggle: (dish: CatalogDish) => void
  /** 鎖定原因、儲存失敗等訊息 */
  notice?: ReactNode
  stapleName: string
}

export function DetailPage({ dish, checked, chosenCount, readOnly, onToggle, notice, stapleName }: DetailPageProps) {
  if (!dish) {
    return (
      <>
        <header className={styles.pageHead}>
          <h2>挑一道菜看看</h2>
          <p>點左頁的菜名，這裡會寫出它的食材。</p>
        </header>
        <div className={styles.scroll}>
          {notice}
          <p className={styles.caption}>
            打勾就是夾進托盤，最多 {MAX_DISHES} 道。沒挑飯、麵類的菜時會配一份{stapleName}；按確定後會預覽依人數調整的份量。
          </p>
        </div>
      </>
    )
  }

  const blocked = dish.conflicts.length > 0
  const full = chosenCount >= MAX_DISHES && !checked
  const disabled = readOnly || !canToggle(dish, checked, chosenCount)
  return (
    <>
      <header className={styles.pageHead}>
        <h2>{dish.dish_name}</h2>
        <p>{dishTags(dish).join('・')}</p>
      </header>
      <div className={styles.scroll}>
        {notice}
        {blocked && (
          <p className={styles.conflict}>
            含過敏或不吃的食材：{dish.conflicts.join('、')}。這道菜不能選。
          </p>
        )}
        <h3>食材</h3>
        <p>{dish.ingredients.length > 0 ? dish.ingredients.join('、') : '食譜庫沒有列出這道菜的食材。'}</p>
        <h3>份量與營養</h3>
        <p className={styles.caption}>
          食譜庫的清單沒有單一道菜的營養數字。按「確定」後會依人數調整份量，並算出整餐的營養（缺資料的項目會標「未知」）。
          沒挑飯、麵類的菜時會配一份{stapleName}。
        </p>
      </div>
      <footer className={styles.pageFoot}>
        <button
          type="button" className={`${styles.ink} ${checked ? styles.inkOn : styles.inkPrimary}`}
          disabled={disabled} aria-pressed={checked} onClick={() => onToggle(dish)}
        >
          {checked ? '✓ 已夾進托盤（點一下拿出來）' : '夾進托盤'}
        </button>
        {!checked && blocked && <span>含不能吃的食材，不能選</span>}
        {!blocked && full && <span>托盤已經放滿 {MAX_DISHES} 道</span>}
      </footer>
    </>
  )
}

type BookmarkTabsProps = {
  /** 放在書籤上的類型（最多四個；其餘在「更多」的面板裡） */
  values: readonly string[]
  selected: readonly string[]
  onToggle: (value: string) => void
  onAll: () => void
  onMore: () => void
  /** 類型以外的分類目前勾了幾項（顯示在「更多」上） */
  otherCount: number
}

/** 右緣的彩色書籤＝「類型」篩選。書籤看不到的版面由場景決定不畫（改用頁首那一排）。 */
export function BookmarkTabs({ values, selected, onToggle, onAll, onMore, otherCount }: BookmarkTabsProps) {
  const tabs: { key: string; label: string; name: string; pressed?: boolean; badge?: number; onClick: () => void }[] = [
    { key: 'all', label: '全部', name: '類型：全部', pressed: selected.length === 0, onClick: onAll },
    ...values.map((value) => ({
      key: `v-${value}`, label: value, name: `類型：${value}`, pressed: selected.includes(value), onClick: () => onToggle(value),
    })),
    {
      key: 'more', label: '更多', name: otherCount > 0 ? `更多分類，已選 ${otherCount} 項` : '更多分類',
      badge: otherCount > 0 ? otherCount : undefined, onClick: onMore,
    },
  ]
  return (
    <>
      {tabs.map((tab, index) => (
        <Placed key={tab.key} x={TABS.x} y={TABS.y + TABS.step * index} width={TABS.width} k={0.4} className={styles.tabSlot}>
          <button type="button" className={styles.tab} aria-pressed={tab.pressed} aria-label={tab.name} onClick={tab.onClick}>
            <span>{tab.label}</span>
            {tab.badge !== undefined && <i aria-hidden="true">{tab.badge}</i>}
          </button>
        </Placed>
      ))}
    </>
  )
}

type TrayPicksProps = {
  chosen: readonly string[]
  conflicted: ReadonlySet<string>
  readOnly: boolean
  /** 確定按鈕停用：儲存中、唯讀、或主食清單還沒讀到 */
  disabled: boolean
  saving: boolean
  onRemove: (name: string) => void
  onConfirm: () => void
}

/** 托盤：已選的菜（點一下拿出來）和「確定」。 */
export function TrayPicks({ chosen, conflicted, readOnly, disabled, saving, onRemove, onConfirm }: TrayPicksProps) {
  return (
    <>
      {chosen.length === 0 ? (
        <p className={styles.trayHint}>勾選想吃的菜（最多 {MAX_DISHES} 道）</p>
      ) : (
        <ul className={styles.trayCards} aria-label="托盤裡的菜">
          {chosen.map((name) => (
            <li key={name}>
              <button
                type="button" className={conflicted.has(name) ? styles.cardWarn : undefined}
                disabled={readOnly} onClick={() => onRemove(name)}
                aria-label={`把${name}拿出托盤${conflicted.has(name) ? '（含不能吃的食材）' : ''}`}
              >
                {name}<i aria-hidden="true">✕</i>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button" className={styles.trayGo} disabled={disabled || chosen.length === 0}
        aria-busy={saving || undefined} onClick={onConfirm}
      >
        {saving ? '儲存中…' : `確定（${chosen.length} 道）`}
      </button>
    </>
  )
}

type FilterPanelProps = {
  facets: RecipeCatalog['facets']
  filters: Filters
  matches: number
  onToggle: (key: FacetKey, value: string) => void
  onClear: () => void
  onClose: () => void
}

/** 四種分類都在這裡（書籤與頁首那一排只放得下「類型」）。 */
export function FilterPanel({ facets, filters, matches, onToggle, onClear, onClose }: FilterPanelProps) {
  return (
    <Panel
      title="分類" side="right" onClose={onClose}
      footer={(
        <>
          <Button disabled={filterCount(filters) === 0} onClick={onClear}>清除分類</Button>
          <Button variant="primary" onClick={onClose}>看 {matches} 道菜</Button>
        </>
      )}
    >
      <p className={styles.panelLead}>同一個分類裡符合任一個就算；不同分類要同時符合。</p>
      {FACETS.map(({ key, label }) => (
        <fieldset key={key} className={styles.facet}>
          <legend>{label}</legend>
          {facets[key].length === 0 && <span className={styles.muted}>沒有可以選的項目</span>}
          {facets[key].map((facet) => (
            <button
              key={facet.value} type="button" aria-pressed={filters[key].includes(facet.value)}
              onClick={() => onToggle(key, facet.value)}
            >
              {facet.value}<small>{facet.count}</small>
            </button>
          ))}
        </fieldset>
      ))}
    </Panel>
  )
}
