import { createContext, useContext } from 'react'
import { DEFAULT_DATE_OPTIONS, type DateOptions } from './dates'

/** The financial year start and custom range of the dashboard being shown (for KPI labels, SQL notes). */
export const DateOptionsContext = createContext<DateOptions>(DEFAULT_DATE_OPTIONS)
export const useDateOptions = () => useContext(DateOptionsContext)
