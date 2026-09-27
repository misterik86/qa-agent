"""Admin panel schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserOut(BaseModel):
    id: str
    email: str
    full_name: str
    plan: str
    plan_expires_at: object | None = None
    is_verified: bool
    is_admin: bool
    tests_used_this_month: int
    runs_count: int = 0
    created_at: object | None = None

    class Config:
        from_attributes = True


class AdminUserPatch(BaseModel):
    """All fields optional; only provided ones are applied."""
    plan: str | None = Field(default=None, pattern="^(trial|pro|enterprise|enterprise_trial|blocked)$")
    is_admin: bool | None = None
    is_verified: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)


class AdminStats(BaseModel):
    users_total: int
    users_verified: int
    users_blocked: int
    users_admins: int
    users_new_today: int
    runs_total: int
    runs_passed: int
    runs_failed: int
    runs_active: int
    runs_today: int
    payments_total: int
    payments_sum: int


class AdminRunOut(BaseModel):
    id: str
    owner_email: str
    url: str
    mode: str
    status: str
    verdict: str | None = None
    total_steps: int = 0
    passed_steps: int = 0
    failed_steps: int = 0
    created_at: object | None = None
    finished_at: object | None = None

    class Config:
        from_attributes = True


class AdminPaymentOut(BaseModel):
    id: str
    user_email: str
    plan: str
    amount: int
    currency: str
    status: str
    created_at: object | None = None

    class Config:
        from_attributes = True


class AdminAuditOut(BaseModel):
    id: str
    user_email: str | None = None
    event: str
    entity_type: str
    entity_id: str | None = None
    ip: str
    meta: str
    created_at: object | None = None

    class Config:
        from_attributes = True
