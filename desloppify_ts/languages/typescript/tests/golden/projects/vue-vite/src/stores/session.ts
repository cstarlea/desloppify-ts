export interface Session {
  theme: string
  save(theme: string): void
}

const state: Session = {
  theme: 'light',
  save(theme: string) {
    state.theme = theme
  },
}

export function useSession(): Session {
  return state
}
