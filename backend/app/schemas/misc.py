from typing import Literal

from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    scenario_id: str
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    channel: Literal["telegram", "vk", "email"] = "email"
    chat_id: str | None = None
    vk_user_id: str | None = None
    vk_token: str | None = None
    email_recipient: str | None = None


class ScheduleOut(BaseModel):
    id: str
    scenario_id: str
    frequency: str
    channel: str
    chat_id: str | None
    vk_user_id: str | None
    email_recipient: str | None
    status: str
    last_run_at: object | None = None
    created_at: object | None = None


class WebhookCreate(BaseModel):
    url: str
    event: Literal["test_finished"] = "test_finished"
    secret: str = ""


class WebhookOut(BaseModel):
    id: str
    url: str
    event: str
    status: str
    created_at: object | None = None


class ApiKeyCreate(BaseModel):
    name: str = "Default"


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    active: bool
    created_at: object | None = None
    key: str | None = None


class PaymentCreate(BaseModel):
    plan: Literal["pro", "enterprise"] = "pro"