import { useState } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/layout/PageHeader'
import {
  useClearIbkrSettings,
  useIbkrSettings,
  useSetIbkrSettings,
} from '@/hooks/usePortfolioData'
import { useClearLlmSettings, useLlmSettings, useSetLlmSettings } from '@/hooks/useAccountingData'

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

function LlmCategorizationCard() {
  const { data, isLoading } = useLlmSettings()
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
          provider just isn't offered. Mistral and Gemeni both have a free tier, but you must create an account and generate an API key to use them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <>
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

export function SettingsPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Settings" />
      <div className="mx-auto max-w-2xl space-y-6 px-8 py-8">
        <IbkrConnectionCard />
        <LlmCategorizationCard />
      </div>
    </div>
  )
}
