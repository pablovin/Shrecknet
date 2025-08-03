from pydantic import BaseModel, EmailStr
from typing import Optional
from app.models.model_user import UserRole


class UserCreate(BaseModel):
    nickname: str
    email: EmailStr
    password: str
    role: UserRole
    image_url: Optional[str] = None
    timezone: Optional[str] = None


class UserRead(BaseModel):
    id: int
    nickname: str
    email: EmailStr
    role: UserRole
    image_url: Optional[str]
    timezone: Optional[str] = None

    class Config:
        orm_mode = True


class UserUpdate(BaseModel):
    nickname: Optional[str] = None
    password: Optional[str] = None
    role: Optional[UserRole] = None
    image_url: Optional[str] = None
    timezone: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str
