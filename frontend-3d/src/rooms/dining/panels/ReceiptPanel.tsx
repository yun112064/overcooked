/**
 * 收據明細（舊前端結果頁的「需補買食材」）：每樣要補買的食材的缺量、包裝、兩種金額與報價來源。
 *
 * 「要買的整包」（整包採購價）和「這餐吃掉」（用量成本）是兩個數字，分開列、不相加。
 * 查無價格的項目用虛線框列出並寫原因；總額未知時寫「未知（已知 …）」，不寫成 0。
 */
import type { ReactNode } from 'react'
import type { MealResult, PriceItem } from '../../../api/types'
import { Money } from '../../../components/Money'
import { Notice } from '../../../components/Notice'
import { StatusChip } from '../../../components/StatusChip'
import { budgetStamp, grams, missingReason, shortTime, volume } from '../resultText'
import styles from './panels.module.css'

function Source({ item }: { item: PriceItem }) {
  if (item.price_status === 'unknown') {
    return <p className={styles.itemMissing} title={item.missing_reason ?? undefined}>查無價格・{missingReason(item.missing_reason)}</p>
  }
  const name = item.source_name ?? item.retailer ?? item.source
  return (
    <p className={styles.itemSource}>
      {item.product_url ? <a href={item.product_url} target="_blank" rel="noreferrer">{name}</a> : name}
      {item.queried_at && <span>{shortTime(item.queried_at)} 查詢</span>}
      {item.missing_reason && <em>{missingReason(item.missing_reason)}</em>}
    </p>
  )
}

type ReceiptDetailProps = {
  prices: MealResult['prices']
  /** 價格重試、一鍵購入這些跟狀態有關的區塊，由場景決定要放什麼 */
  children?: ReactNode
}

export function ReceiptDetail({ prices, children }: ReceiptDetailProps) {
  const computed = prices.status === 'computed'
  const missingQuantityNames = (prices.unquantified_items ?? []).map((i) => i.name)
  const missingPriceNames = prices.items.filter((i) => i.price_status === 'unknown').map((i) => i.name)
  const stamp = budgetStamp(prices)
  return (
    <div className={styles.stack}>
      {computed && prices.items.length > 0 && <p className={styles.hint}>調味料與家中已有的不列入。</p>}
      {missingQuantityNames.length > 0 && (
        <Notice tone="warn">
          用量未定，整餐成本未知：{missingQuantityNames.join('、')}。已知小計只包含有用量及報價的食材。
        </Notice>
      )}

      {!computed ? (
        <p>目前缺少可用的食材用量，無法計算缺料價格。</p>
      ) : prices.items.length === 0 ? (
        missingQuantityNames.length === 0 && <p>已知用量的食材無需補買（成本 0 元）。</p>
      ) : (
        <ul className={styles.items}>
          {prices.items.map((item, index) => (
            <li key={`${item.food_id}-${index}`} className={item.price_status === 'unknown' ? styles.itemUnknown : undefined}>
              <p className={styles.itemHead}>
                <b>{item.name}</b>
                <span>缺 {item.shortage_ml != null ? volume(item.shortage_ml) : grams(item.shortage_grams)}</span>
              </p>
              <p className={styles.itemPack}>
                包裝：{item.price_status === 'known' && item.packs !== null ? `${item.package_description ?? ''} × ${item.packs}` : '待確認'}
              </p>
              <dl className={styles.amounts}>
                <div><dt>要買的整包</dt><dd><Money value={item.purchase_price} /></dd></div>
                <div><dt>這餐吃掉</dt><dd><Money value={item.cost} /></dd></div>
              </dl>
              <Source item={item} />
            </li>
          ))}
        </ul>
      )}

      {computed && (
        <section className={styles.totals}>
          <dl className={styles.amounts}>
            <div>
              <dt>要買的整包<small>整包採購價</small></dt>
              <dd><Money value={prices.purchase_total} known={prices.known_purchase_total} /></dd>
            </div>
            <div>
              <dt>這餐吃掉<small>用量成本</small></dt>
              <dd><Money value={prices.cost_total} known={prices.known_cost_total} /></dd>
            </div>
          </dl>
          <p className={styles.hint}>這是兩個不同的數字，不相加：整包是結帳要付的，這餐吃掉的是按用量比例算的成本。</p>
          {prices.budget !== null && (
            <>
              <p className={styles.budget}>
                預算 <Money value={prices.budget} />
                {stamp && <StatusChip tone={stamp.tone === 'ok' ? 'ok' : 'danger'}>{stamp.full}</StatusChip>}
                {prices.budget_status === 'PRICE_INCOMPLETE' && <StatusChip tone="unknown">價格不完整，還不能比</StatusChip>}
              </p>
              {/* 印章緊接在兩個金額下面：講清楚比的是哪一個，免得以為結帳要付的整包也在預算內 */}
              <p className={styles.hint}>預算比的是「這餐吃掉」的用量成本，不是「要買的整包」。</p>
            </>
          )}
          {prices.budget_status === 'PRICE_INCOMPLETE' && missingPriceNames.length > 0 && (
            <p className={styles.hint}>部分價格未知，成本只計已知部分。價格未知：{missingPriceNames.join('、')}。</p>
          )}
        </section>
      )}
      {children}
    </div>
  )
}
