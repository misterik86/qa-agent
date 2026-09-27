from pydantic import BaseModel, Field


class TestRunCreate(BaseModel):
    scenario_id: str | None = None
    suite_id: str | None = None
    credential_id: str | None = None
    name: str = "Без названия"
    url: str
    mode: str = "specific"  # specific | full_page
    script_text: str = ""
    consent: bool = False


class TestRunOut(BaseModel):
    id: str
    scenario_id: str | None
    suite_id: str | None = None
    name: str
    url: str
    mode: str
    status: str
    total_steps: int
    passed_steps: int
    failed_steps: int
    console_errors: list[str] = []
    error_message: str = ""
    duration_seconds: float = 0.0
    predicted_seconds: int = 0
    share_token: str | None = None
    started_at: object | None = None
    finished_at: object | None = None
    created_at: object | None = None


class TestStepOut(BaseModel):
    order: int
    action: str
    action_text: str = ""
    selector: str
    value: str
    expected: str
    status: str
    message: str
    console_error: str = ""
    screenshot: str | None = None
    delta: str = ""  # regressed | fixed | new | "" (unchanged)
    page: str = ""   # source URL for site-crawl runs


class TestRunDetail(BaseModel):
    id: str
    name: str
    url: str
    mode: str
    status: str
    status_label: str
    total_time_seconds: float
    total_steps: int
    passed_steps: int
    failed_steps: int
    console_errors: list[str]
    error_message: str = ""
    steps: list[TestStepOut]
    suite_id: str | None = None
    share_token: str | None = None
    delta: dict | None = None
    started_at: object | None = None
    finished_at: object | None = None