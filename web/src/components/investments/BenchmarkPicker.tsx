import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import { useBenchmarkSetting, useEnsureSymbolPriced, useSetBenchmarkSetting } from '@/hooks/usePortfolioData'
import type { SymbolSearchResult } from '@/types/portfolio'

// Lets the user pick which fund to benchmark against instead of a
// hardcoded VOO, searching any symbol Yahoo Finance knows. Selecting a
// symbol fetches its price history right away instead of waiting for the
// next full sync, so the charts pick it up immediately.
export function BenchmarkPicker() {
  const { data: setting } = useBenchmarkSetting()
  const setSetting = useSetBenchmarkSetting()
  const ensurePriced = useEnsureSymbolPriced()
  const [query, setQuery] = useState('')
  const [isEditing, setIsEditing] = useState(false)
  const [results, setResults] = useState<SymbolSearchResult[]>([])
  const [open, setOpen] = useState(false)
  const debounceRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      return undefined
    }
    window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => {
      api
        .searchSymbols(query)
        .then((found) => {
          setResults(found)
          setOpen(true)
        })
        .catch(() => setResults([]))
    }, 300)
    return () => window.clearTimeout(debounceRef.current)
  }, [query])

  // Auto-hide "Download complete!" a couple seconds after it lands, so it
  // reads as a confirmation rather than a permanent status line.
  const resetEnsurePriced = ensurePriced.reset
  const wasJustPriced = ensurePriced.isSuccess
  useEffect(() => {
    if (!wasJustPriced) return undefined
    const timeout = window.setTimeout(resetEnsurePriced, 2500)
    return () => window.clearTimeout(timeout)
  }, [wasJustPriced, resetEnsurePriced])

  function select(symbol: string) {
    setSetting.mutate({ symbol_override: symbol })
    ensurePriced.mutate(symbol)
    setQuery('')
    setResults([])
    setOpen(false)
    setIsEditing(false)
  }

  const statusText = ensurePriced.isPending
    ? 'Downloading stock history…'
    : ensurePriced.isSuccess
      ? 'Download complete!'
      : ensurePriced.isError
        ? 'Could not fetch price history'
        : null

  // The field shows the persisted selection as real (non-placeholder) text
  // so it doesn't read as "nothing chosen yet" — only while actively typing
  // a new search does it switch to the in-progress query.
  const displayValue = isEditing ? query : (setting?.symbol_override ?? '')

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium">Benchmark</span>
      <div className="relative">
        <Input
          className="w-56"
          placeholder="VOO"
          value={displayValue}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => {
            setIsEditing(true)
            setQuery('')
            if (results.length > 0) setOpen(true)
          }}
          onBlur={() =>
            window.setTimeout(() => {
              setOpen(false)
              setIsEditing(false)
            }, 150)
          }
        />
        {open && results.length > 0 && (
          <ul className="absolute z-10 mt-1 max-h-64 w-72 overflow-y-auto rounded-md border border-border bg-popover p-1 text-sm shadow-md">
            {results.map((result) => (
              <li key={result.symbol}>
                <button
                  type="button"
                  className="flex w-full flex-col items-start rounded px-2 py-1 text-left hover:bg-muted"
                  onMouseDown={() => select(result.symbol)}
                >
                  <span className="font-medium">{result.symbol}</span>
                  <span className="text-xs text-muted-foreground">
                    {result.name} · {result.exchange}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {setting?.symbol_override && (
        <Button variant="ghost" size="sm" onClick={() => setSetting.mutate({ symbol_override: null })}>
          Reset to default
        </Button>
      )}
      {statusText && <span className="text-xs text-muted-foreground">{statusText}</span>}
    </div>
  )
}
