import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { AccountForm, type AccountFormValue } from '@/components/accounting/AccountForm'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useCreateAccount, useDeleteAccount, useUpdateAccount } from '@/hooks/useAccountingData'
import type { Account } from '@/types/accounting'

const VIRTUAL_KINDS = new Set(['income_source', 'expense_payee'])

function emptyDraft(): AccountFormValue {
  return { institution: '', kind: 'checking', currency: 'USD', accountId: '', name: '' }
}

function AccountDialog({
  title,
  initial,
  locked,
  knownInstitutions,
  onClose,
  onSave,
}: {
  title: string
  initial: AccountFormValue
  locked: boolean
  knownInstitutions: string[]
  onClose: () => void
  onSave: (value: AccountFormValue) => void
}) {
  const [draft, setDraft] = useState(initial)
  const canSave = locked ? draft.name.length > 0 : draft.institution && draft.kind && draft.accountId && draft.name

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <AccountForm value={draft} onChange={setDraft} knownInstitutions={knownInstitutions} locked={locked} />
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
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Account | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rows = Object.values(accounts).filter((account) => !VIRTUAL_KINDS.has(account.kind))
  const { sorted, sort, toggleSort } = useSortableRows(rows, 'name')
  const knownInstitutions = [...new Set(rows.map((account) => account.institution))].sort()

  async function handleCreate(value: AccountFormValue) {
    setError(null)
    try {
      await createAccount.mutateAsync({
        account_id: value.accountId,
        name: value.name,
        kind: value.kind,
        institution: value.institution,
        currency: value.currency,
        parent_account_id: null,
        external_ref: null,
        meta: {},
      })
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
                    <TableCell className="font-medium">{account.name}</TableCell>
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
            accountId: editing.account_id,
            name: editing.name,
          }}
          locked={accountIdsWithPostings.has(editing.account_id)}
          knownInstitutions={knownInstitutions}
          onClose={() => setEditing(null)}
          onSave={(value) => handleUpdate(editing.account_id, value)}
        />
      )}
    </Card>
  )
}
