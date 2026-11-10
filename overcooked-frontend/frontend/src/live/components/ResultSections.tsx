import { Box, Stack, Tab, Tabs } from '@mui/material'
import { BookOpen, ChartNoAxesColumn, ShoppingBasket, Utensils } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

/** 隱藏未選頁籤但保留掛載，避免切換頁籤重送查詢或知識生成請求。 */
export default function ResultSections({ recipes, shopping, nutrition, portions }: {
  recipes: ReactNode; shopping: ReactNode; nutrition: ReactNode; portions: ReactNode
}) {
  const id = useId()
  const [tab, setTab] = useState(0)
  const labels = [
    { label: '食譜', icon: <BookOpen size={18} />, content: recipes },
    { label: '採購', icon: <ShoppingBasket size={18} />, content: shopping },
    { label: '營養', icon: <ChartNoAxesColumn size={18} />, content: nutrition },
    { label: '分餐', icon: <Utensils size={18} />, content: portions },
  ]
  return <>
    <Tabs className="live-result-tabs" value={tab} onChange={(_, value) => setTab(value)} variant="scrollable"
      scrollButtons="auto" allowScrollButtonsMobile aria-label="菜單內容">
      {labels.map((item, index) => <Tab key={item.label} label={item.label} icon={item.icon} iconPosition="start"
        id={`${id}-tab-${index}`} aria-controls={`${id}-panel-${index}`} />)}
    </Tabs>
    {labels.map((item, index) => <Box key={item.label} className="live-result-panel" role="tabpanel"
      hidden={tab !== index} id={`${id}-panel-${index}`} aria-labelledby={`${id}-tab-${index}`} tabIndex={0}>
      <Stack spacing={3}>{item.content}</Stack>
    </Box>)}
  </>
}
