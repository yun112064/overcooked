import { replace, type LoaderFunctionArgs } from 'react-router'

/** 舊書籤保留日期等查詢參數，取代舊網址以免返回時反覆轉址。 */
export function redirectGenerationHistory({ request }: Pick<LoaderFunctionArgs, 'request'>) {
  return replace(`/history${new URL(request.url).search}`)
}
