import { Component, type ErrorInfo, type ReactNode } from 'react'
import { PageErrorFallback } from '@/components/shared/PageErrorFallback'

// The last line of defense: without this, any component throwing for a
// reason nobody's hit yet unmounts the *entire* React tree, and the user
// gets a blank white page instead of a contained, recoverable message.
// Wraps the whole routed app in App.tsx, so one page's surprise never
// takes the sidebar or any other page down with it.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled error in the UI:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return <PageErrorFallback detail={this.state.error.message} />
  }
}
