/**
 * 點了畫裡的人：他從畫裡走出來，放大在左邊的面板上（DESIGN.md 5.8）。
 * 內容和舊前端成員卡片相同，另外多一段後端算的每日基準；算不出來就照實說，不補數字。
 */
import { useQuery } from '@tanstack/react-query'
import { apiError } from '../../api/client'
import { ACTIVITY_LABELS, GOAL_LABELS, type IngredientRef, type Member } from '../../api/types'
import { Button } from '../../components/Button'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import { StatusChip, type ChipTone } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import { crayonKind, ingredientLabel, plainNumber } from './people'
import { memberTargetQuery, targetUnavailable } from './queries'
import { CRAYON } from './sprites'
import styles from './panels.module.css'

type Props = {
  member: Member
  onEdit: () => void
  onDelete: () => void
  onClose: () => void
}

const unfilled = <UnknownValue label="未填" />

function Ingredients({ items, prefix, tone }: { items: IngredientRef[]; prefix: string; tone: ChipTone }) {
  if (items.length === 0) return <span className={styles.muted}>沒有登記</span>
  return (
    <ul className={styles.tags}>
      {items.map((item) => (
        <li key={item.ingredient_id}><StatusChip tone={tone}>{prefix}・{ingredientLabel(item)}</StatusChip></li>
      ))}
    </ul>
  )
}

function Amount({ value, unit }: { value: string | null | undefined; unit: string }) {
  const text = plainNumber(value)
  return text === null ? <UnknownValue /> : <>{text} {unit}</>
}

export function MemberDetail({ member, onEdit, onDelete, onClose }: Props) {
  const target = useQuery(memberTargetQuery(member))
  const age = Number.isFinite(member.age_years) ? `${member.age_years} 歲` : null
  const activity = ACTIVITY_LABELS[member.activity_category]
  const goal = GOAL_LABELS[member.goal]

  return (
    <Panel
      title={member.display_name} side="left" onClose={onClose} className={styles.wallLeft}
      footer={(
        <>
          <Button onClick={onEdit}>編輯</Button>
          <Button variant="danger" onClick={onDelete}>刪除</Button>
        </>
      )}
    >
      <div className={styles.stack}>
        <div className={styles.who}>
          <img src={CRAYON[crayonKind(member)]} alt="" />
          <div>
            <p>
              {age ?? unfilled}・{member.equation_sex === 'F' ? '女' : '男'}・
              {member.weight_kg ? `${member.weight_kg} 公斤` : unfilled}
            </p>
            <p className={styles.muted}>活動量：{activity ?? unfilled}</p>
          </div>
        </div>

        <dl className={styles.facts}>
          <dt>飲食目標</dt>
          <dd>{goal ?? unfilled}</dd>
          <dt>食量</dt>
          <dd>{member.portion ? `${member.portion}n` : unfilled}</dd>
          <dt>過敏</dt>
          <dd><Ingredients items={member.allergens} prefix="過敏" tone="danger" /></dd>
          <dt>不吃</dt>
          <dd><Ingredients items={member.avoided} prefix="不吃" tone="warn" /></dd>
        </dl>

        <section>
          <h3>每日基準</h3>
          {target.isPending && <Spinner label="正在算每日基準…" />}
          {target.data && (
            <>
              <dl className={styles.facts}>
                <dt>熱量</dt>
                <dd><Amount value={target.data.daily_reference?.energy_kcal} unit="大卡" /></dd>
                <dt>蛋白質</dt>
                <dd><Amount value={target.data.daily_reference?.protein_g} unit="公克" /></dd>
              </dl>
              <p className={styles.muted}>一整天的基準，還沒乘上餐別比例與食量 n；每一餐的目標在規劃時才算。</p>
            </>
          )}
          {target.isError && (targetUnavailable(target.error) ? (
            <Notice tone="warn" title="每日目標算不出來">
              {apiError(target.error)?.message ?? '以目前的設定算不出每日目標。'}
              規劃餐食時選到這位成員，整桌的營養目標也會算不出來；按「編輯」調整後就可以了。
            </Notice>
          ) : (
            <ErrorNotice error={target.error} fallback="讀不到每日基準。" onRetry={() => target.refetch()} retrying={target.isFetching} />
          ))}
        </section>
      </div>
    </Panel>
  )
}
