"""Suite / test-case schemas."""
from pydantic import BaseModel, Field


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str
    mode: str = "specific"
    credential_id: str | None = None


class SuitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = None
    mode: str | None = None
    credential_id: str | None = None


class CaseCreate(BaseModel):
    action: str = "click"
    selector: str = ""
    value: str = ""
    expected: str = ""
    expected_effect: str = ""
    enabled: bool = True


class CasePatch(BaseModel):
    action: str | None = None
    selector: str | None = None
    value: str | None = None
    expected: str | None = None
    expected_effect: str | None = None
    enabled: bool | None = None
    order: int | None = None


class CaseOut(BaseModel):
    id: str
    order: int
    action: str
    selector: str
    value: str
    expected: str
    expected_effect: str
    enabled: bool
    source: str

    class Config:
        from_attributes = True


class SuiteOut(BaseModel):
    id: str
    name: str
    url: str
    mode: str
    credential_id: str | None = None
    cases_count: int = 0
    enabled_cases_count: int = 0
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_run_at: object | None = None
    created_at: object | None = None


class SaveAsSuiteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
