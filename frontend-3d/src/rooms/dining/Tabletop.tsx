/**
 * 桌上依資料擺出來的東西（DESIGN.md 5.6）：椅子、餐墊與飯碗、轉盤上的器皿、家庭合計立牌、收據、小卡盤。
 * 位置由 tableLayout 算（畫稿座標），這裡只負責畫。每個可以點的東西都是 <Hotspot>，id 和物件列一致。
 *
 * 旋轉一律轉裡面的圖，不轉 <Hotspot>／<Placed> 本身——它們的 transform 已經拿去做置中與視差了。
 */
import chairArt from '../../assets/tableware/chair-top.svg'
import dishMeat from '../../assets/tableware/dish-meat.svg'
import dishOther from '../../assets/tableware/dish-other.svg'
import dishSeafood from '../../assets/tableware/dish-seafood.svg'
import dishSoup from '../../assets/tableware/dish-soup.svg'
import dishStaple from '../../assets/tableware/dish-staple.svg'
import dishVegetable from '../../assets/tableware/dish-vegetable.svg'
import settingArt from '../../assets/tableware/place-setting.svg'
import receiptArt from '../../assets/tableware/receipt.svg'
import bowlUnknownArt from '../../assets/tableware/rice-bowl-unknown.svg'
import bowlArt from '../../assets/tableware/rice-bowl.svg'
import trayEmptyArt from '../../assets/tableware/tip-tray-empty.svg'
import trayArt from '../../assets/tableware/tip-tray.svg'
import type { MealResult } from '../../api/types'
import { Hotspot, Placed, useRoom } from '../../camera/Room'
import { Money } from '../../components/Money'
import { formatMoney } from '../../components/formatMoney'
import { StatusChip } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import {
  OVERALL_VERDICT_TEXT, VERDICT_TONE, overallVerdict, ratioText, type TargetNutrient,
} from '../../components/nutritionVerdict'
import { budgetStamp } from './resultText'
import {
  BOWL_WIDTH, RECEIPT_WIDTH, SETTING_WIDTH, TRAY_WIDTH, VESSEL_WIDTH, layoutAccessories, layoutDishes, layoutSeats,
  type VesselKind,
} from './tableLayout'
import { verdictLabel, type PanelTarget, type Seat, type TipsState, type Vessel } from './tableModel'
import styles from './DiningScene.module.css'

const VESSEL_ART: Record<VesselKind, string> = {
  staple: dishStaple, seafood: dishSeafood, meat: dishMeat, vegetable: dishVegetable, soup: dishSoup, other: dishOther,
}
/** 椅子圖的高寬比（200×180） */
const CHAIR_ASPECT = 0.9

/** 「熱量 102%」；算不出來寫虛線的「未知」，不寫 0%。 */
function Ratio({ nutrient, label, ratio }: { nutrient: TargetNutrient; label: string; ratio: number | null }) {
  return <span>{label} {ratio === null ? <UnknownValue /> : ratioText(nutrient, ratio)}</span>
}

type TabletopProps = {
  vessels: readonly Vessel[]
  seats: readonly Seat[]
  /** 整餐對目標的比例（實際 ÷ 目標；未知是 null） */
  family: { energy: number | null; protein: number | null }
  prices: MealResult['prices']
  tips: TipsState
  /** 目前打開面板的那個物件（抬起來一點） */
  activeId: string | null
  onOpen: (target: PanelTarget) => void
}

