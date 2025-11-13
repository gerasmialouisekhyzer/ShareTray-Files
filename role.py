import os
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import database

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "supersecret_dev_key_change_me")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")
router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[str] = None
    role: Optional[str] = None

class UserBase(BaseModel):
    username: str
    role: str

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: str

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

async def get_user_by_username(username: str):
    user = await database.users_collection.find_one({"username": username})
    if user:
        user["id"] = str(user["_id"])
    return user

async def authenticate_user(username: str, password: str):
    user = await get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code = status.HTTP_401_UNAUTHORIZED,
        detail = "Could not validate credentials",
        headers = {"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None or role is None:
            raise credentials_exception
        token_data = TokenData(user_id=user_id, role=role)
    except JWTError:
        raise credentials_exception

    user = await database.users_collection.find_one({"_id": database.ObjectId(token_data.user_id)})
    if user is None:
        raise credentials_exception

    user["id"] = str(user["_id"])
    return user

def require_role(required_roles: List[str]):
    async def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user.get("role") not in required_roles:
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail = "Operation not permitted for role"
            )
        return current_user
    return role_checker

@router.post("/token", response_model=Token, tags=["users"])
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "Incorrect username or password",
            headers = {"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user["id"], "role": user["role"]})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/users", tags=["users"])
async def create_user(user_in: UserCreate):
    hashed = get_password_hash(user_in.password)
    user_doc = {
        "username": user_in.username,
        "hashed_password": hashed,
        "role": user_in.role,
        "created_at": datetime.utcnow(),
    }
    result = await database.users_collection.insert_one(user_doc)
    created = await database.users_collection.find_one({"_id": result.inserted_id})
    created["id"] = str(created["_id"])
    return {"id": created["id"], "username": created["username"], "role": created["role"]}

@router.get("/me", response_model=User, tags=["users"])
async def read_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "role": current_user["role"],
    }

@router.get("/donor-only", tags=["users"])
async def donor_only_endpoint(current_user: dict = Depends(require_role(["donor", "admin"]))):
    return {"message": f"Hello {current_user['username']}, you have donor/admin access."}

@router.delete("/admin/delete_all", tags=["users"])
async def admin_only_endpoint(current_user: dict = Depends(require_role(["admin"]))):
    return {"message": f"Admin {current_user['username']} did the admin action."}
