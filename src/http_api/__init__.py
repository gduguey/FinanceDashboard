"""Wire-format vocabulary shared by every HTTP surface in this app, owned by neither ledger.

`accounting` and `trades` are deliberately independent — separate domains,
separate data, separate write paths, and no import between them (see
`docs/architecture.md`). That independence is about the ledgers. It was never
meant to extend to the shape of an HTTP response, and letting it do so is what
produced the drift this package exists to end: two hand-written page
envelopes with the same four field names, whose `total` counted different
things, described by one section of `docs/http-api-contract.md` as though
they were one contract.

So this sits beside them rather than inside either, the same way `db` holds
the persistence vocabulary both of them build on. A module here may not
import from `accounting` or `trades`; both may import from it.
"""
