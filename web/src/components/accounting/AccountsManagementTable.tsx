import { useState } from 'react'
import { AlertTriangle, Pencil, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { AccountForm, type AccountFormValue } from '@/components/accounting/AccountForm'
import { useSortableRows } from '@/hooks/useSortableRows'
import {
  useCreateAccount,
  useDeleteAccount,
  useSetOpeningBalance,
  useSupportedImportKinds,
  useUpdateAccount,
} from '@/hooks/useAccountingData'
import type { Account } from '@/types/accounting'

// The two placeholder counterparties every posting starts pointed at (see
// `accounting.store.UNCATEGORIZED_EXPENSE_ACCOUNT_ID`/`UNCATEGORIZED_INCOME_ACCOUNT_ID`)
// aren't a real account or counterparty a user manages — they're re-seeded
// by the backend if ever missing — so they're the only accounts hidden here.
// Every other `income_source`/`expense_payee` counterparty a user creates
// (an employer, a payee) is a normal row, manageable the same as any account.
const SYSTEM_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

function emptyDraft(): AccountFormValue {
  return {
    institution: '',
    kind: 'checking',
    currency: 'USD',
    last4: '',
    accountId: '',
    name: '',
    parentAccountId: null,
    openingBalance: '',
  }
}

function AccountDialog({
  title,
  initial,
  locked,
  knownInstitutions,
  parentAccountOptions,
  showOpeningBalance,
  onClose,
  onSave,
}: {
  title: string
  initial: AccountFormValue
  locked: boolean
  knownInstitutions: string[]
  parentAccountOptions: Account[]
  showOpeningBalance: boolean
  onClose: () => void
  onSave: (value: AccountFormValue) => void
}) {
  const [draft, setDraft] = useState(initial)
  const canSave = locked ? draft.name.length > 0 : draft.institution && draft.kind && draft.last4 && draft.name

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <AccountForm
          value={draft}
          onChange={setDraft}
          knownInstitutions={knownInstitutions}
          parentAccountOptions={parentAccountOptions}
          locked={locked}
          showOpeningBalance={showOpeningBalance}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!canSave}
            onClick={() => {
              onSave(draft)
              onClose()
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function AccountsManagementTable({
  accounts,
  accountIdsWithPostings,
}: {
  accounts: Record<string, Account>
  accountIdsWithPostings: Set<string>
}) {
  const createAccount = useCreateAccount()
  const updateAccount = useUpdateAccount()
  const deleteAccount = useDeleteAccount()
  const setOpeningBalance = useSetOpeningBalance()
  const { data: supportedImportKinds } = useSupportedImportKinds()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Account | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = Object.values(accounts).filter((account) => !SYSTEM_ACCOUNT_IDS.has(account.account_id))
  const parentAccountOptions = rows.filter((account) => account.kind !== 'vault')
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'name')
  const knownInstitutions = [...new Set(rows.map((account) => account.institution))].sort()
  const supportedKinds = new Set((supportedImportKinds ?? []).map((entry) => `${entry.institution}:${entry.account_kind}`))
  const isCounterpartyKind = (kind: Account['kind']) => kind === 'income_source' || kind === 'expense_payee'
  // A counterparty (employer, payee) is never imported into, so it never
  // needs a CSV parsing rule — only real, importable accounts do.
  const hasNoImporter = (account: Account) =>
    !isCounterpartyKind(account.kind) && !supportedKinds.has(`${account.institution}:${account.kind}`)

  async function handleCreate(value: AccountFormValue) {
    setError(null)
    try {
      await createAccount.mutateAsync({
        account_id: value.accountId,
        name: value.name,
        kind: value.kind,
        institution: value.institution,
        currency: value.currency,
        parent_account_id: value.parentAccountId,
        external_ref: null,
        meta: {},
      })
      const amount = Number.parseFloat(value.openingBalance)
      if (value.openingBalance.trim() && !Number.isNaN(amount)) {
        await setOpeningBalance.mutateAsync({
          accountId: value.accountId,
          openingBalance: {
            account_id: value.accountId,
            amount,
            as_of_date: new Date().toISOString(),
          },
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create account')
    }
  }

  async function handleUpdate(accountId: string, value: AccountFormValue) {
    setError(null)
    try {
      await updateAccount.mutateAsync({
        accountId,
        update: { name: value.name, institution: value.institution, kind: value.kind, currency: value.currency, meta: {} },
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update account')
    }
  }

  async function handleDelete(accountId: string) {
    setError(null)
    try {
      await deleteAccount.mutateAsync(accountId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete account')
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Institutions and accounts</CardTitle>
        <CardAction>
          <Button variant="outline" size="icon" onClick={() => setAdding(true)}>
            <Plus className="size-4" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-2">
        {error && <p className="text-xs text-destructive">{error}</p>}
        {sorted.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">No accounts yet — add one, or import a statement to auto-create it.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'institution'} desc={sort.desc} onClick={() => toggleSort('institution')}>
                  Institution
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'name'} desc={sort.desc} onClick={() => toggleSort('name')}>
                  Name
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'kind'} desc={sort.desc} onClick={() => toggleSort('kind')}>
                  Kind
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'currency'} desc={sort.desc} onClick={() => toggleSort('currency')}>
                  Currency
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
                  Account id
                </SortableTableHead>
                <TableHead className="w-16" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((account) => {
                const locked = accountIdsWithPostings.has(account.account_id)
                return (
                  <TableRow key={account.account_id}>
                    <TableCell className="text-muted-foreground">{account.institution}</TableCell>
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-1.5">
                        {account.name}
                        {hasNoImporter(account) && (
                          <Tooltip>
                            <TooltipTrigger>
                              <AlertTriangle className="size-3.5 text-amber-500" />
                            </TooltipTrigger>
                            <TooltipContent>
                              No CSV parsing rule registered for {account.institution} {account.kind} — imports for
                              this account must be added to the codebase first.
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{account.kind}</TableCell>
                    <TableCell className="text-muted-foreground">{account.currency}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{account.account_id}</TableCell>
                    <TableCell className="flex gap-1">
                      <Button variant="ghost" size="icon" onClick={() => setEditing(account)}>
                        <Pencil className="size-3.5 text-muted-foreground" />
                      </Button>
                      {!locked && (
                        <Button variant="ghost" size="icon" onClick={() => handleDelete(account.account_id)}>
                          <Trash2 className="size-3.5 text-muted-foreground" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      {adding && (
        <AccountDialog
          title="Add account"
          initial={emptyDraft()}
          locked={false}
          knownInstitutions={knownInstitutions}
          parentAccountOptions={parentAccountOptions}
          showOpeningBalance
          onClose={() => setAdding(false)}
          onSave={handleCreate}
        />
      )}
      {editing && (
        <AccountDialog
          title="Edit account"
          initial={{
            institution: editing.institution,
            kind: editing.kind,
            currency: editing.currency,
            last4: editing.account_id.split(':').pop() ?? '',
            accountId: editing.account_id,
            name: editing.name,
            parentAccountId: editing.parent_account_id,
            openingBalance: '',
          }}
          locked={accountIdsWithPostings.has(editing.account_id)}
          knownInstitutions={knownInstitutions}
          parentAccountOptions={parentAccountOptions.filter((account) => account.account_id !== editing.account_id)}
          showOpeningBalance={false}
          onClose={() => setEditing(null)}
          onSave={(value) => handleUpdate(editing.account_id, value)}
        />
      )}
    </Card>
  )
}
