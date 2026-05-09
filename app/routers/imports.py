"""导入批次与节点路由。"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import io

from aggregator import extract_proxies_for_batch_import, parse_proxies, probe_imported_proxy_yaml
from auth import require_admin
from database import get_db
from deps import get_setting
from models import ImportBatch, ImportedNode
from proxy_latency import probe_single_proxy, format_probe_success_message
from proxy_uri import looks_like_proxy_uri_line, parse_single_proxy_uri, proxy_dict_to_uri
from services.aggregator_service import (
    node_display_fields,
    proxy_yaml_one_node,
    subscription_batch_prefix,
)

router = APIRouter()

_log_imports = logging.getLogger(__name__)


async def _touch_batch_updated(batch_id: int, db: AsyncSession) -> None:
    """更新导入批次的 updated_at（节点变更后调用）。"""
    b = await db.get(ImportBatch, batch_id)
    if b:
        b.updated_at = datetime.now(timezone.utc)


async def _set_all_imported_nodes_enabled(db: AsyncSession, batch_id: int, enabled: bool) -> int:
    r = await db.execute(select(ImportedNode).where(ImportedNode.batch_id == batch_id))
    n = 0
    for node in r.scalars().all():
        node.enabled = enabled
        n += 1
    return n


@router.get("/api/import-batches")
async def list_import_batches(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    r = await db.execute(select(ImportBatch).order_by(ImportBatch.id.desc()))
    batches = list(r.scalars().all())
    out: list[dict] = []
    for b in batches:
        nr = await db.execute(
            select(ImportedNode)
            .where(ImportedNode.batch_id == b.id)
            .order_by(ImportedNode.sort_order)
        )
        nodes: list[dict] = []
        for n in nr.scalars().all():
            d = n.to_dict()
            d.update(node_display_fields(n.proxy_yaml))
            nodes.append(d)
        bd = b.to_dict()
        bd["nodes"] = nodes
        out.append(bd)
    return out


@router.post("/api/import-batches")
async def create_import_batch(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    b = ImportBatch(name=name[:100])
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b.to_dict()


@router.put("/api/import-batches/{batch_id}")
async def update_import_batch(batch_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    if "name" in body:
        n = (body.get("name") or "").strip()
        if not n:
            raise HTTPException(400, "名称不能为空")
        b.name = n[:100]
    if "set_all_nodes_enabled" in body:
        await _set_all_imported_nodes_enabled(db, batch_id, bool(body.get("set_all_nodes_enabled")))
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(b)
    return b.to_dict()


@router.delete("/api/import-batches/{batch_id}")
async def delete_import_batch(batch_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.post("/api/import-batches/{batch_id}/set-all-nodes-enabled")
async def set_all_import_batch_nodes_enabled(
    batch_id: int,
    req: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    enabled = bool(body.get("enabled", True))
    cnt = await _set_all_imported_nodes_enabled(db, batch_id, enabled)
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "updated": cnt, "enabled": enabled}


@router.post("/api/import-batches/import")
async def import_batches_bulk_import(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """多行分享链接或 Clash proxies YAML：创建一批次及多条节点。"""
    body = await req.json()
    name_base = (body.get("name") or body.get("name_prefix") or "").strip()
    text = body.get("text") or ""
    if not name_base:
        raise HTTPException(400, "名称不能为空")
    display_name = name_base[:100]

    nodes: list[dict] = []
    skipped_lines: list[dict] = []
    mode = ""

    yaml_proxies = extract_proxies_for_batch_import(text)
    if yaml_proxies:
        nodes = yaml_proxies
        mode = "proxies_yaml"
    else:
        raw_lines: list[tuple[int, str]] = []
        for line_num, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            raw_lines.append((line_num, line))
        for line_num, line in raw_lines:
            if not looks_like_proxy_uri_line(line):
                skipped_lines.append({"line": line_num, "reason": "无法解析为分享链接"})
                continue
            p = parse_single_proxy_uri(line)
            if not p:
                skipped_lines.append({"line": line_num, "reason": "无法解析为分享链接"})
                continue
            nodes.append(p)
        mode = "uri_lines"

    if not nodes:
        raise HTTPException(400, "没有可导入的有效分享链接或 proxies 配置")

    batch = ImportBatch(name=display_name)
    db.add(batch)
    await db.flush()

    created_ids: list[int] = []
    for idx, proxy in enumerate(nodes, start=1):
        node = ImportedNode(
            batch_id=batch.id,
            sort_order=idx,
            enabled=True,
            proxy_yaml=proxy_yaml_one_node(proxy),
        )
        db.add(node)
        await db.flush()
        created_ids.append(node.id)

    batch.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(batch)
    return {
        "ok": True,
        "batch_id": batch.id,
        "created": len(created_ids),
        "skipped": len(skipped_lines),
        "details": {"created_node_ids": created_ids, "skipped_lines": skipped_lines, "mode": mode},
    }


@router.post("/api/import-batches/{batch_id}/nodes")
async def add_imported_node(batch_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    raw = (body.get("proxy_yaml") or body.get("text") or "").strip()
    if not raw:
        raise HTTPException(400, "内容不能为空")

    ps = parse_proxies(raw)
    if not ps:
        line = raw.splitlines()[0].strip() if raw.splitlines() else ""
        if line and looks_like_proxy_uri_line(line):
            p = parse_single_proxy_uri(line)
            if p:
                ps = [p]
    if not ps:
        raise HTTPException(400, "无法解析为有效节点")

    max_row = await db.execute(
        select(func.coalesce(func.max(ImportedNode.sort_order), 0)).where(ImportedNode.batch_id == batch_id)
    )
    max_order = int(max_row.scalar_one() or 0)

    node = ImportedNode(
        batch_id=batch_id,
        sort_order=max_order + 1,
        enabled=True,
        proxy_yaml=proxy_yaml_one_node(ps[0]),
    )
    db.add(node)
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)
    d = node.to_dict()
    d.update(node_display_fields(node.proxy_yaml))
    return d


@router.put("/api/imported-nodes/{node_id}")
async def update_imported_node(node_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    body = await req.json()
    if "proxy_yaml" in body:
        raw = (body.get("proxy_yaml") or "").strip()
        if not raw:
            raise HTTPException(400, "proxy_yaml 不能为空")
        ps = parse_proxies(raw)
        if not ps:
            raise HTTPException(400, "无法解析节点")
        n.proxy_yaml = proxy_yaml_one_node(ps[0])
    if "enabled" in body:
        n.enabled = bool(body["enabled"])
    if "sort_order" in body:
        n.sort_order = int(body["sort_order"])
    n.updated_at = datetime.now(timezone.utc)
    batch = await db.get(ImportBatch, n.batch_id)
    if batch:
        batch.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(n)
    d = n.to_dict()
    d.update(node_display_fields(n.proxy_yaml))
    return d


@router.delete("/api/imported-nodes/{node_id}")
async def delete_imported_node(node_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    bid = n.batch_id
    await db.delete(n)
    await _touch_batch_updated(bid, db)
    await db.commit()
    return {"ok": True}


@router.get("/api/nodes/{node_id}/qr.png")
async def node_qrcode(node_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """生成节点 QR 码 PNG，编码节点的 V2Ray 分享链接（vmess://、trojan:// 等）。"""
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")

    proxies = parse_proxies(n.proxy_yaml)
    if not proxies:
        raise HTTPException(422, "无法解析节点配置")

    uri = proxy_dict_to_uri(proxies[0])
    if not uri:
        ptype = proxies[0].get("type", "未知")
        raise HTTPException(422, f"协议 {ptype} 不支持生成分享链接")

    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=4)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(content=buf.read(), media_type="image/png")
    except ImportError as e:
        _log_imports.warning("QR 依赖缺失: %s", e)
        raise HTTPException(
            503,
            "QR 二维码依赖未就绪：请在运行环境中安装 qrcode[pil]（含 Pillow）。",
        ) from e
    except Exception as e:
        _log_imports.exception("QR 生成失败")
        raise HTTPException(
            503,
            "QR 图片生成异常，请检查是否已安装 Pillow，或稍后重试。",
        ) from e


