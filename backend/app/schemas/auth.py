from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)
    accepted_tos: bool = False
    accepted_privacy: bool = False


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str = ""


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    is_verified: bool
    is_admin: bool = False
    plan: str
    plan_expires_at: object | None = None
    usage_percent: float | None = None
    locale: str = "ru"

    class Config:
        from_attributes = True