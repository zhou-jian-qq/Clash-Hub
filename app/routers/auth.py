"""鉴权路由：登录、登出、修改管理员密码。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    create_access_token,
    hash_password,
    init_password_hash,
    verify_password,
    require_admin,
)
from database import get_db
from deps import set_setting
from models import Setting

router = APIRouter()


@router.post("/api/login")
async def login(req: Request, response: Response):
    body = await req.json()
    password = body.get("password", "")
    if not verify_password(password):
        raise HTTPException(status_code=401, detail="密码错误")
    token = create_access_token({"role": "admin"})
    response.set_cookie(key="ch_token", value=token, httponly=True, max_age=86400)
    return {"token": token}


@router.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("ch_token")
    return {"message": "已登出"}


@router.post("/api/settings/admin-password")
async def change_admin_password(
    req: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """修改管理员密码：将 bcrypt 哈希存入 Setting，此后不再依赖明文环境变量。"""
    body = await req.json()
    new_password = (body.get("password") or "").strip()
    if len(new_password) < 6:
        raise HTTPException(400, "密码长度至少 6 位")
    new_hash = hash_password(new_password)
    s = await db.get(Setting, "admin_password_hash")
    if s:
        s.value = new_hash
    else:
        db.add(Setting(key="admin_password_hash", value=new_hash))
    await db.commit()
    init_password_hash(new_hash)
    return {"ok": True, "message": "密码已更新，哈希已持久化"}
