// The transformation step catalogue, shared by the "Add step" menu, the step forms and the column
// header quick actions. Field names match the backend step models (backend/src/stardeck/transforms.py).
import type { TransformStep } from '../api/types'

export type FieldKind =
  | 'column' | 'columns' | 'text' | 'number' | 'choice' | 'mapping' | 'aggregations'
  | 'table' | 'tableColumn' | 'tableColumns' | 'bool' | 'expression'
  | 'names' | 'rules' | 'pivotValues'

export interface FieldDef {
  key: string
  label: string
  kind: FieldKind
  options?: { value: string; label: string }[]
  optional?: boolean
  placeholder?: string
  /** Only shown when this returns true for the step being edited. */
  showIf?: (step: TransformStep) => boolean
  help?: string
}

export interface StepDef {
  op: string
  label: string
  group: string
  fields: FieldDef[]
  defaults?: Partial<TransformStep>
}

const OPERATORS = [
  { value: 'eq', label: 'equals' }, { value: 'ne', label: 'does not equal' },
  { value: 'gt', label: 'is greater than' }, { value: 'gte', label: 'is at least' },
  { value: 'lt', label: 'is less than' }, { value: 'lte', label: 'is at most' },
  { value: 'between', label: 'is between' }, { value: 'contains', label: 'contains' },
  { value: 'starts_with', label: 'starts with' }, { value: 'in', label: 'is one of' },
  { value: 'not_in', label: 'is not one of' }, { value: 'is_blank', label: 'is blank' },
  { value: 'not_blank', label: 'is not blank' },
]
const needsValue = (s: TransformStep) => !['is_blank', 'not_blank'].includes(String(s.operator ?? 'eq'))
export const OPERATOR_OPTIONS = OPERATORS
export const operatorNeedsValue = (op: string) => !['is_blank', 'not_blank'].includes(op)

/** One "if … then …" rule of a conditional column. */
export interface Rule {
  column: string
  operator: string
  value?: string
  value2?: string
  result?: string
  result_column?: string
}

