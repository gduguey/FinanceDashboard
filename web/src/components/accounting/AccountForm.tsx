import { useState } from 'react'
import { InstitutionCombobox } from '@/components/accounting/InstitutionCombobox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCurrencies } from '@/hooks/useAccountingData'
import { useBrokerConnections } from '@/hooks/usePortfolioData'
import { ACCOUNT_KIND_LABELS } from '@/lib/accountKinds'
import type { Account, AccountKind, CurrencyCode } from '@/types/accounting'

// Every real, importable account kind, plus `external_investment` — a
// placeholder that either mirrors the tracked portfolio in Investments or
// is valued manually like any other account (see the pull-vs-manual choice
// below). Excludes the virtual `income_source`/`expense_payee` placeholders
// and `other_asset` (that's a manually-entered net-worth line, not an
// importable account).
const ACCOUNT_KINDS: AccountKind[] = [
  'checking',
  'savings',
  'credit_card',
  'vault',
  'cash',
  'loan',
  'external_investment',
]
const ACCOUNT_KIND_ITEMS: Record<string, string> = Object.fromEntries(
  ACCOUNT_KINDS.map((kind) => [kind, ACCOUNT_KIND_LABELS[kind]]),
)
const NO_PARENT = '__none__'

function deriveName(institution: string, kind: AccountKind, last4: string): string {
  const label = ACCOUNT_KIND_LABELS[kind]
  return last4 ? `${institution} ${label} (...${last4})` : `${institution} ${label}`
}

export interface AccountFormValue {
  institution: string
  kind: AccountKind
  currency: CurrencyCode
  last4: string
  name: string
  parentAccountId: string | null
  openingBalance: string
  // Only meaningful when `kind === 'external_investment'` — the id of the
  // broker connection this account's value is pulled from (see
  // `accounting.dashboard.net_worth.base_balance`), or `null` to value it
  // manually from its own opening balance, same as any other account.
  // Replaces the old free-text `externalRef`, whose only legal value was
  // the literal `'trades'`: the backend column is a real foreign key into
  // `trades.broker_connections` now, so this has to be a connection that
  // exists, not a magic string.
  brokerConnectionId: string | null
}

