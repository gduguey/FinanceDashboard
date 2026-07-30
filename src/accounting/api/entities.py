"""The wire shape of every accounting entity an endpoint returns.

One class here per entity in `accounting.models` that reaches a client. They
are *mirrors*, not aliases: each restates the fields it puts on the wire, so
a field added to a domain model for internal reasons does not ship to every
client and into `web/src/types/schema.ts` without anyone deciding it should.
`_WireModel.from_domain` is the one seam that copies one across, and the
fields it drops are exactly the decoupling.

What is deliberately *not* mirrored is the vocabulary those models are
written in — `AccountKind`, `CurrencyCode`, `CategoryClassification` and the
other `Literal` aliases. Those are value sets, not shapes: they carry no
fields to leak, they appear in the schema inlined rather than as components,
and duplicating them would mean a new currency had to be added in two places
to be sayable. Adding a member is a deliberate vocabulary change either way.
See `docs/http-api-contract.md`.

Request bodies do not belong here — they live in `api_models` beside the
endpoint whose contract they are, because a request body is rarely the same
shape as the entity it creates (`AccountCreate` has no `account_id`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field

from accounting import models
from accounting.models import (
    AccountKind,
    CategoryClassification,
    CurrencyCode,
    DismissedSuggestionKind,
    PendingSuggestionSource,
    TransferLinkSource,
)
from db.money import Money


class _WireModel[DomainT: BaseModel](BaseModel):
    """Base for every mirror below, carrying the one conversion they all share.

    The type parameter is what keeps a mirror pinned to the one domain model
    it mirrors: `Tag.from_domain(some_account)` is a type error rather than a
    runtime surprise, even though both are `BaseModel`s.
    """

    @classmethod
    def from_domain(cls, domain: DomainT) -> Self:
        """Copy one domain entity onto this wire shape.

        Parameters
        ----------
        domain
            The `accounting.models` entity to mirror. Any field it carries
            that this model does not declare is dropped rather than
            forwarded — that is the whole point of the mirror.

        Returns
        -------
        Self
            The wire representation of `domain`.
        """
        return cls(**domain.model_dump())


class Currency(_WireModel[models.Currency]):
    """One supported currency's display metadata."""

    code: CurrencyCode
    symbol: str = Field(min_length=1)
    decimal_places: int = 2


class Account(_WireModel[models.Account]):
    """One place money can sit or be attributed to — a real account, a vault, or a virtual counterparty."""

    account_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: AccountKind
    institution: str = Field(min_length=1)
    currency: CurrencyCode
    last_four: str | None = None
    parent_account_id: str | None = None
    broker_connection_id: uuid.UUID | None = None
    meta: dict[str, str] = Field(default_factory=dict)
    closed: bool = False


class Category(_WireModel[models.Category]):
    """One node in the two-level category tree: a top-level category, or a subcategory of one."""

    category_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    classification: CategoryClassification
    parent_category_id: str | None = None
    color: str = Field(min_length=1)


class Tag(_WireModel[models.Tag]):
    """A cross-cutting label — a trip, a move, an event — independent of the category tree."""

    tag_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class OpeningBalance(_WireModel[models.OpeningBalance]):
    """The balance a real account already had the day before its postings start.

    One of the two mirrors a client also *sends*: `PUT
    /accounts/{account_id}/opening-balance` takes the whole entity, since
    every field of it is the caller's to set.
    """

    account_id: str = Field(min_length=1)
    amount: Money
    as_of_date: datetime

    def to_domain(self) -> models.OpeningBalance:
        """Convert this request-body balance into the domain model the repositories take.

        Returns
        -------
        models.OpeningBalance
            The domain representation of this opening balance.
        """
        return models.OpeningBalance(**self.model_dump())


class ManualTransfer(_WireModel[models.ManualTransfer]):
    """A user-recorded transfer between two of their own accounts, never derived from an import.

    Both amounts stay `gt=0` here, unlike most constraints on a mirror: this
    shape is a *request* body too (`api_models.AccountCloseRequest`), and the
    positivity of the two legs is the invariant that makes them directional
    at all — see `models.ManualTransfer`.
    """

    transfer_id: str = Field(min_length=1)
    date: datetime
    from_account_id: str = Field(min_length=1)
    to_account_id: str = Field(min_length=1)
    from_amount: Money = Field(gt=0)
    to_amount: Money = Field(gt=0)
    description: str = ""

    def to_domain(self) -> models.ManualTransfer:
        """Convert this request-body transfer into the domain model the repositories take.

        The only mirror that needs this direction: every other one is
        response-only, and a client that wants to *create* something posts a
        purpose-built body from `api_models` instead.

        Returns
        -------
        models.ManualTransfer
            The domain representation of this transfer.
        """
        return models.ManualTransfer(**self.model_dump())


