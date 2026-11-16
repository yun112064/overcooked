import add from '../../assets/family/crayon-add.svg'
import elder from '../../assets/family/crayon-elder.svg'
import kid from '../../assets/family/crayon-kid.svg'
import man from '../../assets/family/crayon-man.svg'
import woman from '../../assets/family/crayon-woman.svg'
import type { CrayonKind } from './people'

/** 蠟筆人物的圖檔（120×200）。 */
export const CRAYON: Record<CrayonKind, string> = { man, woman, kid, elder }
/** 還沒畫上的新成員：鉛筆虛線輪廓。 */
export const CRAYON_ADD = add
