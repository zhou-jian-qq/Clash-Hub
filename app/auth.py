"""管理后台鉴权：环境变量密码、JWT Cookie / Bearer。"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# 由 lifespan 调用 init_secret_key() 后设置；启动前默认为空，JWT 操作需在初始化后进行
_SECRET_KEY: str = ""

# 缓存来自 DB 的 bcrypt 哈希（Setting.admin_password_hash）；None 表示使用明文环境变量校验
_password_hash: str | None = None

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


# ─── 初始化 ──────────────────────────────────────────────────────────────────

def init_secret_key(key: str) -> None:
    """应用启动时由 lifespan 调用，将持久化的 SECRET_KEY 写入模块级变量。"""
    global _SECRET_KEY
    _SECRET_KEY = key


def init_password_hash(hash_value: str | None) -> None:
    """应用启动或密码修改后，将 bcrypt 哈希缓存到模块级变量。"""
    global _password_hash
    _password_hash = hash_value or None


async def get_or_create_secret_key() -> str:
    """
    按优先级获取 SECRET_KEY：
    1. 环境变量 CLASH_HUB_SECRET_KEY
    2. Setting 表中的 secret_key
    3. 首次生成随机 key 并持久化到 Setting 表
    """
    env_key = os.environ.get("CLASH_HUB_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    from database import async_session
    from models import Setting
    async with async_session() as session:
        row = await session.get(Setting, "secret_key")
        if row and row.value:
            return row.value
        new_key = os.urandom(32).hex()
        session.add(Setting(key="secret_key", value=new_key))
        await session.commit()
        return new_key


async def load_password_hash() -> str | None:
    """从 Setting 表读取 admin_password_hash，供 lifespan 预热缓存。"""
    from database import async_session
    from models import Setting
    async with async_session() as session:
        row = await session.get(Setting, "admin_password_hash")
        return row.value if row and row.value else None


# ─── 口令校验 ─────────────────────────────────────────────────────────────────

def get_admin_password() -> str:
    """从环境变量 ADMIN_PASSWORD 读取管理员口令（默认 admin888）。"""
    return os.environ.get("ADMIN_PASSWORD", "admin888")


def verify_password(plain: str) -> bool:
    """
    校验明文口令：
    1. 若 Setting 中存有 admin_password_hash，走 bcrypt 校验
    2. 否则与环境变量明文做 compare_digest（防时序攻击）
    """
    if _password_hash:
        return pwd_context.verify(plain, _password_hash)
    admin_pw = get_admin_password()
    return secrets.compare_digest(plain.encode("utf-8"), admin_pw.encode("utf-8"))


def hash_password(plain: str) -> str:
    """将明文口令哈希为 bcrypt 字符串，供修改密码 API 存库。"""
    return pwd_context.hash(plain)


def is_weak_default_password() -> bool:
    """检测当前是否仍在使用默认弱口令且未设置哈希。"""
    if _password_hash:
        return False
    return get_admin_password() in ("admin888", "")


# ─── JWT ─────────────────────────────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    """签发 JWT（含 exp），用于 Cookie `ch_token` 或 Authorization Bearer。"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _SECRET_KEY, algorithm=ALGORITHM)


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
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[ALGORITHM])
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
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") == "admin":
            return payload
    except JWTError:
        pass
    return None
