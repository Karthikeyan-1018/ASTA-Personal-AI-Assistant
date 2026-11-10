"""Pydantic schemas for single-owner authentication."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OwnerSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class OwnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class LoginResponse(BaseModel):
    owner: OwnerResponse
    expires_at: datetime
    csrf_token: str


class MeResponse(BaseModel):
    owner: OwnerResponse
    session_expires_at: datetime