export const STEP_DEFS: StepDef[] = [
  { op: 'select_columns', label: 'Choose columns', group: 'Columns', fields: [{ key: 'columns', label: 'Keep these columns', kind: 'columns' }] },
  { op: 'remove_columns', label: 'Remove columns', group: 'Columns', fields: [{ key: 'columns', label: 'Remove these columns', kind: 'columns' }] },
  { op: 'rename_column', label: 'Rename column', group: 'Columns', fields: [
    { key: 'column', label: 'Column', kind: 'column' }, { key: 'to', label: 'New name', kind: 'text', placeholder: 'e.g. order_status' }] },
  { op: 'change_type', label: 'Change type', group: 'Columns', defaults: { to: 'text' }, fields: [
    { key: 'column', label: 'Column', kind: 'column' },
    { key: 'to', label: 'To', kind: 'choice', options: [
      { value: 'text', label: 'Text' }, { value: 'integer', label: 'Whole number' },
      { value: 'decimal', label: 'Decimal number' }, { value: 'date', label: 'Date' }] }] },
  { op: 'conditional_column', label: 'Add conditional column', group: 'Columns',
    defaults: { rules: [{ column: '', operator: 'eq', value: '', result: '' }], otherwise: '' }, fields: [
    { key: 'name', label: 'New column name', kind: 'text', placeholder: 'e.g. order_size' },
    { key: 'rules', label: 'Rules (checked in order; the first match wins)', kind: 'rules' },
    { key: 'otherwise', label: 'Otherwise', kind: 'text', optional: true, placeholder: 'e.g. Low (empty = blank)' },
    { key: 'otherwise_column', label: 'Or use this column', kind: 'column', optional: true }] },
  { op: 'split_column', label: 'Split column', group: 'Columns', defaults: { delimiter: ',', mode: 'each', parts: 2 }, fields: [
    { key: 'column', label: 'Column', kind: 'column' },
    { key: 'delimiter', label: 'Delimiter', kind: 'text', placeholder: 'e.g. , or - or a space' },
    { key: 'mode', label: 'Split at', kind: 'choice', options: [
      { value: 'each', label: 'Each delimiter' }, { value: 'first', label: 'The first delimiter only (2 columns)' }] },
    { key: 'parts', label: 'Into how many columns', kind: 'number', showIf: (s) => s.mode !== 'first' },
    { key: 'names', label: 'New column names (comma-separated)', kind: 'names', optional: true, placeholder: 'e.g. first_name, last_name' }] },
  { op: 'calculated_column', label: 'Add calculated column', group: 'Columns', fields: [
    { key: 'name', label: 'New column name', kind: 'text', placeholder: 'e.g. amount_inr' },
    { key: 'expression', label: 'Formula', kind: 'expression', placeholder: 'e.g. amt / 100.0',
      help: 'Use column names with + - * /, or functions like ROUND, UPPER, COALESCE, CASE WHEN … THEN … END.' }] },
  { op: 'date_part', label: 'Extract from date', group: 'Columns', defaults: { part: 'year_month' }, fields: [
    { key: 'column', label: 'Date column', kind: 'column' },
    { key: 'part', label: 'Part', kind: 'choice', options: [
      { value: 'year_month', label: 'Year-month (2025-03)' }, { value: 'year', label: 'Year' },
      { value: 'quarter', label: 'Quarter' }, { value: 'month', label: 'Month number' },
      { value: 'month_name', label: 'Month name' }, { value: 'weekday', label: 'Weekday' }, { value: 'day', label: 'Day' }] },
    { key: 'name', label: 'New column name', kind: 'text', placeholder: 'e.g. order_month' }] },

  { op: 'filter_rows', label: 'Filter rows', group: 'Rows', defaults: { operator: 'eq' }, fields: [
    { key: 'column', label: 'Column', kind: 'column' },
    { key: 'operator', label: 'Keep rows where it', kind: 'choice', options: OPERATORS },
    { key: 'value', label: 'Value', kind: 'text', showIf: needsValue, placeholder: 'For "one of": a, b, c' },
    { key: 'value2', label: 'And', kind: 'text', showIf: (s) => s.operator === 'between' }] },
  { op: 'sort', label: 'Sort', group: 'Rows', defaults: { descending: false }, fields: [
    { key: 'column', label: 'Column', kind: 'column' }, { key: 'descending', label: 'Largest / latest first', kind: 'bool' }] },
  { op: 'keep_top', label: 'Keep top rows', group: 'Rows', defaults: { n: 10 }, fields: [{ key: 'n', label: 'Number of rows', kind: 'number' }] },
  { op: 'remove_duplicates', label: 'Remove duplicates', group: 'Rows', defaults: { columns: [] }, fields: [
    { key: 'columns', label: 'Duplicate when these match (empty = whole row)', kind: 'columns', optional: true }] },

  { op: 'replace_values', label: 'Replace values', group: 'Values', fields: [
    { key: 'column', label: 'Column', kind: 'column' },
    { key: 'mapping', label: 'Replacements (one per line)', kind: 'mapping', placeholder: '1 = Completed\n9 = Refunded' }] },
  { op: 'fill_blanks', label: 'Fill blanks', group: 'Values', fields: [
    { key: 'column', label: 'Column', kind: 'column' }, { key: 'value', label: 'Fill with', kind: 'text', placeholder: 'e.g. 0 or Unknown' }] },
  { op: 'text_transform', label: 'Clean text', group: 'Values', defaults: { mode: 'trim' }, fields: [
    { key: 'column', label: 'Column', kind: 'column' },
    { key: 'mode', label: 'Make it', kind: 'choice', options: [
      { value: 'trim', label: 'Trimmed (no extra spaces)' }, { value: 'upper', label: 'UPPERCASE' }, { value: 'lower', label: 'lowercase' }] }] },

  { op: 'merge', label: 'Merge with another table', group: 'Combine', defaults: { how: 'left', columns: [] }, fields: [
    { key: 'table', label: 'Other table', kind: 'table' },
    { key: 'left_on', label: 'Match this column', kind: 'column' },
    { key: 'right_on', label: 'With this column in the other table', kind: 'tableColumn' },
    { key: 'how', label: 'Keep', kind: 'choice', options: [
      { value: 'left', label: 'All rows (blank where no match)' }, { value: 'inner', label: 'Only matching rows' }] },
    { key: 'columns', label: 'Columns to bring in', kind: 'tableColumns' }] },
  { op: 'append', label: 'Append another table', group: 'Combine', fields: [
    { key: 'table', label: 'Table with the same columns', kind: 'table' }] },

  { op: 'group_by', label: 'Group and summarize', group: 'Summarize', defaults: { by: [], aggregations: [{ column: null, func: 'count', name: 'rows' }] }, fields: [
    { key: 'by', label: 'Group by', kind: 'columns', optional: true },
    { key: 'aggregations', label: 'Summaries', kind: 'aggregations' }] },
  { op: 'pivot', label: 'Pivot column', group: 'Summarize', defaults: { func: 'sum', values: [] }, fields: [
    { key: 'column', label: 'Turn the values of this column into columns', kind: 'column' },
    { key: 'value_column', label: 'Fill them with', kind: 'column' },
    { key: 'func', label: 'Combined by', kind: 'choice', options: [
      { value: 'sum', label: 'Sum' }, { value: 'avg', label: 'Average' }, { value: 'count', label: 'Count' },
      { value: 'min', label: 'Minimum' }, { value: 'max', label: 'Maximum' }] },
    { key: 'values', label: 'Which values become columns (none picked = all, up to 30)', kind: 'pivotValues', optional: true }] },
  { op: 'unpivot', label: 'Unpivot columns', group: 'Summarize', defaults: { name_column: 'attribute', value_column: 'value' }, fields: [
    { key: 'columns', label: 'Turn these columns into rows', kind: 'columns' },
    { key: 'name_column', label: 'Name column', kind: 'text' }, { key: 'value_column', label: 'Value column', kind: 'text' }] },
]

