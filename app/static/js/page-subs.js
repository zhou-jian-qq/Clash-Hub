/**
 * 订阅管理：列表、启用开关、批量操作、添加/编辑订阅弹窗
 * 依赖：core.js（api、toast、_subsCache）；由 app.js 的 switchPage('subs') 触发 loadSubs
 */
async function loadSubs() {
    try {
        const subs = await api('/api/subscriptions');
        _subsCache = subs;
        const el = document.getElementById('subsList');
        if (!subs.length) { el.innerHTML = '<div class="card text-center text-slate-400 py-8">暂无订阅，点击添加订阅</div>'; return; }
        el.innerHTML = subs.map(s => {
            const pct = s.total > 0 ? Math.min(100, (s.used / s.total * 100)).toFixed(1) : 0;
            const color = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '#22c55e';
            return `<div class="card mb-3" data-sub-id="${s.id}">
<div class="flex items-center justify-between mb-2 flex-wrap gap-2">
  <div class="flex items-center gap-2">
    <input type="checkbox" class="sub-cb rounded border-slate-500 shrink-0" data-id="${s.id}" title="勾选后可批量启用/禁用/检测" onclick="event.stopPropagation()" onchange="syncSubSelectAll()">
    <label class="sub-switch mr-1" title="${s.enabled ? '点击禁用' : '点击启用'}">
      <input type="checkbox" role="switch" aria-label="启用订阅" ${s.enabled ? 'checked' : ''} onchange="toggleSubEnabled(${s.id}, this.checked)">
      <span class="sub-switch-slider"></span>
    </label>
    <span class="tag ${s.enabled ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}">${s.enabled ? '启用' : '禁用'}</span>
    <span class="font-semibold">${esc(s.name)}</span>
    ${s.prefix ? `<span class="tag bg-blue-600/20 text-blue-400">${esc(s.prefix)}</span>` : ''}
  </div>
  <div class="flex flex-wrap gap-1">
    <button type="button" class="btn btn-ghost btn-sm" onclick="refreshSub(${s.id})">刷新</button>
    <button type="button" class="btn btn-outline-accent btn-sm" onclick="checkSub(${s.id})" title="检测：拉取+解析；单节点时额外 TCP 建连延迟">检测</button>
    <button type="button" class="btn btn-secondary btn-sm" onclick="showEditSub(${s.id})">编辑</button>
    <button type="button" class="btn btn-danger btn-sm" onclick="deleteSub(${s.id})">删除</button>
  </div>
</div>
<div class="flex flex-wrap items-center gap-4 text-sm text-slate-400 mb-2">
  <span>节点: ${s.node_count}</span>
  <span>已用: ${formatBytes(s.used)} / ${formatBytes(s.total)}</span>
  <span>到期: ${formatDate(s.expire)}</span>
  <span>同步: ${formatIsoTime(s.last_sync)}</span>
  <span>添加: ${formatIsoTime(s.created_at)}</span>
  <span>更新: ${formatIsoTime(s.updated_at)}</span>
</div>
<div class="progress-bar"><div class="progress-fill" style="width:${pct}%;background:${color}"></div></div>
      </div>`;
        }).join('');
        const sa = document.getElementById('subSelectAll');
        if (sa) { sa.checked = false; sa.indeterminate = false; }
    } catch (e) { toast(e.message, 'error'); }
}

function getSelectedSubIds() {
    return Array.from(document.querySelectorAll('#subsList .sub-cb:checked')).map(cb => parseInt(cb.getAttribute('data-id'), 10)).filter(n => !isNaN(n));
}

function toggleSelectAllSubs(checked) {
    document.querySelectorAll('#subsList .sub-cb').forEach(cb => { cb.checked = checked; });
    const sa = document.getElementById('subSelectAll');
    if (sa) sa.indeterminate = false;
}

function syncSubSelectAll() {
    const boxes = Array.from(document.querySelectorAll('#subsList .sub-cb'));
    if (!boxes.length) return;
    const n = boxes.length;
    const c = boxes.filter(b => b.checked).length;
    const sa = document.getElementById('subSelectAll');
    if (!sa) return;
    sa.checked = c === n;
    sa.indeterminate = c > 0 && c < n;
}

