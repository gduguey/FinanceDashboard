import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeader } from '@/components/layout/PageHeader'
import { api } from '@/lib/api'
import { accountingApi } from '@/lib/accountingApi'
import { downloadCsv, downloadFromUrl, downloadJson, exportStamp } from '@/lib/download'
import {
  useClearIbkrSettings,
  useIbkrSettings,
  useSetIbkrSettings,
} from '@/hooks/usePortfolioData'
import { useClearLlmSettings, useLlmSettings, useLlmUsage, useSetLlmSettings } from '@/hooks/useAccountingData'

// Never shows a saved secret back — the backend only ever reports whether
// a field is set, never its value, so a field that's already configured
// shows as an empty box with a placeholder saying so, and typing a new
// value replaces it on save. This is the "somewhere in Settings" the
// Investments page's own "not connected" fallback links to.
function IbkrConnectionCard() {
  const { data, isLoading } = useIbkrSettings()
  const setSettings = useSetIbkrSettings()
  const clearSettings = useClearIbkrSettings()
  const [token, setToken] = useState('')
  const [queryId, setQueryId] = useState('')

  function handleSave() {
    setSettings.mutate(
      { ...(token && { token }), ...(queryId && { query_id: queryId }) },
      {
        onSuccess: () => {
          setToken('')
          setQueryId('')
        },
      },
    )
  }

  const hasAnyOverride = Boolean(data?.token_set || data?.query_id_set)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Investments — IBKR connection</CardTitle>
        <CardDescription>
          Only IBKR's Flex Web Service is supported right now. Without this connected, the Investments page has
          nothing to show.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <div className="flex items-center gap-1.5 text-sm">
              {data.configured ? (
                <>
                  <CheckCircle2 className="size-4 text-emerald-600" />
                  <span className="text-emerald-600">Connected</span>
                </>
              ) : (
                <span className="text-muted-foreground">Not connected</span>
              )}
            </div>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Flex Web Service token
              <Input
                type="password"
                autoComplete="off"
                placeholder={data.token_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Query ID
              <Input
                type="password"
                autoComplete="off"
                placeholder={data.query_id_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                value={queryId}
                onChange={(event) => setQueryId(event.target.value)}
              />
            </label>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleSave}
                disabled={(!token && !queryId) || setSettings.isPending}
              >
                Save
              </Button>
              {hasAnyOverride && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => clearSettings.mutate()}
                  disabled={clearSettings.isPending}
                >
                  Disconnect
                </Button>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function ProviderStatus({ label, configured }: { label: string; configured: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      {configured ? (
        <>
          <CheckCircle2 className="size-4 text-emerald-600" />
          <span className="text-emerald-600">{label} — Connected</span>
        </>
      ) : (
        <span className="text-muted-foreground">{label} — Not connected</span>
      )}
    </span>
  )
}

// A provider counts as connected once a key is available from *any*
// source — an override saved here, or `GEMINI_API_KEY`/`MISTRAL_API_KEY`
// in `.env` — the same `configured` flag `LlmUsageBanner` shows on
// Transactions, read here too so a `.env`-only key still shows as
// connected instead of looking unset just because nothing was typed here.
function LlmCategorizationCard() {
  const { data, isLoading } = useLlmSettings()
  const { data: usage } = useLlmUsage()
  const setSettings = useSetLlmSettings()
  const clearSettings = useClearLlmSettings()
  const [geminiKey, setGeminiKey] = useState('')
  const [mistralKey, setMistralKey] = useState('')

  function handleSave() {
    setSettings.mutate(
      { ...(geminiKey && { gemini_api_key: geminiKey }), ...(mistralKey && { mistral_api_key: mistralKey }) },
      {
        onSuccess: () => {
          setGeminiKey('')
          setMistralKey('')
        },
      },
    )
  }

  const hasAnyKey = Boolean(data?.gemini_key_set || data?.mistral_key_set)

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI categorization</CardTitle>
        <CardDescription>
          Powers the "AI suggest category" button on Transactions. Either key is optional, without one, that
          provider just isn't offered. Mistral and Gemini both have a free tier, but you must create an account and generate an API key to use them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <ProviderStatus label="Gemini" configured={usage?.gemini?.configured ?? false} />
              <ProviderStatus label="Mistral" configured={usage?.mistral?.configured ?? false} />
            </div>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Gemini API key
              <Input
                type="password"
                autoComplete="off"
                placeholder={data.gemini_key_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                value={geminiKey}
                onChange={(event) => setGeminiKey(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Mistral API key
              <Input
                type="password"
                autoComplete="off"
                placeholder={data.mistral_key_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                value={mistralKey}
                onChange={(event) => setMistralKey(event.target.value)}
              />
            </label>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleSave}
                disabled={(!geminiKey && !mistralKey) || setSettings.isPending}
              >
                Save
              </Button>
              {hasAnyKey && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => clearSettings.mutate()}
                  disabled={clearSettings.isPending}
                >
                  Clear both keys
                </Button>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

interface ExportRowProps {
  label: string
  description: string
  error?: string
  // One button per entry — a raw-statements row passes a single "Export"
  // action (it's already a file); a ledger/lots/postings row passes one
  // action per format (JSON, CSV) so both render side by side.
  exports: { label: string; onExport: () => void | Promise<void> }[]
}

function ExportRow({ label, description, error, exports }: ExportRowProps) {
  const [pendingLabel, setPendingLabel] = useState<string | null>(null)

  function handleClick(exportLabel: string, run: () => void | Promise<void>) {
    return async () => {
      setPendingLabel(exportLabel)
      try {
        await run()
      } finally {
        setPendingLabel(null)
      }
    }
  }

  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
        {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        {exports.map((entry) => (
          <Button
            key={entry.label}
            variant="outline"
            size="sm"
            onClick={handleClick(entry.label, entry.onExport)}
            disabled={pendingLabel !== null}
          >
            <Download className="size-3.5" />
            {entry.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

// Every export on the dashboard, in one place — the ledgers and postings
// each also have a page-local export button right next to the data they
// come from (Transactions, Allocation's Data quality panel), for whoever's
// already looking at that data. This tab exists for the opposite case:
// you know you want a backup or a raw file, and don't want to remember
// which page it lives on.
function ExportTab() {
  const [errors, setErrors] = useState<Record<string, string>>({})

  function withErrorHandling(key: string, run: () => Promise<void>) {
    return async () => {
      try {
        await run()
        setErrors((prev) => {
          const { [key]: _removed, ...rest } = prev
          return rest
        })
      } catch (error) {
        setErrors((prev) => ({ ...prev, [key]: error instanceof Error ? error.message : 'Export failed' }))
      }
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Investments</CardTitle>
          <CardDescription>Everything behind the Performance, Allocation, and Taxes pages.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ExportRow
            label="Ledger"
            description="Every deposit, withdrawal, buy, sell, dividend, fee, and split ever synced."
            error={errors.investmentsLedger}
            exports={[
              {
                label: 'JSON',
                onExport: withErrorHandling('investmentsLedger', async () => {
                  downloadJson(await api.ledgerExport(), `investments-ledger-${exportStamp()}.json`)
                }),
              },
              {
                label: 'CSV',
                onExport: withErrorHandling('investmentsLedger', async () => {
                  downloadCsv(await api.ledgerExport(), `investments-ledger-${exportStamp()}.csv`)
                }),
              },
            ]}
          />
          <ExportRow
            label="Lots"
            description="Every open and closed lot, plus the per-symbol rollup shown on Allocation. CSV downloads all three as separate files."
            error={errors.lots}
            exports={[
              {
                label: 'JSON',
                onExport: withErrorHandling('lots', async () => {
                  downloadJson(await api.lots(), `investments-lots-${exportStamp()}.json`)
                }),
              },
              {
                label: 'CSV',
                onExport: withErrorHandling('lots', async () => {
                  const lots = await api.lots()
                  const stamp = exportStamp()
                  downloadCsv(lots.open_lots, `investments-lots-open-${stamp}.csv`)
                  downloadCsv(lots.closed_lots, `investments-lots-closed-${stamp}.csv`)
                  downloadCsv(lots.symbol_rollup, `investments-lots-symbol-rollup-${stamp}.csv`)
                }),
              },
            ]}
          />
          <ExportRow
            label="Raw statements"
            description="Every Flex statement archived from a sync, verbatim, as a .zip of the original XML files."
            exports={[{ label: 'Export', onExport: () => downloadFromUrl('/api/statements/export') }]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Accounting</CardTitle>
          <CardDescription>Everything behind Transactions, Budget, Goals, and Net Worth.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <ExportRow
            label="Ledger (raw)"
            description="Every posting exactly as imported — before transfer rules, overrides, splits, or merges."
            error={errors.accountingLedger}
            exports={[
              {
                label: 'JSON',
                onExport: withErrorHandling('accountingLedger', async () => {
                  downloadJson(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.json`)
                }),
              },
              {
                label: 'CSV',
                onExport: withErrorHandling('accountingLedger', async () => {
                  downloadCsv(await accountingApi.ledgerExport(), `accounting-ledger-${exportStamp()}.csv`)
                }),
              },
            ]}
          />
          <ExportRow
            label="Postings (resolved)"
            description="The same postings after every rule and override is applied — what Transactions actually shows."
            error={errors.postings}
            exports={[
              {
                label: 'JSON',
                onExport: withErrorHandling('postings', async () => {
                  downloadJson(await accountingApi.postings(), `accounting-postings-${exportStamp()}.json`)
                }),
              },
              {
                label: 'CSV',
                onExport: withErrorHandling('postings', async () => {
                  downloadCsv(await accountingApi.postings(), `accounting-postings-${exportStamp()}.csv`)
                }),
              },
            ]}
          />
          <ExportRow
            label="Raw statements"
            description="Every bank/card CSV or statement PDF you've ever uploaded, verbatim, as a .zip."
            exports={[{ label: 'Export', onExport: () => downloadFromUrl('/api/accounting/statements/export') }]}
          />
        </CardContent>
      </Card>
    </div>
  )
}

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'connections'
  const setTab = (value: string) => setSearchParams(value === 'connections' ? {} : { tab: value })

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Settings" />
      <div className="mx-auto max-w-2xl px-8 py-8">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="connections">Connections</TabsTrigger>
            <TabsTrigger value="export">Export</TabsTrigger>
          </TabsList>
          <TabsContent value="connections" className="space-y-6 pt-6">
            <IbkrConnectionCard />
            <LlmCategorizationCard />
          </TabsContent>
          <TabsContent value="export" className="pt-6">
            <ExportTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
