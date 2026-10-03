import { createContext, useContext } from 'react'

/**
 * Where floating menus render. Normally <body>; inside a modal <dialog> it must be the dialog itself, because a
 * modal sits above the rest of the page and everything outside it is inert (a menu on <body> would open behind it
 * and couldn't be clicked).
 */
export const PortalHost = createContext<HTMLElement | null>(null)

export const usePortalHost = () => useContext(PortalHost) ?? document.body