export function AccountForm({
  value,
  onChange,
  knownInstitutions,
  parentAccountOptions,
  locked,
  showOpeningBalance,
}: {
  value: AccountFormValue
  onChange: (value: AccountFormValue) => void
  knownInstitutions: string[]
  parentAccountOptions: Account[]
  locked: boolean
  // Doubles as "this is a brand-new account, not an edit" — the backend has
  // no way to change an existing account's parent after creation yet, so
  // the parent-account picker is gated on the same flag as the one-time
  // opening balance rather than adding a second prop for the same case.
  showOpeningBalance: boolean
}) {
  const [nameEdited, setNameEdited] = useState(false)
  const { data: currencies } = useCurrencies()
  const currencyItems = Object.fromEntries((currencies ?? []).map((currency) => [currency.code, currency.code]))
  // Scoped to the institution currently chosen in the form — a vault's
  // parent is always an account at the same bank, so an account from a
  // different institution is never a valid pick here.
  const institutionParentOptions = parentAccountOptions.filter((account) => account.institution === value.institution)
  const parentAccountItems = {
    [NO_PARENT]: 'None',
    ...Object.fromEntries(institutionParentOptions.map((account) => [account.account_id, account.name])),
  }

  // Institution/kind/last-4-digits determine the display name (until the
  // user overrides it) — recomputed here rather than left to the caller.
  // The account id itself is no longer derived here at all: the server
  // generates it on creation (see `AccountCreate`), so there's nothing to
  // recompute when identity fields change.
  function updateIdentity(patch: Partial<Pick<AccountFormValue, 'institution' | 'kind' | 'last4'>>) {
    const next = { ...value, ...patch }
    const name = nameEdited ? value.name : deriveName(next.institution, next.kind, next.last4)
    // A previously chosen parent belongs to the old institution's account
    // list — carrying it over silently once the institution changes would
    // point a vault at a parent from the wrong bank.
    const parentAccountId = patch.institution !== undefined ? null : next.parentAccountId
    onChange({ ...next, name, parentAccountId })
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Institution
        {locked ? (
          <Input className="w-40" value={value.institution} disabled />
        ) : (
          <InstitutionCombobox
            value={value.institution}
            onChange={(institution) => updateIdentity({ institution })}
            knownInstitutions={knownInstitutions}
          />
        )}
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Account kind
        <Select
          value={value.kind}
          onValueChange={(next) => next && updateIdentity({ kind: next as AccountKind })}
          disabled={locked}
        >
          <SelectTrigger size="sm" className="w-32">
            <SelectValue items={ACCOUNT_KIND_ITEMS} />
          </SelectTrigger>
          <SelectContent>
            {ACCOUNT_KINDS.map((kind) => (
              <SelectItem key={kind} value={kind}>
                {ACCOUNT_KIND_LABELS[kind]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Currency
        <Select
          value={value.currency}
          onValueChange={(next) => next && onChange({ ...value, currency: next as CurrencyCode })}
          disabled={locked}
        >
          <SelectTrigger size="sm" className="w-20">
            <SelectValue items={currencyItems} />
          </SelectTrigger>
          <SelectContent>
            {(currencies ?? []).map((currency) => (
              <SelectItem key={currency.code} value={currency.code}>
                {currency.code}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      {value.kind === 'external_investment' ? (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Label
          <Input
            className="w-24"
            value={value.last4}
            maxLength={16}
            onChange={(event) => updateIdentity({ last4: event.target.value })}
            disabled={locked}
            placeholder="main"
          />
        </label>
      ) : (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Last 4 digits
          <Input
            className="w-24"
            value={value.last4}
            maxLength={4}
            onChange={(event) => updateIdentity({ last4: event.target.value.replace(/\D/g, '').slice(0, 4) })}
            disabled={locked}
            placeholder="1234"
          />
        </label>
      )}
      {value.kind === 'vault' && showOpeningBalance && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Parent account (optional)
          <Select
            value={value.parentAccountId ?? NO_PARENT}
            onValueChange={(next) => onChange({ ...value, parentAccountId: next === NO_PARENT ? null : next })}
            disabled={institutionParentOptions.length === 0}
          >
            <SelectTrigger size="sm" className="w-44">
              <SelectValue items={parentAccountItems} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PARENT}>None</SelectItem>
              {institutionParentOptions.map((account) => (
                <SelectItem key={account.account_id} value={account.account_id}>
                  {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Display name
        <Input
          className="w-52"
          value={value.name}
          onChange={(event) => {
            setNameEdited(true)
            onChange({ ...value, name: event.target.value })
          }}
        />
      </label>
      {value.kind === 'external_investment' && (
        <ExternalInvestmentSourceToggle
          value={value.brokerConnectionId}
          onChange={(brokerConnectionId) => onChange({ ...value, brokerConnectionId })}
        />
      )}
      {showOpeningBalance && !(value.kind === 'external_investment' && value.brokerConnectionId !== null) && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Opening balance (optional)
          <Input
            className="w-32"
            type="number"
            inputMode="decimal"
            value={value.openingBalance}
            onChange={(event) => onChange({ ...value, openingBalance: event.target.value })}
            placeholder="0.00"
          />
        </label>
      )}
    </div>
  )
}

// Only shown while creating/editing an `external_investment` account —
// lets the user choose between mirroring one of their synced broker
// connections in Investments and tracking this one manually like any other
// account. The "pull" option is offered per real connection, and disappears
// entirely when there are none: the backend link is a foreign key, so a
// connection that doesn't exist yet is not something this can offer.
function ExternalInvestmentSourceToggle({
  value,
  onChange,
}: {
  value: string | null
  onChange: (value: string | null) => void
}) {
  const { data: connections, isLoading } = useBrokerConnections()
  const available = connections ?? []

  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>Value comes from</span>
      <div className="flex flex-col gap-1.5">
        {available.map((connection) => (
          <label key={connection.connection_id} className="flex items-center gap-1.5">
            <input
              type="radio"
              name="external-investment-source"
              className="size-3.5 accent-current"
              checked={value === connection.connection_id}
              onChange={() => onChange(connection.connection_id)}
            />
            Pull from Investments ({connection.broker.toUpperCase()})
          </label>
        ))}
        {!isLoading && available.length === 0 && (
          <p className="text-[11px] text-amber-600">
            Nothing to pull from yet — no brokerage has been synced. Connect one in Settings and run a sync, or set this
            account's value manually below.
          </p>
        )}
        <label className="flex items-center gap-1.5">
          <input
            type="radio"
            name="external-investment-source"
            className="size-3.5 accent-current"
            checked={value === null}
            onChange={() => onChange(null)}
          />
          Set manually
        </label>
      </div>
    </div>
  )
}
