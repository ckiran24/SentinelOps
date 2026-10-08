from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import utc_timestamp
from .policy import ServiceName


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreate(StrictModel):
    title: str = Field(min_length=3, max_length=200)
    service: ServiceName
    severity: Literal["SEV1", "SEV2", "SEV3"] = "SEV2"
    description: str = Field(min_length=3, max_length=10000)
    scenario: Literal["checkout_regression", "database_saturation", "memory_leak", "unknown"] = (
        "unknown"
    )


class ApprovalRequest(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=2000)


class ExecuteRequest(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    service: str
    severity: str
    description: str
    scenario: str
    status: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_utc_timestamps(cls, value: datetime) -> datetime:
        return utc_timestamp(value)


class IncidentDetail(IncidentRead):
    hypothesis: dict | None
    evidence: list[dict]
    proposal: dict | None
    trace: list[dict]
    report: str | None
    audit: list[dict]
