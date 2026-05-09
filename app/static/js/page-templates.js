/**
 * 模板页 — Alpine store action 实现
 * 依赖：core.js（api / toast / copyText）
 *       alpine/store.js（templates store 骨架）
 *       page-config.js（mountYamlViewer 函数复用）
 */

const _TPL_YAML_STUB = `mixed-port: 7890
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
    interval: 300
rule-providers: {}
rules:
  - MATCH,🚀 节点选择
`;

const _tplDesc = {
    '精简版': '基础分流，7 个代理组',
    '标准版': '常用服务分流，14 个代理组',
    '完整版': '全面精细分流，33 个代理组',
};

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('templates');

    store.preview = { yaml: '', mode: 'yaml', loading: false, stats: '' };

    store.load = async function () {
        try {
            const t = await api('/api/templates');
            this.presets = t.presets || [];
            this.customs = t.custom_templates || [];
            this.active = t.active || '';
        } catch (e) { toast(e.message, 'error'); }
        /* 初次加载后刷新预览 */
        this.refreshPreview();
    };

    store.selectPreset = async function (name) {
        try {
            await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ name }) });
            toast('已切换到: ' + name);
            await this._reloadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.selectCustom = async function (id) {
        try {
            await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ custom_id: id }) });
            toast('已选用自定义模板');
            await this._reloadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store._reloadTemplates = async function () {
        try {
            const t = await api('/api/templates');
            this.presets = t.presets || [];
            this.customs = t.custom_templates || [];
            this.active = t.active || '';
        } catch (_) {}
    };

    store.saveTemplate = async function (id, data) {
        if (!data.name) { toast('请填写模板名称', 'error'); return; }
        try {
            if (id == null) {
                await api('/api/templates/custom-items', { method: 'POST', body: JSON.stringify(data) });
            } else {
                await api('/api/templates/custom-items/' + id, { method: 'PUT', body: JSON.stringify(data) });
            }
            toast('已保存');
            await this._reloadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteTemplate = async function (id) {
        if (!confirm('确定删除该自定义模板？')) return;
        try {
            await api('/api/templates/custom-items/' + id, { method: 'DELETE' });
            toast('已删除');
            await this._reloadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.setPreviewMode = function (mode) {
        this.preview.mode = mode;
        if (this._lastPreviewData) this._applyPreviewToDom(this._lastPreviewData);
    };

    store.copyPreviewYaml = function () {
        const t = this._lastPreviewData && this._lastPreviewData.yaml != null
            ? String(this._lastPreviewData.yaml) : '';
        if (!t) { toast('暂无 YAML，请先刷新预览', 'error'); return; }
        copyText(t, '已复制 YAML');
    };

    store._applyPreviewToDom = function (d) {
        const yamlStr = d.yaml != null ? String(d.yaml) : '';
        const mf = d.module_flags || {};
        const modParts = [];
        if (mf.base_override) modParts.push('base');
        if (mf.tun_override) modParts.push('tun');
        if (mf.dns_override) modParts.push('dns');
        if (mf.corp_dns_enabled) modParts.push('corpDNS');
        if ((mf.rules_tail_count || 0) > 0) modParts.push('rulesTail:' + mf.rules_tail_count);
        const modText = modParts.length ? ' · 模块 ' + modParts.join('/') : '';
        this.preview.stats = '节点 ' + d.proxy_count + ' · 代理组 ' + d.group_count + ' · 规则集 ' + (d.rule_provider_count || 0) + modText;

        const yw = document.getElementById('configPreviewYaml');
        if (yw && typeof mountYamlViewer === 'function') mountYamlViewer(yw, yamlStr);

        const vis = document.getElementById('configPreviewVisual');
        if (!vis) return;
        const names = Array.isArray(d.proxy_names) ? d.proxy_names : [];
        const gnames = Array.isArray(d.group_names) ? d.group_names : [];
        vis.replaceChildren();
        function buildCard(title, items) {
            const card = document.createElement('div');
            card.className = 'card flex flex-col flex-1 min-h-0 min-w-0';
            card.style.padding = '1rem';
            const h = document.createElement('h4');
            h.className = 'font-semibold mb-2 shrink-0';
            h.textContent = title + ' (' + items.length + ')';
            const ul = document.createElement('ul');
            ul.className = 'list-disc list-inside space-y-1 overflow-y-auto flex-1 min-h-0 pr-1';
            ul.style.color = 'var(--muted)';
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
    };

    store.refreshPreview = async function () {
        const yw = document.getElementById('configPreviewYaml');
        if (!yw) return;
        yw.replaceChildren();
        const loading = document.createElement('div');
        loading.className = 'p-4';
        loading.style.color = 'var(--muted)';
        loading.textContent = '加载中…';
        yw.appendChild(loading);
        this.preview.loading = true;
        try {
            const d = await api('/api/preview');
            this._lastPreviewData = d;
            this._applyPreviewToDom(d);
        } catch (e) {
            yw.replaceChildren();
            const err = document.createElement('div');
            err.className = 'p-4 text-red-400';
            err.textContent = e.message || String(e);
            yw.appendChild(err);
        } finally {
            this.preview.loading = false;
        }
    };

    /* 同步兼容旧 config store */
    const oldConfig = Alpine.store('config');
    if (oldConfig) {
        oldConfig._tplYamlStub = _TPL_YAML_STUB;
        oldConfig._presetDesc = _tplDesc;
    }
});
