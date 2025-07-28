from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import  OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session

from app.models.model_user import User, UserRole

from app.auth import verify_password, create_access_token
from app.schemas.schema_user import UserCreate, UserRead, UserUpdate, Token
from app.crud.crud_users import get_user_by_email, get_user, create_user, list_all_users, update_user_crud, delete_user_crud
from app.models.model_user import User
from app.dependencies import get_current_user, require_role

router = APIRouter(tags=["Users"])

@router.post("/user/", response_model=UserRead)
async def register(user: UserCreate, session: AsyncSession = Depends(get_session)):
    existing = await get_user_by_email(session, user.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    # user.created_at = datetime.now(timezone.utc)
    # user.created_by = user.id
    db_user = await create_user(session, user)
    if not db_user:
        raise HTTPException(status_code=400, detail="Failed to create user")
    return db_user

@router.post("/user/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)):
    user = await get_user_by_email(session, form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(data={"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@router.get("/user/me", response_model=UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.get("/users/", response_model=list[UserRead])
async def list_users(skip: int = 0, limit: int = 100, session: AsyncSession = Depends(get_session)):
    return await list_all_users(session, skip=skip, limit=limit)

@router.get("/user/{user_id}", response_model=UserRead)
async def see_user(user_id: int, session: AsyncSession = Depends(get_session)):
    user = await get_user(session, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.patch("/user/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    session: AsyncSession = Depends(get_session),            
):     
    updated = await update_user_crud(session, user_id, user_update)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return updated

@router.delete("/user/{user_id}")
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),    
    user: User = Depends(require_role(UserRole.system_admin))
):
    deleted = await delete_user_crud(session, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"msg": "User deleted"}
