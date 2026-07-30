/**
 * One editable leg of a split, before it has been submitted.
 *
 * `amount` is the raw string the number input carries rather than a number,
 * so a half-typed value like `-` or `1.` survives a keystroke instead of
 * collapsing to `NaN` and snapping the caret back. Callers parse it only when
 * they total the legs up or send them.
 *
 * Shared by the two places a split gets drafted — the Transactions split
 * dialog and the paystub card's proposed-split editor — which build the same
 * shape and post it to the same endpoint.
 */
export interface DraftLeg {
  /** Stable for as long as this leg exists; see `newDraftLeg`. */
  id: string
  amount: string
  categoryId: string | null
  subcategoryId: string | null
  description: string
}

// A draft leg has no natural key. Its amount, category and description are
// all user-editable and all legitimately collide — "add leg" twice in a row
// produces two legs that are equal in every field — so nothing derived from
// its contents can identify it. Identity therefore has to be *assigned* when
// the leg is created and then carried, which is what this counter does.
//
// This matters because the legs are a list of focusable, individually
// removable rows. Keyed by array position, removing leg 0 makes React reuse
// leg 0's DOM nodes for what used to be leg 1: the caret jumps, an in-progress
// selection lands on the wrong input, and the row the user was editing is not
// the row that ends up submitted. Keyed by an assigned id, React removes the
// node that actually went away and leaves every surviving row untouched.
//
// A counter rather than `crypto.randomUUID()`: these ids never leave the
// browser, never reach the API, and never need to be unguessable — they only
// need to be distinct within one component's lifetime, which a counter
// guarantees deterministically and without a crypto dependency the test
// environment would have to provide.
let nextDraftLegId = 0

/** Mint a draft leg, assigning it the identity React needs to track its row. */
export function newDraftLeg(fields: Omit<DraftLeg, 'id'>): DraftLeg {
  nextDraftLegId += 1
  return { id: `leg-${nextDraftLegId}`, ...fields }
}
