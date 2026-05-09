/**
 * Alpine.js 全局 Store 注册
 * 必须在 Alpine defer 脚本之前同步执行（由 base.html 保证加载顺序）。
 *
 * 各 store 的 action 实现由对应的 page-*.js 在 document.addEventListener('alpine:init') 中注入。
 */

document.addEventListener('alpine:init', () => {

    /* ─── subs store ─────────────────────────────────────────────── */
    Alpine.store('subs', {
        items: [],
        selectedIds: new Set(),
        currentNodes: [],
        nodesLoading: false,
        tagFilter: '',          /* 标签筛选 */
        sseRunning: false,      /* SSE 刷新进度 */
        sseItems: [],           /* [{id, name, status, ok, node_count, latency_ms}] */

        get allTags() {
            const s = new Set();
            for (const item of this.items) {
                for (const t of (item.tags || '').split(',')) {
                    const v = t.trim();
                    if (v) s.add(v);
                }
            }
            return [...s].sort();
        },

        get filteredItems() {
            if (!this.tagFilter) return this.items;
            return this.items.filter(s => {
                return (s.tags || '').split(',').map(t => t.trim()).includes(this.tagFilter);
            });
        },

        get allSelected() {
            return this.items.length > 0 && this.selectedIds.size === this.items.length;
        },
        get someSelected() {
            return this.selectedIds.size > 0 && this.selectedIds.size < this.items.length;
        },

        toggleSelect(id, checked) {
            if (checked) this.selectedIds.add(id);
            else this.selectedIds.delete(id);
            this.selectedIds = new Set(this.selectedIds);
        },
        selectAll(checked) {
            this.selectedIds = checked ? new Set(this.items.map(s => s.id)) : new Set();
        },
        getSelectedIds() {
            return [...this.selectedIds];
        },

        /* 以下 action 由 page-subs.js 注入 */
        async load() {},
        async toggleEnabled(id, enabled) {},
        async batchEnable(enabled) {},
        async batchDelete() {},
        async batchCheck() {},
        async refreshAll() {},
        async refreshAllSSE() {},
        async refresh(id) {},
        async check(id) {},
        async save(id, data) {},
        async deleteItem(id) {},
        async loadNodes(id) {},
        async checkNode(idx) {},
        async batchCheckNodes() {},
        async showDedupePreview() {},
    });

    /* ─── imports store ───────────────────────────────────────────── */
    Alpine.store('imports', {
        batches: [],

        findNode(id) {
            for (const b of this.batches) {
                const n = (b.nodes || []).find(x => x.id === id);
                if (n) return n;
            }
            return null;
        },

        /* actions 由 page-imports.js 注入 */
        async load() {},
        async importBatch(name, text) {},
        async renameBatch(id, name) {},
        async deleteBatch(id) {},
        async setBatchAllEnabled(id, enabled) {},
        async toggleNodeEnabled(id, enabled) {},
        async saveNodeYaml(id, yaml) {},
        async deleteNode(id) {},
        async checkNode(id, showAlert) {},
        async batchCheckBatch(batchId) {},
        async nodeToV2rayUri(yaml) {},
        async showNodeQR(nodeId) {},
    });

    /* ─── profiles store ──────────────────────────────────────────── */
    Alpine.store('profiles', {
        items: [],
        /* actions 由 page-profiles.js 注入 */
        async load() {},
        async create(data) {},
        async update(id, data) {},
        async deleteItem(id) {},
    });

    /* ─── templates store ─────────────────────────────────────────── */
    Alpine.store('templates', {
        presets: [],
        customs: [],
        active: '',
        preview: { yaml: '', loading: false, stats: '' },
        _lastPreviewData: null,
        _previewTimer: null,

        schedulePreview() {
            clearTimeout(this._previewTimer);
            this._previewTimer = setTimeout(() => this.refreshPreview(), 400);
        },

        /* actions 由 page-templates.js 注入 */
        async load() {},
        async selectPreset(name) {},
        async selectCustom(id) {},
        async saveTemplate(id, data) {},
        async deleteTemplate(id) {},
        async refreshPreview() {},
        setPreviewMode(mode) {},
        copyPreviewYaml() {},
    });

    /* ─── settings store ──────────────────────────────────────────── */
    Alpine.store('settings', {
        data: {},
        health: null,
        activeTab: 'system',

        /* actions 由 page-settings.js 注入 */
        async load() {},
        async saveSystem() {},
        async saveFilter() {},
        async saveModule() {},
        async saveNotify() {},
        async testNotify() {},
        async changePassword() {},
        async exportBackup() {},
        async importBackup(file, dryRun) {},
        async loadHealth() {},
    });

    /* ─── config store (兼容遗留代码) ────────────────────────────── */
    Alpine.store('config', {
        settings: {},
        templates: { presets: [], customs: [], active: '' },
        preview: { yaml: '', mode: 'yaml', loading: false, stats: '' },
        _previewTimer: null,

        schedulePreview() {
            /* 当 templates store 存在时，转发过去；否则 no-op */
            const ts = Alpine.store('templates');
            if (ts && ts.schedulePreview) ts.schedulePreview();
        },

        async loadSettings() {
            const ss = Alpine.store('settings');
            if (ss && ss.load) await ss.load();
        },
        async loadTemplates() {
            const ts = Alpine.store('templates');
            if (ts && ts.load) await ts.load();
        },
        async refreshPreview() {
            const ts = Alpine.store('templates');
            if (ts && ts.refreshPreview) await ts.refreshPreview();
        },
        setPreviewMode(mode) {
            const ts = Alpine.store('templates');
            if (ts && ts.setPreviewMode) ts.setPreviewMode(mode);
        },
        /* 保留 tplDesc/tplGroups 供遗留代码调用 */
        _presetDesc: { '精简版': '基础分流, 7 个代理组', '标准版': '常用服务分流, 14 个代理组', '完整版': '全面精细分流, 33 个代理组' },
        _presetGroups: { '精简版': '7', '标准版': '14', '完整版': '33' },
        tplDesc(n) { return this._presetDesc[n] || ''; },
        tplGroups(n) { return this._presetGroups[n] || '-'; },
    });

    /* ─── home store ──────────────────────────────────────────────── */
    Alpine.store('home', {
        traffic: null,
        health: null,
        subBaseUrl: '',
        subV2rayUrl: '',
        activeTab: 'ios',

        /* actions 由 page-home.js 注入 */
        async load() {},
        async resetUuid() {},
        async loadHealth() {},
        setSubTab(_tab) {},
    });

    /* ─── logs store ──────────────────────────────────────────────── */
    Alpine.store('logs', {
        activeTab: 'access',

        /* access logs */
        items: [],
        total: 0,
        page: 1,
        pageSize: 50,
        totalPages: 1,
        filter: { ip: '', date_from: '', date_to: '' },

        /* audit logs */
        auditItems: [],
        auditTotal: 0,
        auditPage: 1,
        auditPageSize: 50,
        auditTotalPages: 1,

        /* probe history */
        probeTarget: '',
        probeDays: 7,
        probeRecords: [],
        probeLoading: false,

        /* actions 由 page-logs.js 注入 */
        async load(p) {},
        async clearLogs() {},
        applyFilter() {},
        resetFilter() {},
        async loadAudit(p) {},
        async clearAudit() {},
        async loadProbe() {},
        parseClient(ua) { return ''; },
    });

});
