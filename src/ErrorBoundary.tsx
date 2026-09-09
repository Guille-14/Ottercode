import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { err: string | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null }

  static getDerivedStateFromError(error: Error): State {
    return { err: error?.message || String(error) }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ottercode]', error, info.componentStack)
  }

  render() {
    if (this.state.err) {
      return (
        <div className="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 bg-canvas p-6 text-ink">
          <p className="text-sm font-semibold">Algo falló en la interfaz</p>
          <p className="max-w-md text-center text-xs text-muted">{this.state.err}</p>
          <button
            type="button"
            className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs"
            onClick={() => {
              this.setState({ err: null })
              window.location.reload()
            }}
          >
            Recargar
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
