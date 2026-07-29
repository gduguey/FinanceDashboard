"""Saved inputs to the compound-interest projector."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import CompoundingFrequency
from db.base import MONEY, RATE, UUID7_DEFAULT, Base, Timestamped, check_in_sql
from db.models import CURRENCY_CODE_COLUMN


class SimulatorScenario(Base, Timestamped):
    """A saved set of inputs to the compound-interest projector (see `dashboard.simulator.project`)."""

    __tablename__ = "simulator_scenarios"
    __table_args__ = (
        CheckConstraint(
            check_in_sql("compounding_frequency", get_args(CompoundingFrequency)),
            name="compounding_frequency",
        ),
        UniqueConstraint("user_id", "natural_key", name="uq_simulator_scenarios_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    name: Mapped[str]
    initial_capital: Mapped[Decimal] = mapped_column(MONEY)
    monthly_contribution: Mapped[Decimal] = mapped_column(MONEY)
    horizon_years: Mapped[Decimal] = mapped_column(RATE)
    annual_rate_pct: Mapped[Decimal] = mapped_column(RATE)
    compounding_frequency: Mapped[str] = mapped_column(default="monthly")
    currency: Mapped[str] = mapped_column(ForeignKey(CURRENCY_CODE_COLUMN), default="USD")
