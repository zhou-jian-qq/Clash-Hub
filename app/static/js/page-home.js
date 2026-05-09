/**
 * 首页 — Alpine store action 实现
 * 依赖：core.js（api / toast / formatBytes / copyText）
 *       alpine/store.js（home store 骨架）
 */

const SUB_CLIENT_NAME = 'clash_hub';

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('home');

    function _refreshLucide() {
        if (typeof lucide === 'undefined') return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => { lucide.createIcons(); });
        });
    }

    function _buildSubUrl(uuid) {
        return location.origin + '/sub/' + uuid + '?name=' + encodeURIComponent(SUB_CLIENT_NAME);
    }
    function _buildV2rayUrl(uuid) {
        return location.origin + '/sub/' + uuid + '/v2ray';
    }
    function _shadowrocketUrl(raw) {
        try { return 'sub://' + btoa(unescape(encodeURIComponent(raw))); }
        catch (_) { return 'sub://' + btoa(raw); }
    }
    function _clashImportUrl(subBaseUrl) {
        return 'clash://install-config?url=' + encodeURIComponent(subBaseUrl) + '&name=' + encodeURIComponent(SUB_CLIENT_NAME);
    }

    store.load = async function () {
        try {
            const [t, s] = await Promise.all([api('/api/traffic'), api('/api/settings')]);
            const pctNum = t.total_total > 0 ? (t.total_used / t.total_total * 100) : 0;
            t._pct = pctNum.toFixed(1);
            t._usedColor = pctNum > 92 ? '#dc2626'
                : pctNum > 82 ? '#ef4444'
                : pctNum > 70 ? '#f97316'
                : pctNum > 55 ? '#f59e0b'
                : pctNum > 40 ? '#eab308'
                : pctNum > 25 ? '#84cc16'
                : '#22c55e';
            this.traffic = t;
            this.subBaseUrl = _buildSubUrl(s.sub_uuid);
            this.subV2rayUrl = _buildV2rayUrl(s.sub_uuid);
            _refreshLucide();
        } catch (e) { toast(e.message, 'error'); }

        /* 健康检查（非阻塞）*/
        this.loadHealth();
    };

    store.loadHealth = async function () {
        try {
            this.health = await api('/api/system/health');
        } catch (_) {
            this.health = null;
        }
        _refreshLucide();
    };

    /** 概览「订阅链接」子 Tab：切换后 Alpine x-if 会插入新 DOM，需重建 Lucide */
    store.setSubTab = function (tab) {
        this.activeTab = tab;
        _refreshLucide();
    };

    store.resetUuid = async function () {
        if (!confirm('重置后旧订阅链接将立即失效，客户端需重新导入。确定？')) return;
        try {
            const d = await api('/api/settings/reset-uuid', { method: 'POST', body: '{}' });
            this.subBaseUrl = _buildSubUrl(d.sub_uuid);
            this.subV2rayUrl = _buildV2rayUrl(d.sub_uuid);
            toast('已重置密钥');
            _refreshLucide();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.copyGenericSub = async function () {
        if (!this.subBaseUrl) { toast('请先加载首页', 'error'); return; }
        await copyText(this.subBaseUrl, '已复制通用订阅链接');
    };

    store.copyV2raySub = async function () {
        if (!this.subV2rayUrl) { toast('请先加载首页', 'error'); return; }
        await copyText(this.subV2rayUrl, '已复制 V2Ray 订阅链接');
    };

    store.openImportClash = function () { window.location.href = _clashImportUrl(this.subBaseUrl); };
    store.openImportClashMeta = function () { window.location.href = _clashImportUrl(this.subBaseUrl); };
    store.openShadowrocket = function () { window.location.href = _shadowrocketUrl(this.subBaseUrl); };
});