@router.post("/api/imported-nodes/{node_id}/check")
async def check_imported_node(node_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """对导入节点 YAML 首条 proxy 做延迟探测，并更新 last_check_at / last_latency_ms。"""
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    batch = await db.get(ImportBatch, n.batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await get_setting(db, "mihomo_path", "")
    prefix = subscription_batch_prefix(batch.name, n.sort_order)
    r = await probe_imported_proxy_yaml(n.proxy_yaml, prefix, timeout, mihomo_path=mihomo_path)

    n.last_check_at = datetime.now(timezone.utc)
    if r.get("ok"):
        n.last_latency_ms = int(r.get("latency_ms")) if r.get("latency_ms") is not None else -1
    else:
        n.last_latency_ms = -1
    await db.commit()

    # 写入探测历史
    try:
        from services.probe_history_service import record_probe
        await record_probe(
            target_kind="node",
            target_id=node_id,
            ok=bool(r.get("ok")),
            latency_ms=r.get("latency_ms"),
        )
    except Exception:
        pass

    return {
        "available": r["ok"],
        "node_count": r["node_count"],
        "message": r["message"],
        "error": r.get("error"),
        "latency_ms": r.get("latency_ms"),
        "tcp_tested": r.get("tcp_tested", False),
        "probe_kind": r.get("probe_kind", "none"),
        "display_name": node_display_fields(n.proxy_yaml)["display_name"],
        "enabled": n.enabled,
    }
