import { useState } from 'react'
import { InstitutionCombobox } from '@/components/accounting/InstitutionCombobox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCurrencies } from '@/hooks/useAccountingData'
import type { Account, AccountKind, CurrencyCode } from '@/types/accounting'

// Every real, importable account kind — excludes the virtual
// `income_source`/`expense_payee` placeholders and `external_investment`
// (owned by `trades`, never created here) and `other_asset` (that's a
// manually-entered net-worth line, not an importable account).
const ACCOUNT_KINDS: AccountKind[] = ['checking', 'savings', 'credit_card', 'vault', 'cash', 'loan']
const ACCOUNT_KIND_ITEMS: Record<string, string> = Object.fromEntries(ACCOUNT_KINDS.map((kind) => [kind, kind]))
const KIND_LABELS: Record<AccountKind, string> = {
  checking: 'Checking',
  savings: 'Savings',
  credit_card: 'Credit Card',
  vault: 'Vault',
  cash: 'Cash',
  loan: 'Loan',
  income_source: 'Income Source',
  expense_payee: 'Expense Payee',
  external_investment: 'External Investment',
  other_asset: 'Other Asset',
}
const NO_PARENT = '__none__'

function deriveAccountId(institution: string, kind: AccountKind, last4: string): string {
  return `${institution.toLowerCase().replace(/\s+/g, '-')}:${kind}:${last4}`
}

function deriveName(institution: string, kind: AccountKind, last4: string): string {
  return last4 ? `${institution} ${KIND_LABELS[kind]} (...${last4})` : `${institution} ${KIND_LABELS[kind]}`
}

export interface AccountFormValue {
  institution: string
  kind: AccountKind
  currency: CurrencyCode
  last4: string
  accountId: string
  name: string
  parentAccountId: string | null
  openingBalance: string
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

  // Institution/kind/last-4-digits fully determine the account id and (until
  // the user overrides it) the display name too — recomputed here rather
  // than left to the caller, so "add an account" only ever needs the last 4
  // digits off a statement, never a hand-typed id in the `institution:kind:1234`
  // convention.
  function updateIdentity(patch: Partial<Pick<AccountFormValue, 'institution' | 'kind' | 'last4'>>) {
    const next = { ...value, ...patch }
    const accountId = deriveAccountId(next.institution, next.kind, next.last4)
    const name = nameEdited ? value.name : deriveName(next.institution, next.kind, next.last4)
    // A previously chosen parent belongs to the old institution's account
    // list — carrying it over silently once the institution changes would
    // point a vault at a parent from the wrong bank.
    const parentAccountId = patch.institution !== undefined ? null : next.parentAccountId
    onChange({ ...next, accountId, name, parentAccountId })
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
        <Select value={value.kind} onValueChange={(next) => next && updateIdentity({ kind: next as AccountKind })} disabled={locked}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue items={ACCOUNT_KIND_ITEMS} />
          </SelectTrigger>
          <SelectContent>
            {ACCOUNT_KINDS.map((kind) => (
              <SelectItem key={kind} value={kind}>
                {kind}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Currency
        <Select value={value.currency} onValueChange={(next) => next && onChange({ ...value, currency: next as CurrencyCode })} disabled={locked}>
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
      {showOpeningBalance && (
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