export function Tabletop({ vessels, seats, family, prices, tips, activeId, onOpen }: TabletopProps) {
  const { phone } = useRoom()
  const seatSpots = layoutSeats(seats.length)
  const dishSpots = layoutDishes(vessels.length)
  const spots = layoutAccessories(seats.length)
  const active = (id: string) => (activeId === id ? styles.active : '')

  const computed = prices.status === 'computed'
  const missingNames = prices.items.filter((i) => i.price_status === 'unknown').map((i) => i.name)
  const missingPrices = missingNames.length
  const stamp = budgetStamp(prices)
  // 手機上或人多、收據縮小時，紙上寫不下字：改成收據下面的小標籤
  const paperText = !phone && spots.receipt.scale >= 0.85
  // 兩個金額各自顯示、不相加；總額未知時寫「未知」並附已知的部分，不寫成 0
  const totals = {
    purchase: computed ? prices.purchase_total : null,
    purchaseKnown: computed ? prices.known_purchase_total : null,
    cost: computed ? prices.cost_total : null,
    costKnown: computed ? prices.known_cost_total : null,
  }
  const purchase = <Money value={totals.purchase} known={totals.purchaseKnown} />
  const cost = <Money value={totals.cost} known={totals.costKnown} />
  // 紙很窄：「已知 …」另起一行
  const knownLine = (value: string | null, known: string | null) => {
    const text = value === null ? formatMoney(known) : null
    return text === null ? null : <small>已知 {text}</small>
  }
  const familyVerdict = overallVerdict(family.energy, family.protein)

  return (
    <>
      {/* 椅子在最下面，一半收在桌子底下 */}
      {seats.map((seat, i) => {
        const spot = seatSpots[i]
        const width = SETTING_WIDTH * spot.scale
        return (
          <Placed key={seat.id} x={spot.chair.x} y={spot.chair.y - (width * CHAIR_ASPECT) / 2} width={width} k={0.4}>
            <img className={styles.sprite} src={chairArt} alt="" draggable={false} style={{ rotate: `${spot.rotation}deg` }} />
          </Placed>
        )
      })}

      {seats.map((seat, i) => {
        const spot = seatSpots[i]
        const width = SETTING_WIDTH * spot.scale
        // 把畫稿座標換成這個方框裡的百分比（方框中心＝餐墊中心）
        const at = (p: { x: number; y: number }) => ({
          left: `${50 + ((p.x - spot.setting.x) / width) * 100}%`,
          top: `${50 + ((p.y - spot.setting.y) / width) * 100}%`,
        })
        return (
          <Hotspot
            key={seat.id} id={seat.id} x={spot.setting.x} y={spot.setting.y - width / 2} width={width} k={0.7}
            label={`${seat.name}${seat.bentoOnly ? '（只帶便當）' : ''}：${seat.verdictLabel}，看個人營養`}
            className={`${styles.seat} ${active(seat.id)}`}
            onClick={() => onOpen({ kind: 'person', memberId: seat.memberId })}
          >
            <img className={styles.setting} src={settingArt} alt="" draggable={false} style={{ rotate: `${spot.rotation}deg` }} />
            {/* 算不出來：空碗加虛線框，不畫飯 */}
            <img
              className={`${styles.bowl} ${styles[seat.verdict] ?? ''}`} alt="" draggable={false}
              src={seat.verdict === 'unknown' ? bowlUnknownArt : bowlArt}
              style={{ ...at(spot.bowl), width: `${(BOWL_WIDTH / SETTING_WIDTH) * 100}%` }}
            />
            <span className={styles.nameTag} style={at(phone ? spot.label : spot.labelWide)}>
              <span className={styles.who}>
                <b>{seat.name}</b>
                {/* 手機上名牌要窄（左右兩側的座位離畫面邊緣很近）；哪一項超出或不足寫在物件列與面板裡 */}
                <StatusChip tone={VERDICT_TONE[seat.verdict]}>{phone ? OVERALL_VERDICT_TEXT[seat.verdict] : seat.verdictLabel}</StatusChip>
              </span>
              {phone ? seat.bentoOnly && <small>只帶便當</small> : (
                <small className={styles.numbers}>
                  {seat.bentoOnly && <span>便當</span>}
                  <Ratio nutrient="energy_kcal" label="熱量" ratio={seat.energy} />
                  <Ratio nutrient="protein_g" label="蛋白質" ratio={seat.protein} />
                </small>
              )}
            </span>
          </Hotspot>
        )
      })}

      <Hotspot
        id="family" x={spots.tent.x} y={spots.tent.y} k={0.7}
        label={`家庭合計：${verdictLabel(family.energy, family.protein)}，看全家營養`}
        className={`${styles.tent} ${styles.centred} ${active('family')}`} onClick={() => onOpen({ kind: 'family' })}
      >
        <b>家庭合計</b>
        {/* 三態都寫成字（通過／超出或不足／計算不完整）；是哪一項超出或不足，看下面的數字、物件列與面板 */}
        <StatusChip tone={VERDICT_TONE[familyVerdict]}>{OVERALL_VERDICT_TEXT[familyVerdict]}</StatusChip>
        <Ratio nutrient="energy_kcal" label="熱量" ratio={family.energy} />
        <Ratio nutrient="protein_g" label="蛋白質" ratio={family.protein} />
      </Hotspot>

      <Hotspot
        id="tips" x={spots.tray.x} y={spots.tray.y} width={TRAY_WIDTH * spots.tray.scale} k={0.7}
        label={`${tips.label}，打開小卡盤`}
        className={`${styles.tray} ${styles.centred} ${active('tips')}`} onClick={() => onOpen({ kind: 'tips' })}
      >
        <img src={tips.state === 'available' ? trayArt : trayEmptyArt} alt="" draggable={false} />
        <span className={styles.tag}>{tips.label}</span>
      </Hotspot>

      <Hotspot
        id="receipt" x={spots.receipt.x} y={spots.receipt.y} width={RECEIPT_WIDTH * spots.receipt.scale} k={0.7}
        label={`收據：要買的整包和這餐吃掉的金額${stamp ? `（${stamp.full}）` : ''}，看每樣食材的明細`}
        className={`${styles.receipt} ${styles.centred} ${active('receipt')}`} onClick={() => onOpen({ kind: 'receipt' })}
      >
        <span className={styles.paper} style={{ backgroundImage: `url("${receiptArt}")` }}>
          {paperText && (
            <>
              <b>超市收據</b>
              <span className={styles.line}>
                <small>要買的整包</small><Money value={totals.purchase} />{knownLine(totals.purchase, totals.purchaseKnown)}
              </span>
              <span className={styles.line}>
                <small>這餐吃掉</small><Money value={totals.cost} />{knownLine(totals.cost, totals.costKnown)}
              </span>
              {/* 價格不完整：不蓋章，把查不到價格的列出來 */}
              {missingNames.slice(0, 2).map((name, k) => (
                <small key={k} className={styles.missing}><span>{name}</span><UnknownValue /></small>
              ))}
              {missingPrices > 2 && <small>共 {missingPrices} 項價格未知</small>}
            </>
          )}
          {/* 價格不完整、沒有預算時不蓋章。預算比的是「這餐吃掉」那一個金額，印章上寫出來 */}
          {stamp && <em className={`${styles.stamp} ${styles[stamp.tone]}`}><small>這餐吃掉</small>{stamp.text}</em>}
        </span>
        {!paperText && (
          <span className={styles.receiptTag}>
            <span>整包 {purchase}</span>
            <span>吃掉 {cost}</span>
            {missingPrices > 0 && <small>{missingPrices} 項價格未知</small>}
          </span>
        )}
      </Hotspot>

      {vessels.map((vessel, i) => {
        const spot = dishSpots[i]
        return (
          <Hotspot
            key={vessel.id} id={vessel.id} x={spot.x} y={spot.y - spot.size / 2} width={spot.size} k={0.7}
            label={vessel.target.kind === 'staple' ? `主食 ${vessel.name}，看份量` : `${vessel.name}，看做法與食材`}
            className={`${styles.dish} ${active(vessel.id)}`} onClick={() => onOpen(vessel.target)}
          >
            <img src={VESSEL_ART[vessel.kind]} alt="" draggable={false} style={{ width: `${VESSEL_WIDTH[vessel.kind] * 100}%` }} />
            <span className={styles.tag}>
              {vessel.name}
              {vessel.highSodium && <em>高鈉</em>}
              {vessel.problem && <em>份量未知</em>}
            </span>
          </Hotspot>
        )
      })}
    </>
  )
}
