/**
 * 日志页 — Alpine store action 实现（访问日志 / 操作审计日志 / 探测历史）
 * 依赖：core.js（api / toast / formatIsoTime）
 *       alpine/store.js（logs store 骨架）
 */

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('logs');

    /* ── 访问日志 ── */
    store.load = async function (p) {
        p = Math.max(1, p || 1);
        if (p > this.totalPages && this.totalPages > 0) return;
        this.page = p;
        try {
            const params = new URLSearchParams({ page: p, page_size: this.pageSize });
            const f = this.filter;
            if (f.ip) params.set('ip', f.ip);
            if (f.date_from) params.set('date_from', f.date_from);
            if (f.date_to) params.set('date_to', f.date_to);
            const data = await api(`/api/sub-access-logs?${params}`);
            this.total = data.total || 0;
            this.items = data.items || [];
            this.totalPages = Math.max(1, Math.ceil(this.total / this.pageSize));
        } catch (e) {
            this.items = [];
            toast(e.message, 'error');
        }
    };

    store.applyFilter = function () {
        this.totalPages = 1;
        this.load(1);
    };

    store.resetFilter = function () {
        this.filter = { ip: '', date_from: '', date_to: '' };
        this.totalPages = 1;
        this.load(1);
    };

    store.clearLogs = async function () {
        if (!confirm('确定要清空所有访问日志吗？此操作不可恢复。')) return;
        try {
            await api('/api/sub-access-logs', { method: 'DELETE' });
            toast('日志已清空');
            await this.load(1);
        } catch (e) {
            toast('清空失败：' + e.message, 'error');
        }
    };

    /* ── 操作审计日志 ── */
    store.loadAudit = async function (p) {
        p = Math.max(1, p || 1);
        this.auditPage = p;
        try {
            const data = await api(`/api/audit-logs?page=${p}&page_size=${this.auditPageSize}`);
            this.auditTotal = data.total || 0;
            this.auditItems = data.items || [];
            this.auditTotalPages = Math.max(1, Math.ceil(this.auditTotal / this.auditPageSize));
        } catch (e) {
            this.auditItems = [];
            toast(e.message, 'error');
        }
    };

    store.clearAudit = async function () {
        if (!confirm('确定要清空所有操作日志吗？')) return;
        try {
            await api('/api/audit-logs', { method: 'DELETE' });
            toast('操作日志已清空');
            await this.loadAudit(1);
        } catch (e) {
            toast('清空失败：' + e.message, 'error');
        }
    };

    /* ── 探测历史（由 page_logs.html 中的 x-data 内联处理，此处为兼容桩） ── */
    store.loadProbe = async function () {};

    /** 从 User-Agent 提取客户端名称 */
    store.parseClient = function (ua) {
        if (!ua) return '';
        const lower = ua.toLowerCase();
        if (/clash[\s\-_]?verge/.test(lower)) {
            const m = ua.match(/[Cc]lash[\s\-_][Vv]erge[\/\s]*([\d.]+)/);
            return 'Clash Verge' + (m ? ' ' + m[1] : '');
        }
        if (/clashmetaforandroid/.test(lower)) {
            const m = ua.match(/ClashMetaForAndroid\/([\d.]+)/i);
            return 'Clash Meta Android' + (m ? ' ' + m[1] : '');
        }
        if (/clash\.meta/.test(lower) || /clashmeta/.test(lower)) return 'Clash Meta';
        if (/mihomo/.test(lower)) {
            const m = ua.match(/mihomo\/([\d.]+)/i);
            return 'Mihomo' + (m ? ' ' + m[1] : '');
        }
        if (/stash/.test(lower)) {
            const m = ua.match(/[Ss]tash\/([\d.]+)/);
            return 'Stash' + (m ? ' ' + m[1] : '');
        }
        if (/clash\//.test(lower) || /clash-premium/.test(lower)) return 'Clash';
        if (/surge/.test(lower)) return 'Surge';
        if (/quantumult/.test(lower)) return 'Quantumult';
        if (/shadowrocket/.test(lower)) return 'Shadowrocket';
        if (/loon/.test(lower)) return 'Loon';
        return '';
    };
});
