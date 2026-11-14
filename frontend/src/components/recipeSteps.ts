// 食譜步驟只顯示做法本身：工作簿整理時寫進步驟的版本說明、步驟編號標籤不給使用者看。

/** 版本／整理說明句，例如「本版備料以食材表採值為準。」「本版設定：…原1人份保留在source_archive。」 */
const EDITOR_SENTENCE = /本版|source_archive/i
/** 和畫面編號重複的「步驟一：」「步驟 2.」 */
const STEP_LABEL = /^\s*步驟\s*[0-9０-９零〇一二三四五六七八九十百兩]+\s*[：:、.．]\s*/
/** 步驟標籤後的小標，例如「預備材料：」 */
const HEADING = /^[^，,。；;：:\s]{1,8}[：:]\s*/

export function cleanStep(text: string): string {
  const sentences = text.match(/[^。]+。?/g) ?? []
  let body = sentences.filter((s) => !EDITOR_SENTENCE.test(s)).join('').trim()
  const label = STEP_LABEL.exec(body)
  if (label) body = body.slice(label[0].length).replace(HEADING, '')
  return body.trim()
}

/** 清掉說明文字；整筆只有說明的步驟不顯示。 */
export function cleanSteps(steps: string[]): string[] {
  return steps.map(cleanStep).filter(Boolean)
}
