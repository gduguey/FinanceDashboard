// A small progress bar for actions with a real step to describe but no
// backend-tracked percent (unlike the investments Sync button, which
// polls a real percent — see SyncButton.tsx) — sliding rather than
// filling, so it never claims a completion percentage it doesn't have.
export function LoadingProgressBar({ step }: { step: string }) {
  return (
    <div className="w-48">
      <div className="relative h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="absolute h-full w-1/3 rounded-full bg-foreground"
          style={{ animation: 'indeterminate-bar 1.1s ease-in-out infinite' }}
        />
      </div>
      <div className="mt-0.5 text-center text-[10px] text-muted-foreground">{step}</div>
    </div>
  )
}
