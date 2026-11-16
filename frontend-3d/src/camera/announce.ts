/**
 * 給讀屏的即時播報（換了房間、鏡頭換了停靠點）；畫面上看不到。
 *
 * 播報區要先在頁面上、之後改文字才會被念出來，所以整個 app 共用一個直接掛在 <body> 下的節點，
 * 不跟著房間卸載（跟著房間的話，新房間一掛上去文字就已經在裡面，讀屏不會念）。
 * 故意不加 role="status"：那個 role 留給場景自己的狀態訊息。
 */
let region: HTMLElement | null = null

export function announce(text: string): void {
  if (typeof document === 'undefined') return
  if (!region || !region.isConnected) {
    region = document.createElement('div')
    region.className = 'visually-hidden'
    region.setAttribute('aria-live', 'polite')
    document.body.appendChild(region)
  }
  region.textContent = text
}
