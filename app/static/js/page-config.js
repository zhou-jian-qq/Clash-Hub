/**
 * 配置页：模板与预设、过滤与系统设置、YAML/可视化预览与复制
 * 依赖：core.js；switchPage('config') → loadConfigPage() / loadSettings()
 */

function tplDescPreset(n) {
    const m = { '精简版': '基础分流, 7 个代理组', '标准版': '常用服务分流, 14 个代理组', '完整版': '全面精细分流, 33 个代理组' };
    return m[n] || '';
}

function tplStatPreset(n) {
    const g = { '精简版': '7', '标准版': '14', '完整版': '33' };
    return g[n] || '-';
}

function presetRowKeydown(ev, name) {
    if (!ev || (ev.key !== 'Enter' && ev.key !== ' ')) return;
    ev.preventDefault();
    selectTplPreset(name);
}

async function loadConfigPage() {
    await loadSettings();
    await renderConfigTemplates();
    await refreshPreview();
}

async function renderConfigTemplates() {
    try {
        const t = await api('/api/templates');
        document.getElementById('configPresetList').innerHTML = (t.presets || []).map(n => {
            const isActive = t.active === n;
            return `<div class="preset-row-config ${isActive ? 'is-active' : ''}">
<div class="cursor-pointer rounded-lg p-1 -m-1" role="button" tabindex="0"
  onclick="selectTplPreset('${n}')" onkeydown='presetRowKeydown(event,${JSON.stringify(n)})'>
  <div class="flex justify-between gap-2">
    <div>
      <span class="font-semibold">${esc(n)}</span>
      ${isActive ? '<span class="tag bg-blue-600/20 text-blue-400 ml-2">当前</span>' : ''}
      <p class="text-sm text-slate-400 mt-1">${tplDescPreset(n)}</p>
    </div>
    <div class="text-xs text-slate-500 text-right shrink-0">${tplStatPreset(n)} 组</div>
  </div>
</div>
<div class="mt-3 flex justify-end">
  <button type="button" class="btn btn-secondary text-xs py-1.5 px-3" onclick='event.stopPropagation();previewPresetSkeleton(${JSON.stringify(n)})'>预览</button>
</div>
      </div>`;
        }).join('');
        const list = document.getElementById('configCustomTplList');
        const items = t.custom_templates || [];
        if (!items.length) {
            list.innerHTML = '<div class="text-slate-500 text-sm py-2">暂无自定义模板。</div>';
        } else {
            list.innerHTML = items.map(c => {
                const isActive = t.active === 'custom:' + c.id;
                return `<div class="card flex flex-wrap items-center justify-between gap-2 py-2 ${isActive ? 'ring-1 ring-blue-500' : ''}">
  <div class="flex items-center gap-2 min-w-0">
    <span class="font-medium truncate">${esc(c.name)}</span>
    <span class="text-xs text-slate-500 shrink-0">#${c.id}</span>
    ${isActive ? '<span class="tag bg-blue-600/20 text-blue-400 shrink-0">当前</span>' : ''}
  </div>
  <div class="flex flex-wrap gap-1 shrink-0">
    <button type="button" class="btn btn-secondary btn-sm" onclick="selectTplCustom(${c.id})">选用</button>
    <button type="button" class="btn btn-secondary btn-sm" onclick="showTplItemModal(${c.id})">编辑</button>
    <button type="button" class="btn btn-danger btn-sm" onclick="deleteTplItem(${c.id})">删除</button>
  </div>
</div>`;
            }).join('');
        }
    } catch (e) { toast(e.message, 'error'); }
}

