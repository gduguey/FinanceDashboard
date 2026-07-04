import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import { useBenchmarkSetting, useSetBenchmarkSetting } from '@/hooks/usePortfolioData'
import type { SymbolSearchResult } from '@/types/portfolio'

// NEW_TASKS.md 2.2b: lets the user pick which fund to benchmark against
// instead of a hardcoded VOO, searching any symbol Yahoo Finance knows.
export function BenchmarkPicker() {
  const { data: setting } = useBenchmarkSetting()
  const setSetting = useSetBenchmarkSetting()
  const [query, setQuery] = useState('')
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

  function select(symbol: string) {
    setSetting.mutate({ symbol_override: symbol })
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium">Benchmark</span>
      <div className="relative">
        <Input
          className="w-56"
          placeholder={setting?.symbol_override ?? 'VOO'}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
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
    </div>
  )
}
