/**
 * 节点管理 — Alpine store action 实现
 * 依赖：core.js（api / toast / formatIsoTime / copyText）
 *       alpine/store.js（store 骨架）
 */

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('imports');

    /** 为每个节点附加响应式测速状态字段 */
    function _enrichNode(n) {
        n._checking = false;
        n._available = null;
        n._latencyMs = null;
        return n;
    }

    store.load = async function () {
        try {
            const batches = await api('/api/import-batches');
            this.batches = batches.map(b => {
                b.nodes = (b.nodes || []).map(_enrichNode);
                return b;
            });
        } catch (e) { toast(e.message, 'error'); }
    };

    store.importBatch = async function (name, text) {
        try {
            toast('正在导入…');
            const r = await api('/api/import-batches/import', {
                method: 'POST',
                body: JSON.stringify({ name, text }),
            });
            let msg = '已导入批次 #' + (r.batch_id || '') + '，共 ' + (r.created || 0) + ' 个节点';
            if (r.skipped) msg += '，跳过 ' + r.skipped + ' 行无效内容';
            toast(msg);
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.renameBatch = async function (id, name) {
        try {
            await api('/api/import-batches/' + id, { method: 'PUT', body: JSON.stringify({ name }) });
            toast('已更新');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteBatch = async function (id) {
        if (!confirm('将删除该批次及其下全部节点，不可恢复。确定？')) return;
        try {
            await api('/api/import-batches/' + id, { method: 'DELETE' });
            toast('已删除批次');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.setBatchAllEnabled = async function (batchId, enabled) {
        const act = enabled ? '启用' : '禁用';
        try {
            await api('/api/import-batches/' + batchId, {
                method: 'PUT',
                body: JSON.stringify({ set_all_nodes_enabled: enabled }),
            });
            toast('已批量' + act);
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.toggleNodeEnabled = async function (id, enabled) {
        try {
            await api('/api/imported-nodes/' + id, { method: 'PUT', body: JSON.stringify({ enabled }) });
            toast(enabled ? '已启用' : '已禁用');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) {
            toast(e.message, 'error');
            await this.load();
        }
    };

    store.saveNodeYaml = async function (id, proxy_yaml) {
        try {
            await api('/api/imported-nodes/' + id, { method: 'PUT', body: JSON.stringify({ proxy_yaml }) });
            toast('已保存');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteNode = async function (id) {
        if (!confirm('确定删除该节点？')) return;
        try {
            await api('/api/imported-nodes/' + id, { method: 'DELETE' });
            toast('已删除');
            await this.load();
            Alpine.store('config').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.nodeToV2rayUri = async function (proxy_yaml) {
        if (!proxy_yaml.trim()) { toast('内容为空', 'error'); return; }
        try {
            const r = await api('/api/proxies/to-v2ray-uri', {
                method: 'POST',
                body: JSON.stringify({ proxy_yaml }),
            });
            await copyText(r.uri, '已复制 V2Ray 分享链接');
        } catch (e) { toast(e.message, 'error'); }
    };

    /** 单节点测速：更新节点的三个响应式状态字段 */
    store.checkNode = async function (id, showAlert = false) {
        const node = this.findNode(id);
        if (!node) return;
        node._checking = true;
        node._available = null;
        node._latencyMs = null;
        try {
            if (showAlert) toast('正在测速…');
            const r = await api('/api/imported-nodes/' + id + '/check', { method: 'POST', body: '{}' });
            node._available = r.available;
            node._latencyMs = r.latency_ms ?? null;
            if (showAlert) {
                const head = r.available ? '可用' : '不可用';
                let tcpLine = '';
                const pk = r.probe_kind || '';
                if (r.latency_ms != null)
                    tcpLine = '\n延迟（' + (pk === 'httpx' ? '经代理 URL' : pk === 'mihomo' ? 'Mihomo URL' : pk === 'tcp-fallback' ? 'TCP 兜底' : '探测') + '）: ' + Math.round(r.latency_ms) + ' ms';
                else if (r.tcp_tested && !r.available)
                    tcpLine = '\n已尝试探测（失败，见上文说明）';
                showResultModal('「' + (r.display_name || '') + '」 ' + head, (r.message || '') + tcpLine);
            }
        } catch (e) {
            node._available = false;
            if (showAlert) toast(e.message, 'error');
        } finally {
            node._checking = false;
        }
    };

    /** 批次内所有节点并发测速（最大并发 10） */
    store.batchCheckBatch = async function (batchId) {
        const b = this.batches.find(x => x.id === batchId);
        if (!b || !b.nodes || b.nodes.length === 0) {
            toast('该批次下无节点', 'error');
            return;
        }
        toast('开始批量测速 ' + b.nodes.length + ' 个节点...');
        const nodes = b.nodes;
        const concurrency = 10;
        let i = 0;
        const next = async () => {
            if (i >= nodes.length) return;
            const node = nodes[i++];
            try { await this.checkNode(node.id, false); } catch (_) {}
            await next();
        };
        await Promise.all(Array.from({ length: concurrency }, next));
        toast('批次 ' + b.name + ' 测速完成');
    };
});
