import { createContext, useContext } from 'react'
import type { AuthMode, CurrentUser, Role, WorkspaceInfo } from '../api/types'

const RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2 }

export interface AuthInfo {
  mode: AuthMode
  /** Sign-in is on (a password or accounts). */
  required: boolean
  user: CurrentUser | null
  /** Hosted mode: people sign in with their email into their own workspace. */
  hosted: boolean
  workspace: WorkspaceInfo | null
  signOut: () => void
  /** Re-read who is signed in (e.g. after turning on sign-in). */
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthInfo>({
  mode: 'open', required: false, user: null, hosted: false, workspace: null, signOut: () => {}, refresh: async () => {},
})
export const useAuth = () => useContext(AuthContext)

/** Whether the signed-in person has at least `role`. Open installs (no sign-in) are admin. */
export function useCan(role: Role): boolean {
  const { user } = useAuth()
  return RANK[user?.role ?? 'admin'] >= RANK[role]
}
