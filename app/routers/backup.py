"""配置备份与恢复：导出 ZIP / 导入 ZIP（不含 secret_key / admin_password_hash）。"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_admin
from database import get_db
from models import CustomTemplate, ImportBatch, ImportedNode, Setting, Subscription

router = APIRouter()

# 敏感键：导出时排除，导入时不覆盖
_SENSITIVE_KEYS = {"secret_key", "admin_password_hash"}

_VERSION = "1"


@router.get("/api/backup/export")
async def export_backup(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """打包并下载配置 ZIP（订阅 / 节点 / 设置 / 模板）。"""
    # 1. 收集数据
    subs_result = await db.execute(select(Subscription).order_by(Subscription.id))
    subs = [s.to_dict() for s in subs_result.scalars().all()]

    batches_result = await db.execute(select(ImportBatch).order_by(ImportBatch.id))
    batches = []
    for b in batches_result.scalars().all():
        bd = b.to_dict()
        nodes_result = await db.execute(
            select(ImportedNode).where(ImportedNode.batch_id == b.id).order_by(ImportedNode.sort_order)
        )
        bd["nodes"] = [n.to_dict() for n in nodes_result.scalars().all()]
        batches.append(bd)

    settings_result = await db.execute(select(Setting))
    settings = {
        s.key: s.value
        for s in settings_result.scalars().all()
        if s.key not in _SENSITIVE_KEYS
    }

    templates_result = await db.execute(select(CustomTemplate).order_by(CustomTemplate.id))
    templates = [{"name": t.name, "yaml_body": t.yaml_body} for t in templates_result.scalars().all()]

    manifest = {
        "version": _VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "subscriptions": len(subs),
            "import_batches": len(batches),
            "settings": len(settings),
            "templates": len(templates),
        },
    }

    # 2. 打包
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("subscriptions.json", json.dumps(subs, ensure_ascii=False, indent=2))
        zf.writestr("imports.json", json.dumps(batches, ensure_ascii=False, indent=2))
        zf.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
        zf.writestr("templates.json", json.dumps(templates, ensure_ascii=False, indent=2))
    buf.seek(0)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=clash_hub_backup_{ts}.zip"},
    )


@router.post("/api/backup/import")
async def import_backup(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="仅预览，不写库"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """上传 ZIP，事务级覆盖数据库（dry_run=True 时只返回预览不写库）。"""
    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception:
        raise HTTPException(400, "无法解析 ZIP 文件")

    names = zf.namelist()
    for required in ("subscriptions.json", "imports.json", "settings.json"):
        if required not in names:
            raise HTTPException(400, f"ZIP 中缺少 {required}")

    subs_data: list[dict] = json.loads(zf.read("subscriptions.json"))
    imports_data: list[dict] = json.loads(zf.read("imports.json"))
    settings_data: dict = json.loads(zf.read("settings.json"))
    templates_data: list[dict] = json.loads(zf.read("templates.json")) if "templates.json" in names else []

    preview = {
        "subscriptions": len(subs_data),
        "import_batches": len(imports_data),
        "settings": len(settings_data),
        "templates": len(templates_data),
        "sensitive_keys_skipped": list(_SENSITIVE_KEYS),
    }

    if dry_run:
        return {"ok": True, "dry_run": True, "preview": preview}

    # 事务写入
    await db.execute(__import__("sqlalchemy").text("DELETE FROM subscriptions"))
    await db.execute(__import__("sqlalchemy").text("DELETE FROM import_batches"))
    await db.execute(__import__("sqlalchemy").text("DELETE FROM imported_nodes"))
    await db.execute(__import__("sqlalchemy").text("DELETE FROM custom_templates"))

    for s in subs_data:
        db.add(Subscription(
            name=s.get("name", ""),
            url=s.get("url", ""),
            prefix=s.get("prefix", ""),
            enabled=bool(s.get("enabled", True)),
            auto_disable=bool(s.get("auto_disable", True)),
            tags=s.get("tags", ""),
        ))

    for b in imports_data:
        batch = ImportBatch(name=b.get("name", "导入"))
        db.add(batch)
        await db.flush()
        for n in b.get("nodes", []):
            db.add(ImportedNode(
                batch_id=batch.id,
                sort_order=int(n.get("sort_order", 0)),
                enabled=bool(n.get("enabled", True)),
                proxy_yaml=n.get("proxy_yaml", ""),
            ))

    for key, val in settings_data.items():
        if key in _SENSITIVE_KEYS:
            continue
        s = await db.get(Setting, key)
        if s:
            s.value = str(val)
        else:
            db.add(Setting(key=key, value=str(val)))

    for t in templates_data:
        db.add(CustomTemplate(name=t.get("name", ""), yaml_body=t.get("yaml_body", "")))

    await db.commit()

    # 失效缓存
    from services.config_cache import config_cache
    config_cache.invalidate_all()

    return {"ok": True, "dry_run": False, "imported": preview}
