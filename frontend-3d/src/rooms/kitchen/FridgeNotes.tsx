/**
 * 冰箱門上的兩張便利貼（DESIGN.md 5.2）：「最近採用」→ 冰箱門的已採用紀錄；「近 7 天」→ 營養與採購。
 * 資料由 useFridgeNotes() 讀好傳進來，因為物件列也要顯示同一份狀態。
 *
 * 未知的數字畫成虛線的「未知」，不寫成 0；成本是「按比例成本」，和整包花費是兩回事，這裡不相加。
 */
import { withinTarget } from '../../api/nutrition'
import { Hotspot } from '../../camera/Room'
import { formatMoney } from '../../components/formatMoney'
import { ratioText } from '../../components/nutritionVerdict'
import { UnknownValue } from '../../components/UnknownValue'
import { NOTE_IDS, adoptedLabel, dayText, weekLabel } from './fridgeNoteText'
import type { AdoptedNote, WeekNote } from './useFridgeNotes'
import styles from './KitchenScene.module.css'

// 畫稿座標：冰箱下門 1230–1530 × 264–858，把手在 1258–1274，所以便利貼從 1296 開始貼
const ADOPTED = { x: 1366, y: 300, width: 136 }
const WEEK = { x: 1410, y: 486, width: 212 }
/** 便利貼上最多寫幾餐，其餘用「還有 N 餐」帶過 */
const MAX_LINES = 2

type FridgeNotesProps = {
  adopted: AdoptedNote
  week: WeekNote
  onOpenAdopted: () => void
  onOpenWeek: () => void
}

export function FridgeNotes({ adopted, week, onOpenAdopted, onOpenWeek }: FridgeNotesProps) {
  return (
    <>
      <Hotspot
        id={NOTE_IDS.adopted} label={adoptedLabel(adopted)} {...ADOPTED} k={0.4}
        className={`${styles.note} ${styles.noteYellow}`} onClick={onOpenAdopted}
      >
        <i className={styles.magnet} aria-hidden="true" />
        <b>最近採用</b>
        {adopted.status === 'loading' && <span>讀取中…</span>}
        {adopted.status === 'error' && <span>讀不到紀錄，點這裡到冰箱門再試。</span>}
        {adopted.status === 'empty' && <span>還沒有採用過菜單，採用後會貼在這裡。</span>}
        {adopted.status === 'ready' && (
          <>
            <span className={styles.noteDay}>{dayText(adopted.date)}</span>
            {adopted.lines === null && <span>{adopted.linesError ? '讀不到這天的菜色。' : '讀取中…'}</span>}
            {adopted.lines?.slice(0, MAX_LINES).map((line, i) => (
              <span key={i} className={styles.noteLine}><em>{line.meal}</em>{line.dishes || '（沒有菜名）'}</span>
            ))}
            {adopted.lines && adopted.lines.length > MAX_LINES && <small>還有 {adopted.lines.length - MAX_LINES} 餐</small>}
          </>
        )}
      </Hotspot>

      <Hotspot
        id={NOTE_IDS.week} label={weekLabel(week)} {...WEEK} k={0.4}
        className={`${styles.note} ${styles.noteWhite}`} onClick={onOpenWeek}
      >
        <i className={`${styles.magnet} ${styles.magnetGreen}`} aria-hidden="true" />
        <b>近 7 天</b>
        {week.status === 'loading' && <span>讀取中…</span>}
        {week.status === 'error' && <span>讀不到近 7 天的統計，點這裡到冰箱門再試。</span>}
        {week.status === 'ready' && week.meals === 0 && <span>近 7 天沒有已採用的餐食，採用菜單後這裡會有統計。</span>}
        {week.status === 'ready' && week.meals > 0 && (
          <>
            <span className={styles.stat}><span>採用</span><strong>{week.meals} 餐</strong></span>
            <span className={styles.stat}>
              <span>熱量達成</span>
              {week.energyReach === null ? <UnknownValue /> : (
                <strong>
                  {ratioText('energy_kcal', week.energyReach)}
                  {!withinTarget('energy_kcal', week.energyReach) && <em>超出</em>}
                </strong>
              )}
            </span>
            <span className={styles.stat}>
              <span>平均每餐成本</span>
              {week.averageCost === null ? <UnknownValue /> : <strong>{formatMoney(week.averageCost)}</strong>}
            </span>
            <small>
              按比例成本，不是整包花費
              {week.unknownCostMeals > 0 && `；${week.unknownCostMeals} 餐價格未知，沒算進平均`}
            </small>
          </>
        )}
      </Hotspot>
    </>
  )
}