/** 预设模板预览 YAML（无真实订阅、占位节点），可复制后到「创建模板」中修改 */
async function previewPresetSkeleton(name) {
    try {
        const r = await fetch(API + '/api/templates/preset-preview/' + encodeURIComponent(name), {
            headers: headers(),
        });
        if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            let msg = j.detail || r.statusText || '加载失败';
            if (Array.isArray(msg)) msg = msg.map(x => (x && x.msg) || String(x)).join('; ');
            throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        const d = await r.json();
        const yaml = d.yaml || '';
        const wrap = document.createElement('div');
        wrap.className = 'modal-bg';
        wrap.id = 'presetSkelModal';
        wrap.addEventListener('click', (e) => { if (e.target === wrap) wrap.remove(); });
        const card = document.createElement('div');
        card.className = 'card w-full max-w-4xl max-h-[92vh] flex flex-col';
        card.addEventListener('click', (e) => e.stopPropagation());
        const title = document.createElement('div');
        title.className = 'flex flex-wrap items-center justify-between gap-2 mb-2';
        title.innerHTML = `<h3 class="text-lg font-bold">预设预览：${esc(name)}</h3>`;
        const close1 = document.createElement('button');
        close1.type = 'button';
        close1.className = 'btn btn-secondary btn-sm';
        close1.textContent = '关闭';
        close1.onclick = () => wrap.remove();
        title.appendChild(close1);
        const hint = document.createElement('p');
        hint.className = 'text-sm text-slate-400 mb-2';
        hint.textContent = '以下为无真实订阅时的示例 YAML（占位节点）。可复制全文，在「创建模板」中粘贴后按需修改。';
        const ta = document.createElement('textarea');
        ta.id = 'presetSkelTa';
        ta.readOnly = true;
        ta.className = 'font-mono text-xs w-full flex-1 min-h-[50vh] rounded-lg';
        ta.value = yaml;
        const bar = document.createElement('div');
        bar.className = 'flex flex-wrap gap-2 justify-end mt-3';
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'btn btn-primary btn-sm';
        copyBtn.textContent = '复制全部';
        copyBtn.onclick = () => {
            copyText(ta.value, '已复制');
        };
        const close2 = document.createElement('button');
        close2.type = 'button';
        close2.className = 'btn btn-secondary btn-sm';
        close2.textContent = '关闭';
        close2.onclick = () => wrap.remove();
        bar.appendChild(copyBtn);
        bar.appendChild(close2);
        card.appendChild(title);
        card.appendChild(hint);
        card.appendChild(ta);
        card.appendChild(bar);
        wrap.appendChild(card);
        document.body.appendChild(wrap);
    } catch (e) { toast(e.message, 'error'); }
}