export const STEP_GROUPS = [...new Set(STEP_DEFS.map((d) => d.group))]
export const stepDef = (op: string) => STEP_DEFS.find((d) => d.op === op)

/** Turn "1 = Completed" lines into {1: "Completed"} and back. */
export const parseMapping = (text: string) => Object.fromEntries(text.split('\n')
  .map((l) => l.split('=')).filter((p) => p.length >= 2 && p[0].trim())
  .map(([k, ...v]) => [k.trim(), v.join('=').trim()]))
export const formatMapping = (m: unknown) =>
  Object.entries((m as Record<string, string>) ?? {}).map(([k, v]) => `${k} = ${v}`).join('\n')

/** Drop fields the step type doesn't use (or that are hidden), so the backend validates cleanly. */
export function cleanStep(step: TransformStep): TransformStep {
  const def = stepDef(step.op)
  if (!def) return step
  const out: TransformStep = { op: step.op }
  for (const f of def.fields) {
    if (f.showIf && !f.showIf(step)) continue
    const v = step[f.key]
    if (v === undefined || v === '' || (Array.isArray(v) && !v.length && !f.optional)) continue
    if (f.kind === 'names') {
      const names = (Array.isArray(v) ? v : String(v).split(',')).map((x) => String(x).trim()).filter(Boolean)
      if (names.length) out[f.key] = names
      continue
    }
    if (f.kind === 'rules') {
      out[f.key] = (v as Rule[]).map((r) => {
        const rule: Record<string, unknown> = { column: r.column, operator: r.operator }
        if (operatorNeedsValue(r.operator)) rule.value = r.value
        if (r.operator === 'between') rule.value2 = r.value2
        if (r.result_column) rule.result_column = r.result_column
        else rule.result = r.result ?? ''
        return rule
      })
      continue
    }
    out[f.key] = f.kind === 'number' ? Number(v) : v
  }
  return out
}

/** Which required fields are still empty. */
export function missingFields(step: TransformStep): string[] {
  const def = stepDef(step.op)
  if (!def) return []
  return def.fields.filter((f) => !f.optional && (!f.showIf || f.showIf(step)) && f.kind !== 'bool')
    .filter((f) => {
      const v = step[f.key]
      return v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)
        || (f.kind === 'mapping' && !Object.keys(v as object).length)
        || (f.kind === 'rules' && (v as Rule[]).some((r) => !r.column
          || (operatorNeedsValue(r.operator) && !String(r.value ?? '').trim())
          || (r.operator === 'between' && !String(r.value2 ?? '').trim())))
    }).map((f) => f.label)
}
