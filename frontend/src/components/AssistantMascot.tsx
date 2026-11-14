import { Box, type SxProps, type Theme } from '@mui/material'
import { useId } from 'react'

/** 飲食小幫手吉祥物：圓滾滾的紫色小寵物，穿橘色圍裙。 */
const PURPLE = '#8B5CF6'
const PURPLE_LIGHT = '#B9A2FB'
const PURPLE_DARK = '#6D3FD9'
const ORANGE = '#F97316'
const ORANGE_LIGHT = '#FDBA74'
const ORANGE_DARK = '#D9590B'
const INK = '#2E2147'
const TRIM = '#FFF4E8'

type Props = {
  size?: number
  /** thinking：查資料時上下晃動；walking：左右搖擺走路、兩腳交替 */
  mood?: 'idle' | 'thinking' | 'walking'
  sx?: SxProps<Theme>
}

export default function AssistantMascot({ size = 96, mood = 'idle', sx }: Props) {
  const id = useId().replace(/:/g, '')
  const body = `mascot-body-${id}`
  const shine = `mascot-shine-${id}`
  const bodyPath = 'M60 18C88 18 103 40 103 68C103 95 86 108 60 108C34 108 17 95 17 68C17 40 32 18 60 18Z'
  return (
    <Box component="span" role="img" aria-label="飲食小幫手吉祥物" sx={[{
      display: 'inline-block', width: size, height: size, flexShrink: 0, lineHeight: 0,
      '& .mascot-all': {
        transformOrigin: '60px 110px',
        animation: mood === 'walking' ? 'mascotWaddle 0.6s ease-in-out infinite'
          : mood === 'thinking' ? 'mascotHop 0.9s ease-in-out infinite' : 'mascotBreathe 3.2s ease-in-out infinite',
      },
      '& .mascot-foot-l, & .mascot-foot-r': { transformBox: 'fill-box', transformOrigin: 'center' },
      ...(mood === 'walking' && {
        '& .mascot-foot-l': { animation: 'mascotStep 0.6s ease-in-out infinite' },
        '& .mascot-foot-r': { animation: 'mascotStep 0.6s ease-in-out -0.3s infinite' },
      }),
      '& .mascot-eyes': { transformOrigin: '60px 49px', animation: 'mascotBlink 4.5s infinite' },
      '& .mascot-sprout': { transformOrigin: '60px 20px', animation: 'mascotSway 2.6s ease-in-out infinite' },
      '@keyframes mascotBreathe': { '0%,100%': { transform: 'scale(1,1)' }, '50%': { transform: 'scale(1.02,0.98)' } },
      '@keyframes mascotHop': { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-6px)' } },
      '@keyframes mascotBlink': { '0%,92%,100%': { transform: 'scaleY(1)' }, '95%': { transform: 'scaleY(0.1)' } },
      '@keyframes mascotWaddle': {
        '0%,100%': { transform: 'translateY(0) rotate(-5deg)' }, '25%': { transform: 'translateY(-4px) rotate(0deg)' },
        '50%': { transform: 'translateY(0) rotate(5deg)' }, '75%': { transform: 'translateY(-4px) rotate(0deg)' },
      },
      '@keyframes mascotStep': { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-4px)' } },
      '@keyframes mascotSway': { '0%,100%': { transform: 'rotate(-8deg)' }, '50%': { transform: 'rotate(8deg)' } },
      '@media (prefers-reduced-motion: reduce)': {
        '& .mascot-all, & .mascot-eyes, & .mascot-sprout, & .mascot-foot-l, & .mascot-foot-r': { animation: 'none' },
      },
    }, ...(Array.isArray(sx) ? sx : [sx])]}>
      <svg viewBox="0 0 120 120" width={size} height={size} aria-hidden="true">
        <defs>
          <clipPath id={body}><path d={bodyPath} /></clipPath>
          <radialGradient id={shine} cx="0.38" cy="0.3" r="0.75">
            <stop offset="0" stopColor={PURPLE_LIGHT} />
            <stop offset="0.6" stopColor={PURPLE} />
            <stop offset="1" stopColor={PURPLE_DARK} />
          </radialGradient>
        </defs>
        <ellipse cx="60" cy="112" rx="34" ry="5" fill={INK} opacity="0.12" />
        <g className="mascot-all">
          {/* 頭上的橘色小芽 */}
          <g className="mascot-sprout">
            <path d="M60 22C60 16 60 12 61 8" stroke={ORANGE_DARK} strokeWidth="3" strokeLinecap="round" fill="none" />
            <path d="M61 9C64 2 73 1 77 4C73 10 66 12 61 9Z" fill={ORANGE} />
            <path d="M60 11C56 5 48 5 45 8C49 13 56 14 60 11Z" fill={ORANGE_LIGHT} />
          </g>
          {/* 小腳 */}
          <ellipse className="mascot-foot-l" cx="45" cy="107" rx="9" ry="5.5" fill={PURPLE_DARK} />
          <ellipse className="mascot-foot-r" cx="75" cy="107" rx="9" ry="5.5" fill={PURPLE_DARK} />
          {/* 身體 */}
          <path d={bodyPath} fill={`url(#${shine})`} />
          {/* 圍裙：胸片＋肩帶＋腰帶＋荷葉邊裙片，兩側露出身體，才看得出是穿上去的 */}
          <g clipPath={`url(#${body})`}>
            {/* 肩帶 */}
            <path d="M47.5 67.5Q36 68 21 75" stroke={ORANGE_DARK} strokeWidth="3" fill="none" strokeLinecap="round" />
            <path d="M72.5 67.5Q84 68 99 72" stroke={ORANGE_DARK} strokeWidth="3" fill="none" strokeLinecap="round" />
            {/* 裙片（下緣荷葉邊） */}
            <path d="M38 87H82L87 98a5.4 5 0 0 1-10.8 0a5.4 5 0 0 1-10.8 0a5.4 5 0 0 1-10.8 0a5.4 5 0 0 1-10.8 0a5.4 5 0 0 1-10.8 0Z"
              fill={ORANGE} stroke={TRIM} strokeWidth="1.4" strokeLinejoin="round" />
            {/* 胸片 */}
            <path d="M46 70Q46 66 50 66H70Q74 66 74 70V86H46Z" fill={ORANGE} stroke={TRIM} strokeWidth="1.4" />
            <path d="M60 79C56.5 76.8 54.5 75 54.5 73C54.5 71.6 55.6 70.6 56.9 70.6C58.3 70.6 59.4 71.5 60 72.5C60.6 71.5 61.7 70.6 63.1 70.6C64.4 70.6 65.5 71.6 65.5 73C65.5 75 63.5 76.8 60 79Z"
              fill={PURPLE} />
            {/* 腰帶 */}
            <rect x="35" y="83" width="50" height="6" rx="3" fill={ORANGE_DARK} />
            {/* 口袋 */}
            <rect x="50" y="91" width="20" height="9" rx="2.5" fill={ORANGE_LIGHT} />
            <path d="M52 93.5H68" stroke={TRIM} strokeWidth="1.2" strokeDasharray="2 2" />
          </g>
          {/* 腰帶側邊的蝴蝶結 */}
          <path d="M86 87L90 97L93.5 95.5Z" fill={ORANGE_DARK} />
          <path d="M86 87L84 98L87.5 98Z" fill={ORANGE_DARK} />
          <ellipse cx="91.5" cy="82.5" rx="5.5" ry="3.4" transform="rotate(-28 91.5 82.5)" fill={ORANGE} stroke={TRIM} strokeWidth="1" />
          <ellipse cx="92" cy="90.5" rx="5.5" ry="3.4" transform="rotate(28 92 90.5)" fill={ORANGE} stroke={TRIM} strokeWidth="1" />
          <circle cx="86.5" cy="86.5" r="2.8" fill={ORANGE_DARK} />
          {/* 小手 */}
          <ellipse cx="18" cy="80" rx="6.5" ry="9" transform="rotate(25 18 80)" fill={PURPLE} />
          <ellipse cx="102" cy="72" rx="6.5" ry="9" transform="rotate(-35 102 72)" fill={PURPLE} />
          {/* 臉 */}
          <ellipse cx="35" cy="59" rx="6" ry="3.5" fill={ORANGE_LIGHT} opacity="0.85" />
          <ellipse cx="85" cy="59" rx="6" ry="3.5" fill={ORANGE_LIGHT} opacity="0.85" />
          <g className="mascot-eyes">
            <ellipse cx="46" cy="49" rx="5.2" ry="6.6" fill={INK} />
            <ellipse cx="74" cy="49" rx="5.2" ry="6.6" fill={INK} />
            <circle cx="47.8" cy="46.4" r="2" fill="#fff" />
            <circle cx="75.8" cy="46.4" r="2" fill="#fff" />
          </g>
          <path d="M54.5 57Q60 63 65.5 57" stroke={INK} strokeWidth="2.6" strokeLinecap="round" fill="none" />
          <ellipse cx="42" cy="31" rx="8" ry="4.5" transform="rotate(-25 45 34)" fill="#fff" opacity="0.25" />
        </g>
      </svg>
    </Box>
  )
}
