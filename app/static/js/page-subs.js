/**
 * 订阅管理 — Alpine store action 实现
 * 在 alpine:init 事件中向 Alpine.store('subs') 注入所有 action。
 * 依赖：core.js（api / toast / formatBytes / formatDate / formatIsoTime / showResultModal）
 *       alpine/store.js（store 骨架）
 */

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('subs');

    /** 计算流量进度条所需的 _pct / _color 字段 */
    function _enrichSub(s) {
        const pct = s.total > 0 ? Math.min(100, (s.used / s.total) * 100) : 0;
        s._pct = pct.toFixed(1);
        s._color = pct > 90 ? '#ef4444' : pct > 70 ? '#f59e0b' : '#22c55e';
        return s;
    }

    store.load = async function () {
        try {
            const subs = await api('/api/subscriptions');
            this.items = subs.map(_enrichSub);
            this.selectedIds = new Set();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.toggleEnabled = async function (id, enabled) {
        try {
            await api('/api/subscriptions/' + id, { method: 'PUT', body: JSON.stringify({ enabled }) });
            toast(enabled ? '已启用' : '已禁用');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) {
            toast(e.message, 'error');
            await this.load();
        }
    };

    store.batchEnable = async function (enabled) {
        const ids = this.getSelectedIds();
        if (!ids.length) { toast('请先勾选要操作的订阅', 'error'); return; }
        try {
            await api('/api/subscriptions/batch-enabled', { method: 'POST', body: JSON.stringify({ ids, enabled }) });
            toast(enabled ? '已批量启用' : '已批量禁用');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.batchDelete = async function () {
        const ids = this.getSelectedIds();
        if (!ids.length) { toast('请先勾选要删除的订阅', 'error'); return; }
        if (!confirm('将永久删除选中的 ' + ids.length + ' 条订阅，不可恢复。确定？')) return;
        try {
            const r = await api('/api/subscriptions/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) });
            toast('已删除 ' + (r.deleted || ids.length) + ' 条');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.batchCheck = async function () {
        const ids = this.getSelectedIds();
        if (!ids.length) { toast('请先勾选要检测的订阅', 'error'); return; }
        if (!confirm('将对选中的 ' + ids.length + ' 条订阅逐一检测。\n不可用且当前为「启用」的订阅将被自动禁用。\n确定继续？')) return;
        try {
            toast('正在批量检测，请稍候…');
            const r = await api('/api/subscriptions/batch-check', { method: 'POST', body: JSON.stringify({ ids }) });
            const n = r.auto_disabled || 0;
            toast('检测完成：共 ' + (r.checked || 0) + ' 条，已自动禁用 ' + n + ' 条');
            if (n > 0 && r.disabled_names && r.disabled_names.length)
                alert('已自动禁用的订阅：\n' + r.disabled_names.join('\n'));
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.refreshAll = async function () {
        try {
            toast('正在刷新所有订阅...');
            await api('/api/subscriptions/refresh-all', { method: 'POST' });
            toast('全部刷新完成');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.refresh = async function (id) {
        try {
            await api('/api/subscriptions/' + id + '/refresh', { method: 'POST' });
            toast('刷新成功');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.check = async function (id) {
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
            showResultModal('「' + (r.name || '') + '」 ' + head, detail);
        } catch (e) { toast(e.message, 'error'); }
    };

    store.save = async function (form) {
        const data = {
            name: form.name,
            url: form.url,
            prefix: form.prefix,
            enabled: form.enabled,
            auto_disable: form.auto_disable,
        };
        if (!data.name || !data.url) { toast('名称和URL不能为空', 'error'); return; }
        try {
            if (form.id) {
                await api('/api/subscriptions/' + form.id, { method: 'PUT', body: JSON.stringify(data) });
            } else {
                await api('/api/subscriptions', { method: 'POST', body: JSON.stringify(data) });
            }
            toast(form.id ? '已更新' : '已添加');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteItem = async function (id) {
        if (!confirm('确定删除？')) return;
        try {
            await api('/api/subscriptions/' + id, { method: 'DELETE' });
            toast('已删除');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    /* ── 节点明细 ── */
    store.currentNodes = [];
    store.nodesLoading = false;

    store.loadNodes = async function (subId) {
        this.currentNodes = [];
        this.nodesLoading = true;
        try {
            const r = await api('/api/subscriptions/' + subId + '/nodes');
            this.currentNodes = (r.nodes || []).map(n => ({
                ...n,
                _checking: false,
                _available: null,
                _latencyMs: null,
            }));
        } catch (e) {
            toast(e.message, 'error');
        } finally {
            this.nodesLoading = false;
        }
    };

    store.checkNode = async function (idx) {
        const node = this.currentNodes[idx];
        if (!node) return;
        node._checking = true;
        node._available = null;
        try {
            const r = await api('/api/proxies/check', {
                method: 'POST',
                body: JSON.stringify({ proxy_yaml: node.proxy_yaml }),
            });
            node._available = r.available;
            node._latencyMs = r.latency_ms ?? null;
        } catch (e) {
            node._available = false;
        } finally {
            node._checking = false;
        }
    };

    store.batchCheckNodes = async function () {
        if (!this.currentNodes.length) return;
        toast('开始批量测速 ' + this.currentNodes.length + ' 个节点...');
        const concurrency = 10;
        let i = 0;
        const next = async () => {
            if (i >= this.currentNodes.length) return;
            const idx = i++;
            try { await this.checkNode(idx); } catch (_) {}
            await next();
        };
        await Promise.all(Array.from({ length: concurrency }, next));
        toast('本页节点测速完成');
    };
});
