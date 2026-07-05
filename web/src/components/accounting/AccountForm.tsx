import { InstitutionCombobox } from '@/components/accounting/InstitutionCombobox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { AccountKind, CurrencyCode } from '@/types/accounting'

// Every real, importable account kind — excludes the virtual
// `income_source`/`expense_payee` placeholders and `external_investment`
// (owned by `trades`, never created here) and `other_asset` (that's a
// manually-entered net-worth line, not an importable account).
const ACCOUNT_KINDS: AccountKind[] = ['checking', 'savings', 'credit_card', 'vault', 'cash', 'loan']

export interface AccountFormValue {
  institution: string
  kind: AccountKind
  currency: CurrencyCode
  accountId: string
  name: string
}

export function AccountForm({
  value,
  onChange,
  knownInstitutions,
  locked,
}: {
  value: AccountFormValue
  onChange: (value: AccountFormValue) => void
  knownInstitutions: string[]
  locked: boolean
}) {
  const conventionHint = value.institution && value.accountId ? null : `e.g. ${(value.institution || 'chase').toLowerCase()}:${value.kind}:1234`

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Institution
        {locked ? (
          <Input className="w-40" value={value.institution} disabled />
        ) : (
          <InstitutionCombobox
            value={value.institution}
            onChange={(institution) => onChange({ ...value, institution })}
            knownInstitutions={knownInstitutions}
          />
        )}
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Account kind
        <Select value={value.kind} onValueChange={(next) => next && onChange({ ...value, kind: next as AccountKind })} disabled={locked}>
          <SelectTrigger size="sm" className="w-32">
            <SelectValue />
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
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="USD">USD</SelectItem>
            <SelectItem value="EUR">EUR</SelectItem>
          </SelectContent>
        </Select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Account id
        <Input
          className="w-44"
          value={value.accountId}
          onChange={(event) => onChange({ ...value, accountId: event.target.value })}
          disabled={locked}
          placeholder={conventionHint ?? undefined}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Display name
        <Input className="w-52" value={value.name} onChange={(event) => onChange({ ...value, name: event.target.value })} />
      </label>
    </div>
  )
}
