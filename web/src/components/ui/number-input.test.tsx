import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { NumberInput } from '@/components/ui/number-input'

describe('NumberInput', () => {
  it('shows the value it was given', () => {
    render(<NumberInput value={7} onCommit={vi.fn()} />)

    expect(screen.getByRole('spinbutton')).toHaveValue(7)
  })

  it('lets a field be emptied without a zero growing back into the next digit', async () => {
    const user = userEvent.setup()
    render(<NumberInput value={7} onCommit={vi.fn()} />)
    const field = screen.getByRole('spinbutton')

    await user.clear(field)
    await user.type(field, '6')

    // Asserted on the raw text, not on `toHaveValue(6)`: a `<input
    // type="number">` holding "06" reads back as the number 6, which is
    // exactly the state this component exists to prevent. Coercing '' to 0 on
    // the keystroke that clears the field is what leaves the 0 behind.
    expect((field as HTMLInputElement).value).toBe('6')
  })

  it('reports nothing until the field is left', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberInput value={null} onCommit={onCommit} />)

    await user.type(screen.getByRole('spinbutton'), '42')

    expect(onCommit).not.toHaveBeenCalled()
  })

  it('reports the number once on blur', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberInput value={null} onCommit={onCommit} />)

    await user.type(screen.getByRole('spinbutton'), '42')
    await user.tab()

    expect(onCommit.mock.calls).toEqual([[42]])
  })

  it('reports a cleared field as null rather than zero', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberInput value={7} onCommit={onCommit} />)

    await user.clear(screen.getByRole('spinbutton'))
    await user.tab()

    expect(onCommit.mock.calls).toEqual([[null]])
  })

  it('commits on Enter without waiting for a click elsewhere', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberInput value={null} onCommit={onCommit} />)

    await user.type(screen.getByRole('spinbutton'), '3{Enter}')

    expect(onCommit.mock.calls).toEqual([[3]])
  })

  it('reports a non-finite entry as cleared rather than committing Infinity', async () => {
    const user = userEvent.setup()
    const onCommit = vi.fn()
    render(<NumberInput value={null} onCommit={onCommit} />)

    await user.type(screen.getByRole('spinbutton'), '1e999')
    await user.tab()

    expect(onCommit.mock.calls).toEqual([[null]])
  })

  it('fires the live callback per keystroke with the raw text', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<NumberInput value={null} onCommit={vi.fn()} onChange={onChange} />)

    await user.type(screen.getByRole('spinbutton'), '12')

    expect(onChange.mock.calls).toEqual([['1'], ['12']])
  })
})
