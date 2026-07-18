import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Account } from '@/types/accounting'

const NO_COUNTERPARTY = '__none__'

export function CounterpartySelect({
  accounts,
  value,
  onChange,
  disabled,
}: {
  accounts: Account[]
  value: string | null
  onChange: (accountId: string | null) => void
  disabled?: boolean
}) {
  const items = {
    [NO_COUNTERPARTY]: 'None',
    ...Object.fromEntries(accounts.map((account) => [account.account_id, account.name])),
  }
  return (
    <Select
      value={value ?? NO_COUNTERPARTY}
      onValueChange={(next) => onChange(next === NO_COUNTERPARTY ? null : (next ?? null))}
      disabled={disabled}
    >
      <SelectTrigger size="sm" className="w-48">
        <SelectValue items={items} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_COUNTERPARTY}>None</SelectItem>
        {accounts.map((account) => (
          <SelectItem key={account.account_id} value={account.account_id}>
            {account.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
