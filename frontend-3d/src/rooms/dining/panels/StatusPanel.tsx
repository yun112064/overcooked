/**
 * 「這桌的狀況」：能不能採用、為什麼、有什麼要決定（舊前端結果頁上半部那一串提示）。
 *
 * 採用資格由後端判定——有結果不代表可以採用。這裡只把後端給的原因、待決定事項與提醒照實列出來。
 * 只能看的桌子（問卷已採用、或這一版已經被新版本取代）不給決定與重試的按鈕：
 * 後端不擋這兩種情況，按了會真的在已採用的問卷或舊版本上寫入一筆。
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import type { DecisionKind, MealResult, MealResultSummary } from '../../../api/types'
import { Button } from '../../../components/Button'
import { Notice } from '../../../components/Notice'
import { Spinner } from '../../../components/Spinner'
import { StatusChip } from '../../../components/StatusChip'
import { DECISION_LABELS, previousDayText } from '../resultText'
import { adoptedResultVersion, type Adoption, type ResultFlags } from '../tableModel'
import styles from './panels.module.css'

type StatusDetailProps = {
  r: MealResult
  flags: ResultFlags
  /** 已採用紀錄的網址 */
  recordsTo: string
  /** 這一餐是哪一天哪一餐（例如「11/14 晚餐」）；問卷還沒讀到是 null */
  mealLabel: string | null
  /** 這份問卷的採用和這一版的關係：只有確定採用的就是這一版，才寫「已採用」 */
  adoption: Adoption
  /** 這份問卷所有的結果版本（新到舊）；重新查價、重試小知識都會多一版 */
  versions?: readonly MealResultSummary[]
  /** 某一版的網址 */
  versionTo: (version: number) => string
  /** 問卷正在重新生成：這時不能決定或重試 */
  cooking: boolean
  decidePending: boolean
  retryPricePending: boolean
  onDecide: (kind: DecisionKind) => void
  onRetryPrice: () => void
  /** 打開「全家營養」（調整建議在那裡） */
  onShowFamily: () => void
  /** 決定、重試、採用失敗的訊息 */
  error?: ReactNode
}

