/**
 * 全家名單：統計（成員數、合計食量、過敏與不吃）加上每一位成員。
 * 統計是舊前端成員頁最上面那一列；名單也讓畫裡排不下的成員有地方進去。
 */
import type { Member } from '../../api/types'
import { Button } from '../../components/Button'
import { Panel } from '../../components/Panel'
import { UnknownValue } from '../../components/UnknownValue'
import { crayonKind, familyStats, memberSummary } from './people'
import { CRAYON } from './sprites'
import styles from './panels.module.css'

type Props = {
  members: Member[]
  /** 後端說每日目標算不出來的成員 */
  unavailable: ReadonlySet<string>
  onOpen: (id: string) => void
  onAdd: () => void
  onClose: () => void
}

export function RosterPanel({ members, unavailable, onOpen, onAdd, onClose }: Props) {
  const stats = familyStats(members)
  return (
    <Panel
      title="全家名單" side="left" onClose={onClose} className={styles.wallLeft}
      footer={<Button variant="primary" onClick={onAdd}>新增成員</Button>}
    >
      <div className={styles.stack}>
        <p className={styles.muted}>
          每位成員的年齡、體重、活動量與目標，決定每個人這一餐的熱量與蛋白質；過敏和不吃的食材一定會避開。
        </p>

        <dl className={styles.stats}>
          <div><dt>成員</dt><dd>{stats.count} 人</dd></div>
          <div>
            <dt>合計食量</dt>
            <dd>
              {stats.portionTotal === null ? <UnknownValue /> : `${stats.portionTotal} n`}
              <small>全員一起用餐時的份數</small>
            </dd>
          </div>
          {/* 有人過敏時這一格換成提醒的底色（和舊前端一樣）；人數本身仍是文字 */}
          <div className={stats.allergic.people > 0 ? styles.warnStat : undefined}>
            <dt>有過敏</dt>
            <dd>{stats.allergic.people} 人<small>共 {stats.allergic.ingredients} 種食材</small></dd>
          </div>
          <div>
            <dt>有不吃</dt>
            <dd>{stats.avoiding.people} 人<small>共 {stats.avoiding.ingredients} 種食材</small></dd>
          </div>
        </dl>

        <ul className={styles.roster}>
          {members.map((member) => (
            <li key={member.id}>
              <button type="button" onClick={() => onOpen(member.id)}>
                <img src={CRAYON[crayonKind(member)]} alt="" />
                <span>
                  <b>{member.display_name}</b>
                  <small>
                    {memberSummary(member, unavailable.has(member.id)).map((piece, i) => (
                      <span key={piece.text}>
                        {i > 0 && '・'}
                        <span className={piece.missing ? styles.pencil : undefined}>{piece.text}</span>
                      </span>
                    ))}
                  </small>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  )
}
