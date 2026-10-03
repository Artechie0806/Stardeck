import { createContext, useContext } from 'react'

export const AuthContext = createContext<{ required: boolean; signOut: () => void }>({
  required: false, signOut: () => {},
})
export const useAuth = () => useContext(AuthContext)
