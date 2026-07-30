import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InstitutionCombobox } from '@/components/accounting/InstitutionCombobox'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

// `getByLabelText` resolves the association the same way a screen reader does.
// Asserting on it — rather than on the presence of an `htmlFor` — is what
// distinguishes a real fix from an `htmlFor` pointing at nothing, which is the
// failure mode a lint rule alone cannot tell apart from success.
describe('Field', () => {
  it('names a plain input', () => {
    render(<Field label="Display name">{(id) => <Input id={id} defaultValue="Chase" />}</Field>)

    expect(screen.getByLabelText('Display name')).toHaveValue('Chase')
  })

  it('names a NumberInput, whose input is a level below the component', () => {
    render(<Field label="Opening balance">{(id) => <NumberInput id={id} value={12} onCommit={vi.fn()} />}</Field>)

    expect(screen.getByLabelText('Opening balance')).toBe(screen.getByRole('spinbutton'))
  })

  it('names an InstitutionCombobox, whose input is a level below the component', () => {
    render(
      <Field label="Institution">
        {(id) => <InstitutionCombobox id={id} value="Chase" onChange={vi.fn()} knownInstitutions={[]} />}
      </Field>,
    )

    expect(screen.getByLabelText('Institution')).toHaveValue('Chase')
  })

  it('names a Select, whose trigger is a button and so was never labelled by nesting', () => {
    render(
      <Field label="Currency">
        {(id) => (
          <Select value="USD">
            <SelectTrigger id={id}>
              <SelectValue items={{ USD: 'USD' }} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="USD">USD</SelectItem>
            </SelectContent>
          </Select>
        )}
      </Field>,
    )

    expect(screen.getByLabelText('Currency')).toBe(screen.getByRole('combobox'))
  })

  it('gives two copies of the same field distinct ids', () => {
    render(
      <>
        <Field label="Amount">{(id) => <Input id={id} defaultValue="1" />}</Field>
        <Field label="Amount">{(id) => <Input id={id} defaultValue="2" />}</Field>
      </>,
    )

    const [first, second] = screen.getAllByLabelText('Amount')
    expect(first.id).not.toBe(second.id)
  })
})