export function StatusDetail({
  r, flags, recordsTo, mealLabel, adoption, versions = [], versionTo, cooking, decidePending, retryPricePending,
  onDecide, onRetryPrice, onShowFamily, error,
}: StatusDetailProps) {
  const el = r.eligibility
  const { confirmed, readOnly, newerVersion, priceRetrying } = flags
  const adoptedVersion = adoptedResultVersion(r, adoption)
  const latestVersion = versions[0]?.result_version
  const recordsLink = <Link className={styles.linkButton} to={recordsTo}>查看紀錄</Link>
  const priceRetry = r.retries?.price ?? null
  const adjust = r.sodium_adjustment
  const highSodium = r.dishes.filter((d) => d.high_sodium)
  const sodiumThreshold = r.nutrition.daily_reference?.high_sodium_dish_mg
  const suggestionCount = r.nutrition.suggestions?.length ?? 0
  return (
    <div className={styles.stack}>
      <p className={styles.tags}>
        <StatusChip tone={r.mode === 'ai_generated_menu' ? 'info' : 'neutral'}>{r.mode_label}</StatusChip>
        <StatusChip>第 {r.result_version} 版</StatusChip>
        {r.derived_from_result_version !== null && (
          <StatusChip>由第 {r.derived_from_result_version} 版{r.knowledge_retry ? '重試知識說明' : '重新查價'}</StatusChip>
        )}
        {mealLabel && <span>{mealLabel}</span>}
      </p>
      {r.fallback_message && <Notice>{r.fallback_message}</Notice>}

      {adoption.kind === 'other' ? (
        <Notice tone="warn" action={<Link className={styles.linkButton} to={versionTo(adoption.version)} replace>看第 {adoption.version} 版</Link>}>
          這份問卷採用的是第 {adoption.version} 版。現在看的第 {r.result_version} 版沒有被採用，只能看，不能再改或重算。
        </Notice>
      ) : adoption.kind === 'menu' ? (
        <Notice tone="warn" action={recordsLink}>
          這份問卷採用的是資料庫菜單，不是這一桌。這一桌沒有被採用，只能看，不能再改或重算。
        </Notice>
      ) : adoption.kind === 'this' ? (
        <Notice tone="success" action={recordsLink}>
          已採用並保存為已採用餐食紀錄（不代表實際已食用）。這一桌只能看，不能再改或重算。
        </Notice>
      ) : confirmed ? (
        // 還不知道採用的是哪一份：不說這一桌被採用
        <Notice action={recordsLink}>
          這份問卷已經採用了，所以這一桌只能看，不能再改或重算。採用的是不是這一桌，還沒有讀到；可以到已採用紀錄確認。
        </Notice>
      ) : newerVersion !== null ? (
        <Notice action={<Link className={styles.linkButton} to={versionTo(newerVersion)} replace>看最新的第 {newerVersion} 版</Link>}>
          這是第 {r.result_version} 版，已經有比較新的第 {newerVersion} 版。舊的版本只能看；要採用、決定或重新查價，請到最新的那一版。
        </Notice>
      ) : el.adoptable ? (
        <Notice tone="success">
          符合採用條件{el.price_exception_accepted ? '（部分價格未知，成本只計已知部分）' : ''}。
        </Notice>
      ) : (
        <Notice tone="warn" title="目前還不能採用">
          {el.blocks.length > 0
            ? <ul className={styles.bullets}>{el.blocks.map((b) => <li key={b.code}>{b.message}</li>)}</ul>
            : '原因見下方。'}
        </Notice>
      )}

      {cooking && !readOnly && <Notice>這份問卷正在重新煮；煮好之前不能採用、決定或重試。</Notice>}

      {flags.dayConflict && (
        <Notice tone="error">
          {r.previous_day_check?.message}（重複：{r.previous_day_check?.conflicting_dishes?.join('、')}）
        </Notice>
      )}

      {!readOnly && el.pending_decisions.map((d) => (
        <Notice key={d.kind} title="需要你決定" action={(
          <span className={styles.buttonRow}>
            {d.kind === 'accept_missing_price' && (
              <Button busy={retryPricePending} disabled={priceRetrying || cooking} onClick={onRetryPrice}>
                {priceRetrying ? '查價中…' : '重新查價'}
              </Button>
            )}
            <Button variant="primary" busy={decidePending} disabled={cooking} onClick={() => onDecide(d.kind)}>{DECISION_LABELS[d.kind]}</Button>
          </span>
        )}>
          {d.message}
        </Notice>
      ))}

      {flags.canRetryPrice && !priceRetrying && (
        <Notice action={<Button busy={retryPricePending} disabled={cooking} onClick={onRetryPrice}>重新查價</Button>}>
          {flags.missingPriceNames.join('、')} 查無價格，成本只計已知部分；不影響採用，也可以重新查價。
        </Notice>
      )}
      {priceRetrying && (
        <Notice>
          <Spinner label="正在向價格來源查詢缺價食材（低頻率查詢，可能需要幾分鐘）；完成後會出現新版本。" />
        </Notice>
      )}
      {priceRetry?.status === 'failed' && (
        <Notice tone="warn">
          上次重新查價沒有完成：{priceRetry.error?.message ?? '原因未知'}。原結果不變{readOnly ? '。' : '，可以直接採用或稍後再試。'}
        </Notice>
      )}
      {error}

      {r.nutrition.quantities_verified === false && (
        <Notice tone="warn">這一桌的用量尚未通過營養驗證，僅供查看與估算成本，不可採用。</Notice>
      )}
      {suggestionCount > 0 && (
        <Notice action={<Button onClick={onShowFamily}>看全家營養</Button>}>
          營養有 {suggestionCount} 則調整建議，寫在「全家營養」裡。
        </Notice>
      )}

      {adjust && Number(adjust.seasoning_ratio) < 1 && (
        <Notice>
          為了讓鈉接近這一餐的建議量（每人份約 {Math.round(Number(adjust.budget_per_n))} mg），
          鹽、醬油等鹹味調味料已減為原本的 {Math.round(Number(adjust.seasoning_ratio) * 100)}%
          （每人份鈉約 {Math.round(Number(adjust.sodium_per_n_before))} → {Math.round(Number(adjust.sodium_per_n_after))} mg）。
          {!adjust.within_budget && '調味料減到一半仍超過建議量，主要來自食材或主食本身的鈉。'}
        </Notice>
      )}
      {highSodium.length > 0 && (
        <Notice tone="warn">
          <b>{highSodium.map((d) => d.dish_name).join('、')}</b> 已依此結果的營養依據標示為高鈉。
          {sodiumThreshold != null && `每人份達 ${sodiumThreshold} mg。`}
          {!readOnly && '這只是提醒，仍可照常採用；如果想換菜，可以回到問卷調整（例如指定其他菜色）後重新生成。'}
        </Notice>
      )}

      <section>
        <h3>避免重複的菜</h3>
        <p>{previousDayText(r.previous_day)}</p>
        {r.previous_day.specified_repeats.length > 0 && (
          <Notice>
            {r.previous_day.specified_repeats.join('、')} 與昨天或今天其他餐重複，因為是你指定的菜所以保留；其他限制（過敏、營養等）仍照常檢查。
          </Notice>
        )}
      </section>

      {versions.length > 1 && (
        <section>
          <h3>這份問卷的其他版本</h3>
          <ul className={styles.versions}>
            {versions.map((v) => (
              <li key={v.result_version}>
                {v.result_version === r.result_version ? (
                  <span aria-current="true"><b>第 {v.result_version} 版</b>（現在看的）</span>
                ) : (
                  <Link to={versionTo(v.result_version)} replace>第 {v.result_version} 版</Link>
                )}
                <small>
                  {v.mode_label}
                  {/* 只有最新的那一版能採用；舊版就算後端說資格符合，這裡也只能看 */}
                  {adoptedVersion === v.result_version ? '・已採用'
                    : confirmed ? null
                      : v.result_version !== latestVersion ? '・舊版，只能看'
                        : v.adoptable ? '・可以採用' : '・還不能採用'}
                </small>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