async function batchEnableSubs(enabled) {
    const ids = getSelectedSubIds();
    if (!ids.length) { toast('请先勾选要操作的订阅', 'error'); return; }
    try {
        await api('/api/subscriptions/batch-enabled', { method: 'POST', body: JSON.stringify({ ids, enabled }) });
        toast(enabled ? '已批量启用' : '已批量禁用');
        await loadSubs();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function batchDeleteSubs() {
    const ids = getSelectedSubIds();
    if (!ids.length) { toast('请先勾选要删除的订阅', 'error'); return; }
    if (!confirm('将永久删除选中的 ' + ids.length + ' 条订阅，不可恢复。确定？')) return;
    try {
        const r = await api('/api/subscriptions/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) });
        toast('已删除 ' + (r.deleted || ids.length) + ' 条');
        await loadSubs();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

function showAddSub() { showSubModal(); }

function showEditSub(id) {
    const sub = _subsCache.find(x => x.id === id);
    if (!sub) { toast('未找到该订阅，请刷新列表后重试', 'error'); loadSubs(); return; }
    showSubModal(sub);
}

function showSubModal(sub = null) {
    const isEdit = !!sub;
    const html = `<div class="modal-bg" id="subModal" onclick="if(event.target===this)this.remove()">
    <div class="card w-full max-w-md max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-bold mb-4">${isEdit ? '编辑' : '添加'}订阅</h3>
      <div class="space-y-3">
<div><label class="text-sm text-slate-400">名称</label><input id="m_name" value="${esc(sub?.name || '')}"></div>
<div><label class="text-sm text-slate-400">机场订阅链接</label><input id="m_url" value="${esc(sub?.url || '')}" placeholder="仅支持 https:// 或 http:// 订阅地址"></div>
<div><label class="text-sm text-slate-400">前缀</label><input id="m_prefix" value="${esc(sub?.prefix || '')}" placeholder="可选, 用于区分来源"></div>
<div class="flex items-center gap-4">
  <label class="flex items-center gap-2"><input type="checkbox" id="m_enabled" ${!sub || sub.enabled ? 'checked' : ''}> 启用</label>
  <label class="flex items-center gap-2"><input type="checkbox" id="m_autodis" ${!sub || sub.auto_disable ? 'checked' : ''}> 自动禁用</label>
</div>
<div class="flex gap-2 justify-end">
  <button class="btn btn-secondary" onclick="document.getElementById('subModal').remove()">取消</button>
  <button class="btn btn-primary" onclick="saveSub(${sub ? sub.id : 'null'})">${isEdit ? '保存' : '添加'}</button>
</div>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

async function saveSub(id) {
    const data = {
        name: document.getElementById('m_name').value,
        url: document.getElementById('m_url').value,
        prefix: document.getElementById('m_prefix').value,
        enabled: document.getElementById('m_enabled').checked,
        auto_disable: document.getElementById('m_autodis').checked,
    };
    if (!data.name || !data.url) { toast('名称和URL不能为空', 'error'); return; }
    try {
        if (id) {
            await api('/api/subscriptions/' + id, { method: 'PUT', body: JSON.stringify(data) });
        } else {
            await api('/api/subscriptions', { method: 'POST', body: JSON.stringify(data) });
        }
        document.getElementById('subModal')?.remove();
        toast(id ? '已更新' : '已添加');
        await loadSubs();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteSub(id) {
    if (!confirm('确定删除？')) return;
    try { await api('/api/subscriptions/' + id, { method: 'DELETE' }); toast('已删除'); await loadSubs(); schedulePreviewRefresh(); }
    catch (e) { toast(e.message, 'error'); }
}

async function refreshSub(id) {
    try { await api('/api/subscriptions/' + id + '/refresh', { method: 'POST' }); toast('刷新成功'); await loadSubs(); schedulePreviewRefresh(); }
    catch (e) { toast(e.message, 'error'); }
}

async function refreshAllSubs() {
    try { toast('正在刷新所有订阅...'); await api('/api/subscriptions/refresh-all', { method: 'POST' }); toast('全部刷新完成'); await loadSubs(); schedulePreviewRefresh(); }
    catch (e) { toast(e.message, 'error'); }
}

/** 单条：只检测并提示，不自动改启用状态 */
async function checkSub(id) {
    try {
        toast('正在检测…');
        const r = await api('/api/subscriptions/' + id + '/check', { method: 'POST', body: '{}' });
        const state = r.enabled ? '当前：启用' : '当前：禁用';
        const head = r.available ? '检测通过（可用）' : '检测未通过（不可用）';
        let tcpLine = '';
        const pk = r.probe_kind || '';
        if (r.latency_ms != null)
            tcpLine = '\n延迟（' + (pk === 'httpx' ? '经代理 URL' : pk === 'mihomo' ? 'Mihomo URL' : pk === 'tcp-fallback' ? 'TCP 兜底' : '探测') + '）: ' + Math.round(r.latency_ms) + ' ms';
        else if (r.tcp_tested && !r.available)
            tcpLine = '\n已尝试探测（失败，见上文说明）';
        const detail = (r.message || '') + tcpLine + '\n\n' + state + '\n请用左侧开关自行调整启用/禁用。';
        alert('「' + (r.name || '') + '」\n\n' + head + '\n' + detail);
    } catch (e) { toast(e.message, 'error'); }
}

/** 批量：仅检测已勾选的订阅；不可用且原为启用的订阅会自动禁用 */
async function batchCheckSubs() {
    const ids = getSelectedSubIds();
    if (!ids.length) { toast('请先勾选要检测的订阅', 'error'); return; }
    if (!confirm('将对选中的 ' + ids.length + ' 条订阅逐一检测。\n不可用且当前为「启用」的订阅将被自动禁用。\n确定继续？')) return;
    try {
        toast('正在批量检测，请稍候…');
        const r = await api('/api/subscriptions/batch-check', { method: 'POST', body: JSON.stringify({ ids }) });
        const n = r.auto_disabled || 0;
        toast('检测完成：共 ' + (r.checked || 0) + ' 条，已自动禁用 ' + n + ' 条');
        if (n > 0 && r.disabled_names && r.disabled_names.length)
            alert('已自动禁用的订阅：\n' + r.disabled_names.join('\n'));
        await loadSubs();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function toggleSubEnabled(id, enabled) {
    try {
        await api('/api/subscriptions/' + id, { method: 'PUT', body: JSON.stringify({ enabled }) });
        toast(enabled ? '已启用' : '已禁用');
        await loadSubs();
        schedulePreviewRefresh();
    } catch (e) {
        toast(e.message, 'error');
        await loadSubs();
    }
}

const TPL_YAML_STUB = `mixed-port: 7890
mode: rule
proxies: []
proxy-groups:
  - name: "🚀 节点选择"
    type: select
    proxies: ["⚡ 自动选择", DIRECT, REJECT]
  - name: "⚡ 自动选择"
    type: url-test
    proxies: []
    url: "https://www.gstatic.com/generate_204"
    interval: 3600
rule-providers: {}
rules:
  - MATCH,🚀 节点选择
`;

