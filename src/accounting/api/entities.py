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
from accounting.models import AccountKind, CategoryClassification, CurrencyCode
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
