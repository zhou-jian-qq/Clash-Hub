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

    /** 从 User-Agent 提取客户端名称和版本 */
    store.parseClient = function (ua) {
        if (!ua) return '';
        const normalizedUa = String(ua).replace(/%20/gi, ' ');
        const lower = normalizedUa.toLowerCase();
        const versionPart = 'v?([0-9][0-9a-z.+_-]*)';
        const cleanVersion = (version) => (version || '')
            .replace(/^v/i, '')
            .replace(/[;,)]+$/, '');
        const display = (name, version) => {
            const cleaned = cleanVersion(version);
            return cleaned ? `${name} ${cleaned}` : name;
        };
        const matchClient = (patterns) => {
            for (const [name, pattern] of patterns) {
                const m = normalizedUa.match(pattern);
                if (m) return display(name, m[1]);
            }
            return '';
        };

        const matched = matchClient([
            ['Clash Verge', new RegExp(`clash[\\s._-]*verge(?:[\\s._-]*rev)?(?:/|\\s)+${versionPart}`, 'i')],
            ['Clash Meta Android', new RegExp(`clashmetaforandroid/${versionPart}`, 'i')],
            ['Clash for Windows', new RegExp(`clash(?:for)?windows/${versionPart}`, 'i')],
            ['ClashX Pro', new RegExp(`clashx[\\s._-]*pro/${versionPart}`, 'i')],
            ['ClashX', new RegExp(`clashx/${versionPart}`, 'i')],
            ['Mihomo Party', new RegExp(`mihomo[\\s._-]*party/${versionPart}`, 'i')],
            ['Mihomo', new RegExp(`mihomo/${versionPart}`, 'i')],
            ['Clash Meta', new RegExp(`clash[\\s._-]*meta/${versionPart}`, 'i')],
            ['Clash', new RegExp(`clash(?:[\\s._-]*premium)?/${versionPart}`, 'i')],
            ['Stash', new RegExp(`stash/${versionPart}`, 'i')],
            ['Surge', new RegExp(`surge(?:\\s+iOS)?/${versionPart}`, 'i')],
            ['Quantumult X', new RegExp(`quantumult\\s*x/${versionPart}`, 'i')],
            ['Quantumult', new RegExp(`quantumult/${versionPart}`, 'i')],
            ['Shadowrocket', new RegExp(`shadowrocket/${versionPart}`, 'i')],
            ['Loon', new RegExp(`loon/${versionPart}`, 'i')],
            ['v2rayN', new RegExp(`v2rayn/${versionPart}`, 'i')],
            ['v2rayNG', new RegExp(`v2rayng/${versionPart}`, 'i')],
            ['NekoBox Android', new RegExp(`nekoboxforandroid/${versionPart}`, 'i')],
            ['NekoBox', new RegExp(`nekobox/${versionPart}`, 'i')],
            ['sing-box', new RegExp(`sing-box/${versionPart}`, 'i')],
            ['Hiddify', new RegExp(`hiddify(?:next)?/${versionPart}`, 'i')],
        ]);
        if (matched) return matched;

        if (/clash[\s._-]*verge/.test(lower)) {
            return 'Clash Verge';
        }
        if (/clashmetaforandroid/.test(lower)) return 'Clash Meta Android';
        if (/clash(?:for)?windows/.test(lower)) return 'Clash for Windows';
        if (/clashx[\s._-]*pro/.test(lower)) return 'ClashX Pro';
        if (/clashx/.test(lower)) return 'ClashX';
        if (/mihomo[\s._-]*party/.test(lower)) return 'Mihomo Party';
        if (/clash\.meta/.test(lower) || /clashmeta/.test(lower)) return 'Clash Meta';
        if (/mihomo/.test(lower)) return 'Mihomo';
        if (/stash/.test(lower)) return 'Stash';
        if (/clash\//.test(lower) || /clash-premium/.test(lower)) return 'Clash';
        if (/surge/.test(lower)) return 'Surge';
        if (/quantumult\s*x/.test(lower)) return 'Quantumult X';
        if (/quantumult/.test(lower)) return 'Quantumult';
        if (/shadowrocket/.test(lower)) return 'Shadowrocket';
        if (/loon/.test(lower)) return 'Loon';
        if (/v2rayng/.test(lower)) return 'v2rayNG';
        if (/v2rayn/.test(lower)) return 'v2rayN';
        if (/nekoboxforandroid/.test(lower)) return 'NekoBox Android';
        if (/nekobox/.test(lower)) return 'NekoBox';
        if (/sing-box/.test(lower)) return 'sing-box';
        if (/hiddify/.test(lower)) return 'Hiddify';
        return '';
    };
});
