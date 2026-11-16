import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'
import SimMarketPage from './pages/SimMarketPage'

const client = new QueryClient()
const theme = createTheme({ typography: { fontFamily: 'system-ui, sans-serif' } })
const router = createBrowserRouter([
  { path: '/', element: <SimMarketPage page="catalog" /> },
  { path: '/checkout', element: <SimMarketPage page="checkout" /> },
  { path: '*', element: <SimMarketPage page="catalog" /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode><ThemeProvider theme={theme}><CssBaseline />
    <QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>
  </ThemeProvider></StrictMode>,
)
