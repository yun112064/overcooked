/**
 * 營養的兩個面板（舊前端的 ResultPersonNutrition 與 NutritionTable）：
 * - <PersonDetail>：點飯碗——這個人這一餐吃到多少，對照他的每日建議；便當另列。
 * - <FamilyDetail>：點家庭合計立牌——整餐熱量與蛋白質對目標、三大營養素比例、調整建議、整餐合計表。
 *
 * 未知的值寫「未知」並附已知的部分，不畫長條、不當成 0。
 */
import { useState } from 'react'
import { NUTRIENTS, amount, nutrientText, type Nutrition } from '../../../api/resultNutrition'
import { MEAL_LABELS, type ResultPersonNutrition, type ResultPersonPortion } from '../../../api/types'
import { Button } from '../../../components/Button'
import { Notice } from '../../../components/Notice'
import { NutritionBadge } from '../../../components/NutritionBadge'
import { StatusChip } from '../../../components/StatusChip'
import { UnknownValue } from '../../../components/UnknownValue'
import { ratioOf } from '../../../components/nutritionVerdict'
import { barTone, dailyPercent, dailyRef, fmt, macroRows, suggestionText } from '../resultText'
import { portionRatios } from '../tableModel'
import styles from './panels.module.css'

const KIND_LABELS = { dining: '用餐', bento: '便當' } as const

type Daily = ResultPersonNutrition['daily']

/** 這一份占每日熱量的比例（與建議比例並列）。 */
function EnergyLine({ portion, daily, label }: { portion: ResultPersonPortion; daily: Daily; label: string }) {
  const mealRatio = Number(portion.meal_ratio)
  const energy = portion.intake.energy_kcal != null ? Number(portion.intake.energy_kcal) : null
  const energyDaily = daily.energy_kcal?.value ? Number(daily.energy_kcal.value) : null
  if (energy === null || !energyDaily) return null
  return (
    <p className={styles.energyLine}>
      {label}約占每日熱量 <b>{Math.round((energy / energyDaily) * 100)}%</b>
      <small>建議約 {Math.round(mealRatio * 100)}%</small>
    </p>
  )
}