async function selectTplPreset(name) {
    try {
        await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ name }) });
        toast('已切换到: ' + name);
        await renderConfigTemplates();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function selectTplCustom(id) {
    try {
        await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ custom_id: id }) });
        toast('已选用自定义模板');
        await renderConfigTemplates();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function showTplItemModal(id) {
    let name = '';
    let yaml = TPL_YAML_STUB;
    const isNew = id == null;
    if (!isNew) {
        try {
            const d = await api('/api/templates/custom-items/' + id);
            name = d.name || '';
            yaml = d.yaml || TPL_YAML_STUB;
        } catch (e) { toast(e.message, 'error'); return; }
    }
    const html = `<div class="modal-bg" id="tplItemModal" onclick="if(event.target===this)this.remove()">
    <div class="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-bold mb-3">${isNew ? '创建' : '编辑'}自定义模板</h3>
      <div class="space-y-3">
<div><label class="text-sm text-slate-400">模板名称</label><input id="m_tpl_name" placeholder="例如：办公用 / 游戏用"></div>
<div><label class="text-sm text-slate-400">YAML（须含 proxy-groups）</label><textarea id="m_tpl_yaml" rows="18" class="font-mono text-sm"></textarea></div>
<div class="flex gap-2 justify-end">
  <button type="button" class="btn btn-secondary" onclick="document.getElementById('tplItemModal').remove()">取消</button>
  <button type="button" class="btn btn-primary" onclick="saveTplItem(${isNew ? 'null' : id})">保存</button>
</div>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    document.getElementById('m_tpl_name').value = name;
    document.getElementById('m_tpl_yaml').value = yaml;
}

async function saveTplItem(id) {
    const name = document.getElementById('m_tpl_name').value.trim();
    const yaml = document.getElementById('m_tpl_yaml').value;
    if (!name) { toast('请填写模板名称', 'error'); return; }
    try {
        if (id == null) {
            await api('/api/templates/custom-items', { method: 'POST', body: JSON.stringify({ name, yaml }) });
        } else {
            await api('/api/templates/custom-items/' + id, { method: 'PUT', body: JSON.stringify({ name, yaml }) });
        }
        document.getElementById('tplItemModal')?.remove();
        toast('已保存');
        await renderConfigTemplates();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteTplItem(id) {
    if (!confirm('确定删除该自定义模板？')) return;
    try {
        await api('/api/templates/custom-items/' + id, { method: 'DELETE' });
        toast('已删除');
        await renderConfigTemplates();
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function loadSettings() {
    try {
        const s = await api('/api/settings');
        document.getElementById('s_include').value = s.include_types || '';
        document.getElementById('s_exclude').value = s.exclude_types || '';
        document.getElementById('s_keywords').value = s.exclude_keywords || '';
        document.getElementById('s_timeout').value = s.fetch_timeout || '15';
        document.getElementById('s_refresh_hours').value = s.refresh_interval_hours != null && s.refresh_interval_hours !== '' ? s.refresh_interval_hours : '6';
        document.getElementById('s_mihomo').value = s.mihomo_path || '';
        document.getElementById('s_autoExpiry').checked = s.auto_disable_on_expiry !== 'false';
        document.getElementById('s_autoEmpty').checked = s.auto_disable_on_empty !== 'false';
        document.getElementById('s_base_yaml').value = s.module_base_override_yaml || '';
        document.getElementById('s_tun_yaml').value = s.module_tun_override_yaml || '';
        document.getElementById('s_dns_yaml').value = s.module_dns_override_yaml || '';
        document.getElementById('s_corpDnsEnabled').checked = s.corp_dns_enabled === 'true';
        document.getElementById('s_corpDnsServers').value = s.corp_dns_servers || '';
        document.getElementById('s_corpDomains').value = s.corp_domain_suffixes || '';
        document.getElementById('s_corpCidrs').value = s.corp_ipcidrs || '';
        document.getElementById('s_rules_tail').value = s.rules_tail || '';
    } catch (e) { toast(e.message, 'error'); }
}

async function saveFilterSettings() {
    try {
        await api('/api/settings', {
            method: 'PUT', body: JSON.stringify({
                include_types: document.getElementById('s_include').value,
                exclude_types: document.getElementById('s_exclude').value,
                exclude_keywords: document.getElementById('s_keywords').value,
            })
        });
        toast('过滤已应用');
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

async function saveSystemSettings() {
    try {
        await api('/api/settings', {
            method: 'PUT', body: JSON.stringify({
                fetch_timeout: document.getElementById('s_timeout').value,
                refresh_interval_hours: document.getElementById('s_refresh_hours').value,
                mihomo_path: document.getElementById('s_mihomo').value,
                auto_disable_on_expiry: document.getElementById('s_autoExpiry').checked ? 'true' : 'false',
                auto_disable_on_empty: document.getElementById('s_autoEmpty').checked ? 'true' : 'false',
            })
        });
        toast('系统设置已保存');
    } catch (e) { toast(e.message, 'error'); }
}

async function saveModuleSettings() {
    try {
        await api('/api/settings', {
            method: 'PUT', body: JSON.stringify({
                module_base_override_yaml: document.getElementById('s_base_yaml').value,
                module_tun_override_yaml: document.getElementById('s_tun_yaml').value,
                module_dns_override_yaml: document.getElementById('s_dns_yaml').value,
                corp_dns_enabled: document.getElementById('s_corpDnsEnabled').checked ? 'true' : 'false',
                corp_dns_servers: document.getElementById('s_corpDnsServers').value,
                corp_domain_suffixes: document.getElementById('s_corpDomains').value,
                corp_ipcidrs: document.getElementById('s_corpCidrs').value,
                rules_tail: document.getElementById('s_rules_tail').value,
            })
        });
        toast('模块配置已保存');
        schedulePreviewRefresh();
    } catch (e) { toast(e.message, 'error'); }
}

/** 用 DOM 挂载 YAML；高亮由 Prism（CDN）完成，主题色见 components.css 中 .yaml-viewer .token.* */
function mountYamlViewer(container, yaml) {
    const raw = yaml == null ? '' : String(yaml);
    container.replaceChildren();
    const lines = raw.split('\n');
    const gutter = document.createElement('div');
    gutter.className = 'yaml-gutter';
    gutter.textContent = lines.map((_, i) => String(i + 1)).join('\n');
    const pre = document.createElement('pre');
    pre.className = 'yaml-code';
    const code = document.createElement('code');
    code.className = 'language-yaml';
    code.textContent = raw;
    pre.appendChild(code);
    container.appendChild(gutter);
    container.appendChild(pre);
    if (typeof Prism !== 'undefined' && Prism.highlightElement) {
        try {
            Prism.highlightElement(code);
        } catch (_) {
            /* 降级：保留纯文本 */
        }
    }
}

function setPreviewMode(mode) {
    _previewMode = mode;
    document.getElementById('pvBtnYaml').classList.toggle('active', mode === 'yaml');
    document.getElementById('pvBtnVis').classList.toggle('active', mode === 'visual');
    document.getElementById('configPreviewYamlWrap').classList.toggle('hidden', mode !== 'yaml');
    document.getElementById('configPreviewVisual').classList.toggle('hidden', mode !== 'visual');
    if (_lastPreview) applyPreviewToDom(_lastPreview);
}

function copyPreviewYaml() {
    const t = _lastPreview && _lastPreview.yaml != null ? String(_lastPreview.yaml) : '';
    if (!t) { toast('暂无 YAML，请先刷新预览', 'error'); return; }
    copyText(t, '已复制 YAML');
}

function applyPreviewToDom(d) {
    const yamlStr = d.yaml != null ? String(d.yaml) : '';
    const mf = d && d.module_flags ? d.module_flags : {};
    const modParts = [];
    if (mf.base_override) modParts.push('base');
    if (mf.tun_override) modParts.push('tun');
    if (mf.dns_override) modParts.push('dns');
    if (mf.corp_dns_enabled) modParts.push('corpDNS');
    if ((mf.rules_tail_count || 0) > 0) modParts.push('rulesTail:' + mf.rules_tail_count);
    const modText = modParts.length ? ` · 模块 ${modParts.join('/')}` : '';
    document.getElementById('previewStats').textContent =
        `节点 ${d.proxy_count} · 代理组 ${d.group_count} · 规则集 ${d.rule_provider_count || 0}${modText}`;
    const yw = document.getElementById('configPreviewYaml');
    if (yw) mountYamlViewer(yw, yamlStr);
    const vis = document.getElementById('configPreviewVisual');
    if (!vis) return;
    const names = Array.isArray(d.proxy_names) ? d.proxy_names : [];
    const gnames = Array.isArray(d.group_names) ? d.group_names : [];
    vis.replaceChildren();
    vis.className = 'flex-1 min-h-0 overflow-hidden p-4 gap-4 text-sm flex flex-col md:flex-row';
    if (_previewMode !== 'visual') vis.classList.add('hidden');
    else vis.classList.remove('hidden');

    function buildCard(title, items) {
        const card = document.createElement('div');
        card.className = 'card flex flex-col flex-1 min-h-0 min-w-0';
        card.style.padding = '1rem';
        const h = document.createElement('h4');
        h.className = 'font-semibold mb-2 shrink-0';
        h.textContent = title + ' (' + items.length + ')';
        const ul = document.createElement('ul');
        ul.className = 'list-disc list-inside text-slate-400 space-y-1 overflow-y-auto flex-1 min-h-0 pr-1';
        items.forEach(n => {
            const li = document.createElement('li');
            li.textContent = n == null ? '' : String(n);
            ul.appendChild(li);
        });
        card.appendChild(h);
        card.appendChild(ul);
        return card;
    }

    vis.appendChild(buildCard('代理组', gnames));
    vis.appendChild(buildCard('节点', names));
}

async function refreshPreview() {
    const yw = document.getElementById('configPreviewYaml');
    if (!yw) return;
    yw.replaceChildren();
    const loading = document.createElement('div');
    loading.className = 'p-4 text-slate-400';
    loading.textContent = '加载中…';
    yw.appendChild(loading);
    try {
        const d = await api('/api/preview');
        _lastPreview = d;
        applyPreviewToDom(d);
        if (_previewMode === 'visual') {
            document.getElementById('configPreviewYamlWrap').classList.add('hidden');
            document.getElementById('configPreviewVisual').classList.remove('hidden');
        } else {
            document.getElementById('configPreviewYamlWrap').classList.remove('hidden');
            document.getElementById('configPreviewVisual').classList.add('hidden');
        }
    } catch (e) {
        yw.replaceChildren();
        const err = document.createElement('div');
        err.className = 'p-4 text-red-400';
        err.textContent = e.message || String(e);
        yw.appendChild(err);
    }
}
