import { CircleHelp, Download } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '@/components/layout/PageHeader'
import { ConnectionStatus } from '@/components/shared/ConnectionStatus'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  useClearLlmSettings,
  useLlmConnectionStatus,
  useLlmSettings,
  useSetLlmSettings,
} from '@/hooks/useAccountingData'
import {
  type ConnectionState,
  useClearIbkrSettings,
  useIbkrConnectionStatus,
  useIbkrSettings,
  useSetIbkrSettings,
} from '@/hooks/usePortfolioData'
import { ACCOUNTING_API_BASE, accountingApi } from '@/lib/accountingApi'
import { api, TRADES_API_BASE } from '@/lib/api'
import { downloadCsv, downloadFromUrl, downloadJson, downloadMultipleCsv, exportStamp } from '@/lib/download'

// Walks through creating a Flex Query on IBKR's own site, since neither
// field means anything without one already existing there first.
function IbkrFlexQueryHelp() {
  return (
    <Dialog>
      <DialogTrigger className="inline-flex align-middle text-muted-foreground/70 hover:text-foreground">
        <CircleHelp className="size-3.5" />
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Setting up an IBKR Flex Query</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm text-muted-foreground">
          <p>
            In IBKR's Client Portal:{' '}
            <strong className="text-foreground">Performance &amp; Reports → Flex Queries</strong> → create a new{' '}
            <strong className="text-foreground">Activity Flex Query</strong>.
          </p>
          <p>Include these sections, with the maximum detail level for each:</p>
          <ul className="list-disc space-y-1 pl-5">
            <li>Trades</li>
            <li>Cash transactions</li>
            <li>Open positions</li>
            <li>Transfers</li>
          </ul>
          <p>
            Set <strong className="text-foreground">Date Period</strong> to the last{' '}
            <strong className="text-foreground">365 days</strong> — long enough to capture a full year of history
            without the query becoming slow to generate, and safe to re-run indefinitely since every sync just replays
            whatever the query returns.
          </p>
          <p>
            Once saved, IBKR shows a <strong className="text-foreground">Query ID</strong> — that's the "Query ID"
            field. Separately, under{' '}
            <strong className="text-foreground">Settings → Reporting → Flex Web Service</strong>, generate a token —
            that's the "Flex Web Service token" field. Both go below.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// Never shows a saved secret back — the backend only ever reports whether
// a field is set, never its value, so a field that's already configured
// shows as an empty box with a placeholder saying so, and typing a new
// value replaces it on save. This is the "somewhere in Settings" the
// Investments page's own "not connected" fallback links to.
function IbkrConnectionCard() {
  const { data, isLoading } = useIbkrSettings()
  const setSettings = useSetIbkrSettings()
  const clearSettings = useClearIbkrSettings()
  const connection = useIbkrConnectionStatus()
  const [token, setToken] = useState('')
  const [queryId, setQueryId] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)

  function handleSave() {
    setSettings.mutate(
      { ...(token && { token }), ...(queryId && { query_id: queryId }) },
      {
        onSuccess: () => {
          setToken('')
          setQueryId('')
          setSaveError(null)
        },
        onError: (error) => setSaveError(error instanceof Error ? error.message : 'Failed to save'),
      },
    )
  }

  const hasAnyOverride = Boolean(data?.token_set || data?.query_id_set)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          Investments — IBKR connection
          <IbkrFlexQueryHelp />
        </CardTitle>
        <CardDescription>
          Only IBKR's Flex Web Service is supported right now. Without this connected, the Investments page has nothing
          to show.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <ConnectionStatus state={connection.state} error={connection.error} />
            <Field label="Flex Web Service token">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  autoComplete="off"
                  placeholder={data.token_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                />
              )}
            </Field>
            <Field label="Query ID">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  autoComplete="off"
                  placeholder={data.query_id_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                  value={queryId}
                  onChange={(event) => setQueryId(event.target.value)}
                />
              )}
            </Field>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={handleSave} disabled={(!token && !queryId) || setSettings.isPending}>
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

function ProviderStatus({ label, state, error }: { label: string; state: ConnectionState; error: string | null }) {
  return (
    <span className="flex max-w-full min-w-0 items-center gap-1.5 sm:max-w-80">
      <span className="shrink-0 font-medium text-foreground">{label}</span>
      <ConnectionStatus state={state} error={error} />
    </span>
  )
}

// A provider counts as configured once a key is available from *any*
// source — an override saved here, or `GEMINI_API_KEY`/`MISTRAL_API_KEY`
// in `.env` — the same `configured` flag `LlmUsageBanner` shows on
// Transactions. "Connected" additionally requires the live verify call
// below to have actually succeeded — configured-but-wrong keys land on
// ConnectionStatus's "Can't authenticate" state instead.
function LlmCategorizationCard() {
  const { data, isLoading } = useLlmSettings()
  const setSettings = useSetLlmSettings()
  const clearSettings = useClearLlmSettings()
  const geminiConnection = useLlmConnectionStatus('gemini')
  const mistralConnection = useLlmConnectionStatus('mistral')
  const [geminiKey, setGeminiKey] = useState('')
  const [mistralKey, setMistralKey] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)

  function handleSave() {
    setSettings.mutate(
      { ...(geminiKey && { gemini_api_key: geminiKey }), ...(mistralKey && { mistral_api_key: mistralKey }) },
      {
        onSuccess: () => {
          setGeminiKey('')
          setMistralKey('')
          setSaveError(null)
        },
        onError: (error) => setSaveError(error instanceof Error ? error.message : 'Failed to save'),
      },
    )
  }

  const hasAnyKey = Boolean(data?.gemini_key_set || data?.mistral_key_set)

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI categorization</CardTitle>
        <CardDescription>
          Powers the "AI suggest category" button on Transactions. Either key is optional, without one, that provider
          just isn't offered. Mistral and Gemini both have a free tier, but you must create an account and generate an
          API key to use them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <ProviderStatus label="Gemini" state={geminiConnection.state} error={geminiConnection.error} />
              <ProviderStatus label="Mistral" state={mistralConnection.state} error={mistralConnection.error} />
            </div>
            <Field label="Gemini API key">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  autoComplete="off"
                  placeholder={data.gemini_key_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                  value={geminiKey}
                  onChange={(event) => setGeminiKey(event.target.value)}
                />
              )}
            </Field>
            <Field label="Mistral API key">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  autoComplete="off"
                  placeholder={data.mistral_key_set ? 'Already set — enter a new value to replace it' : 'Not set'}
                  value={mistralKey}
                  onChange={(event) => setMistralKey(event.target.value)}
                />
              )}
            </Field>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={handleSave} disabled={(!geminiKey && !mistralKey) || setSettings.isPending}>
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
                  await downloadMultipleCsv([
                    { rows: lots.open_lots, filename: `investments-lots-open-${stamp}.csv` },
                    { rows: lots.closed_lots, filename: `investments-lots-closed-${stamp}.csv` },
                    { rows: lots.symbol_rollup, filename: `investments-lots-symbol-rollup-${stamp}.csv` },
                  ])
                }),
              },
            ]}
          />
          <ExportRow
            label="Raw statements"
            description="Every Flex statement archived from a sync, verbatim, as a .zip of the original XML files."
            exports={[{ label: 'Export', onExport: () => downloadFromUrl(`${TRADES_API_BASE}/statements/export`) }]}
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
            exports={[{ label: 'Export', onExport: () => downloadFromUrl(`${ACCOUNTING_API_BASE}/statements/export`) }]}
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
