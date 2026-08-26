import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface State {
  error: Error | null;
}

/** Last-resort render error catcher so one broken panel never blanks the app. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Vigil UI error boundary:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-panel" style={{ margin: 20 }}>
          <div className="title">Something broke while rendering this view</div>
          <div className="small dim mono">{this.state.error.message}</div>
          <button
            className="btn sm"
            style={{ marginTop: 10 }}
            onClick={() => this.setState({ error: null })}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
