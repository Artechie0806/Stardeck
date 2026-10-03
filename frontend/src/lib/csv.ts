import type { QueryResult } from '../api/types'

const cell = (v: unknown) => {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Download a widget's result as a CSV file (with a BOM so Excel reads UTF-8 correctly). */
export function downloadCsv(result: QueryResult, title: string) {
  const lines = [result.columns.map(cell).join(','), ...result.rows.map((r) => r.map(cell).join(','))]
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${title.replace(/[^\w\- ]+/g, '').trim() || 'widget'}.csv`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
