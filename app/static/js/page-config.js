/**
 * 配置页 — Alpine store action 实现
 * 依赖：core.js（api / toast / copyText / mountYamlViewer）
 *       alpine/store.js（config store 骨架）
 */

/** YAML 模板存根（供"创建模板"弹窗默认填充） */
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
    interval: 300
rule-providers: {}
rules:
  - MATCH,🚀 节点选择
`;

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
        try { Prism.highlightElement(code); } catch (_) {}
    }
}

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('config');

    /* ── 静态数据供模板使用 ── */
    store._tplYamlStub = TPL_YAML_STUB;

    store._presetDesc = {
        '精简版': '基础分流, 7 个代理组',
        '标准版': '常用服务分流, 14 个代理组',
        '完整版': '全面精细分流, 33 个代理组',
    };
    store._presetGroups = { '精简版': '7', '标准版': '14', '完整版': '33' };

    store.tplDesc = function (n) { return this._presetDesc[n] || ''; };
    store.tplGroups = function (n) { return this._presetGroups[n] || '-'; };

    /* ── 设置加载 ── */
    store.loadSettings = async function () {
        try {
            const s = await api('/api/settings');
            this.settings = {
                ...s,
                _autoExpiry: s.auto_disable_on_expiry !== 'false',
                _autoEmpty: s.auto_disable_on_empty !== 'false',
                _corpDnsEnabled: s.corp_dns_enabled === 'true',
                fetch_timeout: s.fetch_timeout || '30',
                refresh_interval_hours: s.refresh_interval_hours != null && s.refresh_interval_hours !== '' ? s.refresh_interval_hours : '6',
            };
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveFilterSettings = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT', body: JSON.stringify({
                    include_types: this.settings.include_types || '',
                    exclude_types: this.settings.exclude_types || '',
                    exclude_keywords: this.settings.exclude_keywords || '',
                }),
            });
            toast('过滤已应用');
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveSystemSettings = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT', body: JSON.stringify({
                    fetch_timeout: this.settings.fetch_timeout,
                    refresh_interval_hours: this.settings.refresh_interval_hours,
                    mihomo_path: this.settings.mihomo_path || '',
                    auto_disable_on_expiry: this.settings._autoExpiry ? 'true' : 'false',
                    auto_disable_on_empty: this.settings._autoEmpty ? 'true' : 'false',
                }),
            });
            toast('系统设置已保存');
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveModuleSettings = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT', body: JSON.stringify({
                    module_base_override_yaml: this.settings.module_base_override_yaml || '',
                    module_tun_override_yaml: this.settings.module_tun_override_yaml || '',
                    module_dns_override_yaml: this.settings.module_dns_override_yaml || '',
                    corp_dns_enabled: this.settings._corpDnsEnabled ? 'true' : 'false',
                    corp_dns_servers: this.settings.corp_dns_servers || '',
                    corp_domain_suffixes: this.settings.corp_domain_suffixes || '',
                    corp_ipcidrs: this.settings.corp_ipcidrs || '',
                    rules_tail: this.settings.rules_tail || '',
                }),
            });
            toast('模块配置已保存');
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    /* ── 模板管理 ── */
    store.loadTemplates = async function () {
        try {
            const t = await api('/api/templates');
            this.templates = {
                presets: t.presets || [],
                customs: t.custom_templates || [],
                active: t.active || '',
            };
        } catch (e) { toast(e.message, 'error'); }
    };

    store.selectPreset = async function (name) {
        try {
            await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ name }) });
            toast('已切换到: ' + name);
            await this.loadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.selectCustom = async function (id) {
        try {
            await api('/api/templates/select', { method: 'POST', body: JSON.stringify({ custom_id: id }) });
            toast('已选用自定义模板');
            await this.loadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
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
            await this.loadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteTemplate = async function (id) {
        if (!confirm('确定删除该自定义模板？')) return;
        try {
            await api('/api/templates/custom-items/' + id, { method: 'DELETE' });
            toast('已删除');
            await this.loadTemplates();
            this.schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    /* ── YAML 预览（保留命令式 mountYamlViewer，Alpine 负责 mode/loading 状态） ── */
    store._lastPreviewData = null;

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
        if (yw) mountYamlViewer(yw, yamlStr);

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
    };

    store.refreshPreview = async function () {
        const yw = document.getElementById('configPreviewYaml');
        if (!yw) return;
        yw.replaceChildren();
        const loading = document.createElement('div');
        loading.className = 'p-4 text-slate-400';
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
});

/**
 * 供 app.js switchPage('config') 调用的入口（全局函数名保持兼容）
 * 在 M5 的 app.js 重构后可删除此函数。
 */
async function loadConfigPage() {
    await Alpine.store('config').loadSettings();
    await Alpine.store('config').loadTemplates();
    await Alpine.store('config').refreshPreview();
}
