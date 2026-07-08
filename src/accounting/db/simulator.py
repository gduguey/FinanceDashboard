"""Saved inputs to the compound-interest projector."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import CompoundingFrequency, CurrencyCode
from db.base import MONEY, Base, check_in_sql


class SimulatorScenario(Base):
    """A saved set of inputs to the compound-interest projector (see `dashboard.simulator.project`)."""

    __tablename__ = "simulator_scenarios"
    __table_args__ = (
        CheckConstraint(check_in_sql("currency", get_args(CurrencyCode)), name="currency"),
        CheckConstraint(
            check_in_sql("compounding_frequency", get_args(CompoundingFrequency)),
            name="compounding_frequency",
        ),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scenario_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    initial_capital: Mapped[float] = mapped_column(MONEY)
    monthly_contribution: Mapped[float] = mapped_column(MONEY)
    horizon_years: Mapped[float]
    annual_rate_pct: Mapped[float]
    compounding_frequency: Mapped[str] = mapped_column(default="monthly")
    currency: Mapped[str] = mapped_column(default="USD")
