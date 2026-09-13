import React, { Component, ReactNode } from "react"
import ReactDOM from "react-dom/client"
import App from "./App"
import "./index.css"

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  public componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("Uncaught error:", error, errorInfo)
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-dvh flex-col items-center justify-center bg-zinc-950 p-6 text-center text-zinc-100 font-mono">
          <div className="size-12 rounded-2xl bg-emerald-500/20 flex items-center justify-center text-2xl mb-4">
            ⚡
          </div>
          <h1 className="text-xl font-bold text-emerald-400">Skye Player</h1>
          <p className="mt-2 text-xs text-zinc-400 max-w-xs break-words">
            {this.state.error?.message || "An unexpected rendering error occurred"}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null })
              window.location.reload()
            }}
            className="mt-6 rounded-xl bg-emerald-500 px-4 py-2 text-xs font-bold text-zinc-950 shadow-lg hover:bg-emerald-400 active:scale-95 transition-all"
          >
            Reload Player
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
