import { Component, type ReactNode } from 'react'

/** Keeps one broken panel from blanking the whole app; shows the error and a retry. */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="page" role="alert">
        <p className="error">{this.props.label ?? 'Something went wrong'}: {this.state.error.message}</p>
        <button className="btn ghost small" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    )
  }
}
