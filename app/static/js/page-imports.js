/**
 * 节点管理：导入批次树、批量导入弹窗、批次与节点操作 API
 * 依赖：core.js；switchPage('imports') → loadImportBatches()
 */
function showBatchImportModal() {
    const html = `<div class="modal-bg" id="batchImportModal" onclick="if(event.target===this)this.remove()">
    <div class="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-bold mb-3">批量导入节点</h3>
      <p class="text-sm text-slate-400 mb-3">将创建<strong>一个批次</strong>。支持：<strong>①</strong> 每行一条分享链接；<strong>②</strong> 整段 Clash <code class="text-xs">proxies</code> YAML。聚合时节点前缀为「批次名_序号」。</p>
      <div class="space-y-3">
<div><label class="text-sm text-slate-400">批次名称</label>
  <input id="batch_sub_name" placeholder="例如：我的节点" value="导入"></div>
<div><label class="text-sm text-slate-400">分享链接或 proxies YAML</label>
  <textarea id="batch_uri_text" rows="14" class="font-mono text-sm w-full" placeholder="vmess://... 或 proxies:&#10;  - {name: ..., type: ss, ...}"></textarea></div>
<div class="flex gap-2 justify-end">
  <button type="button" class="btn btn-secondary" onclick="document.getElementById('batchImportModal').remove()">取消</button>
  <button type="button" class="btn btn-primary" onclick="submitBatchImport()">导入</button>
</div>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

async function submitBatchImport() {
    const name = (document.getElementById('batch_sub_name')?.value || '').trim();
    const text = document.getElementById('batch_uri_text')?.value || '';
    if (!name) { toast('请填写批次名称', 'error'); return; }
    if (!text.trim()) { toast('请粘贴分享链接或 proxies 配置', 'error'); return; }
    try {
        toast('正在导入…');
        const r = await api('/api/import-batches/import', {
            method: 'POST',
            body: JSON.stringify({ name, text }),
        });
        document.getElementById('batchImportModal')?.remove();
        let msg = '已导入批次 #' + (r.batch_id || '') + '，共 ' + (r.created || 0) + ' 个节点';
        if (r.skipped) msg += '，跳过 ' + r.skipped + ' 行无效内容';
        toast(msg);
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadImportBatches() {
    try {
        const batches = await api('/api/import-batches');
        _importBatchesCache = batches;
        const el = document.getElementById('importsTree');
        if (!batches.length) {
            el.innerHTML = '<div class="card text-center text-slate-400 py-8">暂无导入批次，点击「批量导入」</div>';
            return;
        }
        el.innerHTML = batches.map(b => {
            const nodes = (b.nodes || []).map(n => `
<div class="flex flex-wrap items-center gap-2 py-2 border-b border-slate-700/50 last:border-0 pl-2 md:pl-4">
  <label class="sub-switch mr-1 shrink-0" title="启用">
    <input type="checkbox" role="switch" aria-label="启用节点" ${n.enabled ? 'checked' : ''} onchange="toggleImportNodeEnabled(${n.id}, this.checked)">
    <span class="sub-switch-slider"></span>
  </label>
  <span class="font-medium truncate max-w-[12rem] md:max-w-none">${esc(n.display_name)}</span>
  <span class="tag bg-slate-600/50 text-xs shrink-0">${esc(n.proxy_type)}</span>
  <span class="text-xs text-slate-500 hidden sm:inline">${formatIsoTime(n.updated_at)}</span>
  <div class="flex flex-wrap gap-1 ml-auto">
    <button type="button" class="btn btn-accent-orange btn-sm" onclick="checkImportNode(${n.id})">测速</button>
    <button type="button" class="btn btn-secondary btn-sm" onclick="showEditImportNodeModal(${n.id})">编辑</button>
    <button type="button" class="btn btn-danger btn-sm" onclick="deleteImportNode(${n.id})">删除</button>
  </div>
</div>`).join('');
            return `<details class="card mb-2" open>
<summary class="cursor-pointer font-semibold flex flex-wrap items-center gap-2 py-2 px-2 list-none">
  <span class="select-none">${esc(b.name)}</span>
  <span class="text-xs text-slate-400 font-normal">添加 ${formatIsoTime(b.created_at)}</span>
  <span class="text-xs text-slate-500 font-normal">更新 ${formatIsoTime(b.updated_at)}</span>
  <span class="flex flex-wrap gap-1 ml-auto" onclick="event.preventDefault(); event.stopPropagation();">
    <button type="button" class="btn btn-refresh btn-sm" onclick="event.stopPropagation(); setImportBatchAllEnabled(${b.id}, true)" title="本批次下全部节点设为启用">批量启用</button>
    <button type="button" class="btn btn-danger btn-sm" onclick="event.stopPropagation(); setImportBatchAllEnabled(${b.id}, false)" title="本批次下全部节点设为禁用">批量禁用</button>
    <button type="button" class="btn btn-secondary btn-sm" onclick="renameImportBatch(${b.id})">改名</button>
    <button type="button" class="btn btn-danger btn-sm" onclick="deleteImportBatch(${b.id})">删除批次</button>
  </span>
</summary>
<div class="border-t border-slate-700 px-2 pb-2">
  ${nodes || '<div class="text-slate-500 text-sm py-2">无节点</div>'}
</div>
      </details>`;
        }).join('');
    } catch (e) { toast(e.message, 'error'); }
}

async function renameImportBatch(id) {
    const b = _importBatchesCache.find(x => x.id === id);
    const cur = b ? b.name : '';
    const name = prompt('批次名称', cur);
    if (name == null) return;
    const t = name.trim();
    if (!t) { toast('名称不能为空', 'error'); return; }
    try {
        await api('/api/import-batches/' + id, { method: 'PUT', body: JSON.stringify({ name: t }) });
        toast('已更新');
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteImportBatch(id) {
    if (!confirm('将删除该批次及其下全部节点，不可恢复。确定？')) return;
    try {
        await api('/api/import-batches/' + id, { method: 'DELETE' });
        toast('已删除批次');
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function toggleImportNodeEnabled(id, enabled) {
    try {
        await api('/api/imported-nodes/' + id, { method: 'PUT', body: JSON.stringify({ enabled }) });
        toast(enabled ? '已启用' : '已禁用');
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) {
        toast(e.message, 'error');
        await loadImportBatches();
    }
}

function showEditImportNodeModal(id) {
    const n = _importBatchesCache.flatMap(b => b.nodes || []).find(x => x.id === id);
    if (!n) { toast('未找到节点', 'error'); loadImportBatches(); return; }
    const html = `<div class="modal-bg" id="nodeEditModal" onclick="if(event.target===this)this.remove()">
    <div class="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-bold mb-3">编辑节点</h3>
      <p class="text-sm text-slate-400 mb-2">可粘贴单节点 YAML、<code class="text-xs">proxies:</code> 片段或一行分享链接。</p>
      <textarea id="node_yaml_edit" rows="18" class="font-mono text-sm w-full">${escHtml(n.proxy_yaml)}</textarea>
      <div class="flex gap-2 justify-end mt-3">
<button type="button" class="btn btn-secondary" onclick="document.getElementById('nodeEditModal').remove()">取消</button>
<button type="button" class="btn btn-primary" onclick="saveImportNodeYaml(${id})">保存</button>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

async function saveImportNodeYaml(id) {
    const proxy_yaml = document.getElementById('node_yaml_edit')?.value || '';
    if (!proxy_yaml.trim()) { toast('内容不能为空', 'error'); return; }
    try {
        await api('/api/imported-nodes/' + id, { method: 'PUT', body: JSON.stringify({ proxy_yaml }) });
        document.getElementById('nodeEditModal')?.remove();
        toast('已保存');
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteImportNode(id) {
    if (!confirm('确定删除该节点？')) return;
    try {
        await api('/api/imported-nodes/' + id, { method: 'DELETE' });
        toast('已删除');
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

/** 与订阅「检测」一致：probe_kind 说明测速方式；修复曾误用 '\\\\n' 导致延迟行显示为字面量 */
async function checkImportNode(id) {
    try {
        toast('正在测速…');
        const r = await api('/api/imported-nodes/' + id + '/check', { method: 'POST', body: '{}' });
        const head = r.available ? '可用' : '不可用';
        let tcpLine = '';
        const pk = r.probe_kind || '';
        if (r.latency_ms != null)
            tcpLine = '\n延迟（' + (pk === 'httpx' ? '经代理 URL' : pk === 'mihomo' ? 'Mihomo URL' : pk === 'tcp-fallback' ? 'TCP 兜底' : '探测') + '）: ' + Math.round(r.latency_ms) + ' ms';
        else if (r.tcp_tested && !r.available)
            tcpLine = '\n已尝试探测（失败，见上文说明）';
        const detail = (r.message || '') + tcpLine;
        alert('「' + (r.display_name || '') + '」\n\n' + head + '\n' + detail);
    } catch (e) { toast(e.message, 'error'); }
}

/** 将某导入批次下全部节点设为启用或禁用；走 PUT /api/import-batches/{id}（与改名同一路由，避免部分环境下长路径 POST 404） */
async function setImportBatchAllEnabled(batchId, enabled) {
    const act = enabled ? '启用' : '禁用';
    try {
        await api('/api/import-batches/' + batchId, {
            method: 'PUT',
            body: JSON.stringify({ set_all_nodes_enabled: enabled }),
        });
        toast('已批量' + act);
        await loadImportBatches();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

