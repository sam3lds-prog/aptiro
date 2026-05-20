import { Component, ErrorInfo, ReactNode } from "react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Logged to console; in prod a real app would ship this to an error service.
    // eslint-disable-next-line no-console
    console.error("[aptiro] render error:", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center p-6 bg-bg text-ink">
          <div className="max-w-md w-full bg-panel border border-line rounded-xl2 p-6">
            <div className="font-display text-2xl font-semibold mb-2">Something broke.</div>
            <div className="text-sub text-[13.5px] mb-4">
              The UI hit an unexpected error and stopped rendering. Your
              data is safe — the backend is untouched. You can recover or
              reload.
            </div>
            <pre className="text-[12px] text-prov-red whitespace-pre-wrap bg-panel2 border border-line rounded-md p-2.5 mb-4 max-h-48 overflow-auto">
              {this.state.error.message}
            </pre>
            <div className="flex gap-2">
              <button
                onClick={this.reset}
                className="h-9 px-4 rounded-lg bg-accent text-white text-[13px] font-medium hover:bg-accent/90"
              >
                Try again
              </button>
              <button
                onClick={() => window.location.reload()}
                className="h-9 px-4 rounded-lg bg-panel2 text-ink border border-line text-[13px] font-medium hover:border-sub/60"
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
