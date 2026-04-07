"""管理后台鉴权：环境变量密码、JWT Cookie / Bearer。"""

import os
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SECRET_KEY = os.urandom(32).hex()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def get_admin_password() -> str:
    """从环境变量 ADMIN_PASSWORD 读取管理员口令（默认 admin888）。"""
    return os.environ.get("ADMIN_PASSWORD", "admin888")


def verify_password(plain: str) -> bool:
    """校验明文口令是否与当前管理员口令一致（明文比较，非哈希）。"""
    return plain == get_admin_password()


def create_access_token(data: dict) -> str:
    """签发 JWT（含 exp），用于 Cookie `ch_token` 或 Authorization Bearer。"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """依赖注入：要求已登录且 role=admin；优先 Cookie `ch_token`，其次 Bearer。"""
    token = request.cookies.get("ch_token")
    if not token and credentials:
        token = credentials.credentials
        
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")


async def get_current_user_optional(request: Request):
    """可选登录态：有效 admin token 则返回 payload，否则 None（用于前端路由）。"""
    token = request.cookies.get("ch_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") == "admin":
            return payload
    except JWTError:
        pass
    return None