class TransferRule(_WireModel[models.TransferRule]):
    """A user-maintained trigger/action pair for automatically resolving a posting's counterparty."""

    rule_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    account_id: str | None = None
    counterparty_account_id: str | None = None
    priority: int = 0
    description: str = ""
    active: bool = True
    excluded_transaction_ids: list[str] = Field(default_factory=list)
    version: int = 1


class TransferLink(_WireModel[models.TransferLink]):
    """A confirmed pairing of two transactions as the two sides of one real-world transfer."""

    link_id: str = Field(min_length=1)
    transaction_id_a: str = Field(min_length=1)
    transaction_id_b: str = Field(min_length=1)
    source: TransferLinkSource = "manual"
    rule_id: str | None = None


class CategoryPattern(_WireModel[models.CategoryPattern]):
    """A user-maintained description-match pattern that *suggests* a category — never applies one silently."""

    pattern_id: str = Field(min_length=1)
    description_contains: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    subcategory_id: str | None = None
    priority: int = 0
    active: bool = True
    version: int = 1


class ManualOverride(_WireModel[models.ManualOverride]):
    """A user's direct edit to one posting, always winning over whatever a rule would have produced.

    A mirror a client also sends: `PUT /postings/{posting_id}/override` takes
    the whole shape, field-merged against whatever is already stored.
    """

    account_id: str | None = None
    category_id: str | None = None
    subcategory_id: str | None = None
    tag_ids: list[str] | None = None
    pending_source: PendingSuggestionSource | None = None
    pending_selected: bool = True
    pending_previous_category_id: str | None = None
    pending_previous_subcategory_id: str | None = None

    def to_domain(self) -> models.ManualOverride:
        """Convert this request-body override into the domain model the repositories take.

        Returns
        -------
        models.ManualOverride
            The domain representation of this override.
        """
        return models.ManualOverride(**self.model_dump())


class PostingSplitLeg(_WireModel[models.PostingSplitLeg]):
    """One piece of a posting split into several independently-categorized legs."""

    amount: Money
    category_id: str | None = None
    subcategory_id: str | None = None
    description: str = ""


class PostingSplit(_WireModel[models.PostingSplit]):
    """A user's decision to break one posting into several legs, keyed by the original posting's id.

    A mirror a client also sends: `PUT /postings/{posting_id}/split` takes the
    legs, and the posting id comes from the path.
    """

    posting_id: str = Field(min_length=1)
    legs: list[PostingSplitLeg] = Field(min_length=2)

    def to_domain(self) -> models.PostingSplit:
        """Convert this request-body split into the domain model the repositories take.

        `model_dump` flattens the legs to dicts on the way, so the nested
        `PostingSplitLeg` mirrors need no conversion of their own.

        Returns
        -------
        models.PostingSplit
            The domain representation of this split.
        """
        return models.PostingSplit(**self.model_dump())


class PostingMerge(_WireModel[models.PostingMerge]):
    """A user's decision that two or more imported transactions are the same real-world event."""

    merge_id: str = Field(min_length=1)
    kept_transaction_id: str = Field(min_length=1)
    duplicate_transaction_ids: list[str] = Field(min_length=1)
    description: str | None = None


class DismissedSuggestion(_WireModel[models.DismissedSuggestion]):
    """A user's decision that an auto-detected suggestion isn't relevant, archived rather than discarded."""

    suggestion_id: str = Field(min_length=1)
    kind: DismissedSuggestionKind
    description: str
    dismissed_at: datetime


class Posting(_WireModel[models.Posting]):
    """One leg of one economic event.

    `posted_at` and `description` are the *transaction's*, not this leg's —
    every leg of one transaction carries the same value for both.
    """

    posting_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    posted_at: datetime
    amount: Money
    currency: CurrencyCode
    category_id: str | None = None
    subcategory_id: str | None = None
    budget_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    description: str = ""
    meta: dict[str, str] = Field(default_factory=dict)