/** 七項營養素：攝取量、每日建議與占比長條（刻度是這一餐應占每日的比例）。 */
function NutrientRows({ portion, daily }: { portion: ResultPersonPortion; daily: Daily }) {
  const mealRatio = Number(portion.meal_ratio)
  return (
    <dl className={styles.nutrients}>
      {NUTRIENTS.map(({ key, label, unit, required }) => {
        const raw = portion.intake[key]
        const value = raw !== null && raw !== undefined ? Number(raw) : null
        const reference = daily[key]
        const ref = dailyRef(reference, unit)
        const partial = portion.known_partial[key]
        const bar = ref && value !== null && reference ? barTone(reference.kind, value / ref.base, mealRatio) : null
        return (
          <div key={key} className={styles.nutrient}>
            <dt className={required ? styles.required : undefined}>{label}</dt>
            <dd>
              <span className={styles.nutrientHead}>
                {value !== null
                  ? <b>{fmt(value, unit)} {unit}</b>
                  : <UnknownValue detail={partial !== undefined ? `已知 ${fmt(Number(partial), unit)} ${unit}` : undefined} />}
                {ref && (
                  <small title={[reference?.label, reference?.caveat].filter(Boolean).join('；')}>
                    每日 {ref.text}{value !== null && `・${dailyPercent(value, ref)}`}
                  </small>
                )}
              </span>
              {bar && ref && value !== null && (
                <span
                  className={styles.bar} role="img"
                  aria-label={`占每日建議 ${Math.round((value / ref.base) * 100)}%，這一餐建議約占 ${Math.round(mealRatio * 100)}%`}
                >
                  <i className={styles[bar.tone]} style={{ width: `${Math.min(value / ref.base, 1) * 100}%` }} />
                  <u style={{ left: `${Math.min(mealRatio, 1) * 100}%` }} />
                </span>
              )}
              {bar?.note && <small className={styles.barNote}>{bar.note}</small>}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

/** 便當另列（隔天午餐），預設收合；只帶便當不在家吃時直接展開。 */
function BentoSection({ person, open: initiallyOpen }: { person: ResultPersonNutrition; open: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const bento = person.bento
  if (!bento) return null
  const meal = MEAL_LABELS[bento.meal] ?? '午餐'
  return (
    <section className={initiallyOpen ? undefined : styles.bento}>
      <p className={styles.tags}>
        <StatusChip>便當・{meal}</StatusChip>
        <span>食量 {Number(bento.n)}n・不計入這一餐</span>
      </p>
      <EnergyLine portion={bento} daily={person.daily} label={`便當（${meal}）`} />
      {!initiallyOpen && (
        <Button variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? '收起便當營養' : '查看便當營養'}
        </Button>
      )}
      {open && <NutrientRows portion={bento} daily={person.daily} />}
    </section>
  )
}

function Footnote({ nutrition }: { nutrition: Nutrition }) {
  const src = nutrition.daily_reference?.source
  return (
    <p className={styles.footnote}>
      每人應吃的量依各自的當餐目標分配整餐（盛菜時大人、小孩份量不同）；帶便當的份另外列出，不計入這一餐；長條上的刻度是這一餐建議占每日的比例
      （早餐 30%、午餐 30%、晚餐 40%，再乘上食量 n）。熱量與蛋白質的每日建議：成人依國健署公式，未滿 19 歲依國健署分齡表；
      其餘依{src ? <a href={src.url} target="_blank" rel="noreferrer">{src.publisher}建議</a> : '國健署建議'}由每日熱量推算；
      糖為 TFDA 總糖，對照的是添加糖上限，僅供參考。
    </p>
  )
}

export function PersonDetail({ person, nutrition, onShowFamily }: {
  person: ResultPersonNutrition; nutrition: Nutrition; onShowFamily: () => void
}) {
  // 舊結果沒有 dining 欄：用餐與便當當時已合併計算，照舊顯示
  const dining = person.dining ?? true
  const judged = dining ? person : person.bento ?? person
  const { energy, protein } = portionRatios(judged, person.daily)
  return (
    <div className={styles.stack}>
      <p className={styles.tags}>
        {person.kinds.map((k) => <StatusChip key={k}>{KIND_LABELS[k]}</StatusChip>)}
        {dining && <span>食量 {Number(person.n)}n</span>}
      </p>
      <p className={styles.badges}>
        <NutritionBadge nutrient="energy_kcal" ratio={energy} />
        <NutritionBadge nutrient="protein_g" ratio={protein} />
      </p>
      <p className={styles.hint}>
        對照的是{dining ? '這一餐' : '便當'}的建議量（每日建議 × 這一份該占的比例）：熱量不超過 110%、蛋白質不低於 90% 才算通過。
        缺資料時是「計算不完整」，不畫飯，也不當成 0。能不能採用看的是整餐，不是個人。
      </p>
      {nutrition.quantities_verified === false && (
        <Notice tone="warn">以下用量尚未通過營養驗證，僅供查看與估算成本，不可採用。</Notice>
      )}
      {dining ? (
        <>
          <EnergyLine portion={person} daily={person.daily} label="這一餐" />
          <NutrientRows portion={person} daily={person.daily} />
          <BentoSection person={person} open={false} />
        </>
      ) : (
        <>
          <p>這一餐不在家吃，只帶便當。</p>
          <BentoSection person={person} open />
        </>
      )}
      <Footnote nutrition={nutrition} />
      <Button onClick={onShowFamily}>看全家合計</Button>
    </div>
  )
}

/** 整餐三大營養素的熱量比例：一條堆疊長條＋每項的百分比、克數與參考範圍（文字並列，不只靠顏色）。 */
function MacroBar({ nutrition }: { nutrition: Nutrition }) {
  const rows = macroRows(nutrition)
  if (!rows) return null
  return (
    <section>
      <h3>三大營養素熱量比例</h3>
      <div className={styles.macroBar} role="img" aria-label={rows.map((r) => `${r.label} ${Math.round(r.percent)}%`).join('、')}>
        {rows.map((r) => <i key={r.key} className={styles[r.tone]} style={{ width: `${r.percent}%` }} />)}
      </div>
      <ul className={styles.macroList}>
        {rows.map((r) => (
          <li key={r.key}>
            <b><i className={styles[r.tone]} aria-hidden="true" />{r.label} {Math.round(r.percent)}%</b>
            <small className={r.off ? styles.off : undefined}>
              {Math.round(r.grams)} g・建議 {r.range[0]}–{r.range[1]}%{r.off ? `・${r.off}` : ''}
            </small>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * 表格裡的一格：未知（「未知」或「未知（已知 X，缺：A、B）」）改用虛線的未知樣式，不和已知的數字長得一樣。
 * 文字是 api/resultNutrition 給的，這裡只換樣式、不改內容。
 */
function Cell({ text }: { text: string }) {
  if (!text.startsWith('未知')) return text
  const detail = /^未知（(.*)）$/.exec(text)?.[1]
  return <UnknownValue detail={detail} />
}

/** 整餐營養表：目標、估算、差距皆為整餐值。熱量與蛋白質有目標，其餘只顯示。 */
function WholeMealTable({ nutrition }: { nutrition: Nutrition }) {
  const n = nutrition.total_n
  return (
    <section>
      <h3>整餐合計<small>{n !== null && n !== undefined ? `共 ${Number(n)} 份` : '整餐'}</small></h3>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th scope="col">項目</th><th scope="col">目標</th><th scope="col">估算</th><th scope="col">差距</th></tr></thead>
          <tbody>
            {NUTRIENTS.map(({ key, label, unit, required }) => (
              <tr key={key}>
                <th scope="row" className={required ? styles.required : undefined}>{label}</th>
                <td>{required ? <Cell text={amount(nutrition.target?.[key as 'energy_kcal' | 'protein_g'], unit)} /> : '—'}</td>
                <td><Cell text={nutrientText(nutrition, key, unit)} /></td>
                <td>{required ? <Cell text={amount(nutrition.gap?.[key as 'energy_kcal' | 'protein_g'], unit)} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** 「目標 620 kcal・+3%」；沒有目標時不寫。 */
function targetNote(value: string | null, target: string | undefined, unit: string): string | null {
  if (target === undefined || !Number(target)) return null
  const goal = Number(target)
  if (value === null) return `目標 ${Math.round(goal)} ${unit}`
  const diff = Number(value) / goal - 1
  return `目標 ${Math.round(goal)} ${unit}・${diff >= 0 ? '+' : ''}${Math.round(diff * 100)}%`
}

export function FamilyDetail({ nutrition, dishCount, stapleLabel }: {
  nutrition: Nutrition; dishCount: number; stapleLabel: string | null
}) {
  const suggestions = nutrition.suggestions ?? []
  const totalN = Number(nutrition.total_n ?? 1) || 1
  const energy = nutrition.energy_kcal
  const protein = nutrition.protein_g
  const energyNote = targetNote(energy, nutrition.target?.energy_kcal, 'kcal')
  const proteinNote = targetNote(protein, nutrition.target?.protein_g, 'g')
  return (
    <div className={styles.stack}>
      <dl className={styles.stats}>
        <div>
          <dt>整餐熱量</dt>
          <dd>{energy === null ? <UnknownValue /> : <><b>{Math.round(Number(energy))}</b> kcal</>}</dd>
          {energyNote && <dd className={styles.statNote}>{energyNote}</dd>}
        </div>
        <div>
          <dt>整餐蛋白質</dt>
          <dd>{protein === null ? <UnknownValue /> : <><b>{Number(protein).toFixed(1)}</b> g</>}</dd>
          {proteinNote && <dd className={styles.statNote}>{proteinNote}</dd>}
        </div>
        <div>
          <dt>菜數</dt>
          <dd><b>{dishCount}</b> 道</dd>
          {stapleLabel && <dd className={styles.statNote}>主食：{stapleLabel}</dd>}
        </div>
      </dl>
      <p className={styles.badges}>
        <NutritionBadge nutrient="energy_kcal" ratio={ratioOf(energy, nutrition.target?.energy_kcal)} />
        <NutritionBadge nutrient="protein_g" ratio={ratioOf(protein, nutrition.target?.protein_g)} />
      </p>
      <p className={styles.hint}>整餐的熱量不超過目標的 110%、蛋白質不低於 90% 才算通過；任何一項算不出來就是「計算不完整」。</p>

      {nutrition.quantities_verified === false && (
        <Notice tone="warn">以上用量尚未通過營養驗證，僅供查看與估算成本，不可採用。</Notice>
      )}
      {(nutrition.per_person ?? []).length === 0 && (
        <Notice>這份結果沒有每個人的資料，所以桌邊沒有擺座位；下面是整餐的合計。</Notice>
      )}

      <MacroBar nutrition={nutrition} />

      {suggestions.length > 0 && (
        <Notice title={`調整建議（整餐，共 ${totalN} 份；數值已由每份換算為整餐）`}>
          <ul className={styles.bullets}>
            {suggestions.map((s, k) => <li key={k}>{suggestionText(s, totalN)}</li>)}
          </ul>
        </Notice>
      )}

      <WholeMealTable nutrition={nutrition} />
      <Footnote nutrition={nutrition} />
    </div>
  )
}
