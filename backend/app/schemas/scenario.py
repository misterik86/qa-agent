from pydantic import BaseModel, Field


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str
    mode: str = "specific"  # specific | full_page
    script_text: str = ""
    description: str = ""
    tags: list[str] = []
    template_key: str | None = None


class ScenarioUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    script_text: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    change_comment: str | None = None


class ScenarioOut(BaseModel):
    id: str
    name: str
    url: str
    mode: str
    script_text: str
    status: str
    tags: list[str]
    current_version: int
    created_at: object | None = None
    updated_at: object | None = None


class ScenarioVersionOut(BaseModel):
    id: str
    version: int
    snapshot: str
    author_id: str
    change_comment: str
    created_at: object | None = None


class ValidationOut(BaseModel):
    valid: bool
    errors: list[str] = []
    suggestions: list[str] = []
    warnings: list[str] = []
    semantic: bool = False