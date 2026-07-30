import { type ReactNode, useId } from 'react'
import { cn } from '@/lib/utils'

/**
 * A label bound to the control it names, by id rather than by nesting.
 *
 * Nesting a control inside `<label>` only associates the two when the control
 * is a *labelable* element the browser can see. Most controls here are
 * components — a `Select` that renders a button-based trigger, a
 * `NumberInput` that wraps its `<input>` a level down — so the browser
 * associated nothing and a screen reader read the control unnamed. Handing
 * the id to the child makes the association explicit and survives the
 * component boundary.
 *
 * The child is a function so the id is generated once, here, rather than
 * spelled at every call site: the same form rendered twice on one page must
 * not hand both copies the same id.
 *
 * The defaults are the stacked-caption-above-control shape almost every form
 * here already used. `className` lands on the wrapper and `labelClassName` on
 * the label, both merged, so a call site that laid its `<label>` out
 * differently keeps exactly the classes it had.
 */
export function Field({
  label,
  className,
  labelClassName,
  children,
}: {
  label: ReactNode
  className?: string
  labelClassName?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={id} className={cn('text-xs text-muted-foreground', labelClassName)}>
        {label}
      </label>
      {children(id)}
    </div>
  )
}
