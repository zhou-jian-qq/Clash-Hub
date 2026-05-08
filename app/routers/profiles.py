"""多订阅方案（SubProfile）路由：增删改查 + /sub/{profile_uuid} 覆盖。"""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_admin
from database import get_db
from models import SubProfile

router = APIRouter()


@router.get("/api/profiles")
async def list_profiles(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(SubProfile).order_by(SubProfile.id))
    return [p.to_dict() for p in result.scalars().all()]


@router.post("/api/profiles")
async def create_profile(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    profile = SubProfile(
        name=name[:100],
        uuid=str(uuid_mod.uuid4()),
        template_name=body.get("template_name", "标准版"),
        tag_filter=body.get("tag_filter", ""),
        custom_template_id=body.get("custom_template_id"),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile.to_dict()


@router.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    p = await db.get(SubProfile, profile_id)
    if not p:
        raise HTTPException(404, "方案不存在")
    body = await req.json()
    for field in ("name", "template_name", "tag_filter", "custom_template_id"):
        if field in body:
            setattr(p, field, body[field])
    await db.commit()
    return p.to_dict()


@router.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    p = await db.get(SubProfile, profile_id)
    if not p:
        raise HTTPException(404, "方案不存在")
    await db.delete(p)
    await db.commit()
    return {"ok": True}
