/**
 * Alpine.js 全局 Store 注册
 * 必须在 Alpine defer 脚本之前同步执行（由 base.html 保证加载顺序）。
 *
 * 各 store 的 action 实现（loadSubs / loadImportBatches / ...）
 * 由对应的 page-*.js 在 document.addEventListener('alpine:init') 中注入，
 * 以保持业务逻辑与 store 骨架的分离。
 */

document.addEventListener('alpine:init', () => {

    /* ─── subs store ─────────────────────────────────────────────── */
    Alpine.store('subs', {
        items: [],
        selectedIds: new Set(),
        currentNodes: [],
        nodesLoading: false,

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

        /* 以下 action 由 page-subs.js 在 alpine:init 后注入 */
        async load() {},
        async toggleEnabled(id, enabled) {},
        async batchEnable(enabled) {},
        async batchDelete() {},
        async batchCheck() {},
        async refreshAll() {},
        async refresh(id) {},
        async check(id) {},
        async save(id, data) {},
        async deleteItem(id) {},
        async loadNodes(id) {},
        async checkNode(idx) {},
        async batchCheckNodes() {},
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
    });

    /* ─── config store ────────────────────────────────────────────── */
    Alpine.store('config', {
        settings: {},
        templates: { presets: [], customs: [], active: '' },
        preview: { yaml: '', mode: 'yaml', loading: false, stats: '' },
        _previewTimer: null,

        schedulePreview() {
            clearTimeout(this._previewTimer);
            this._previewTimer = setTimeout(() => this.refreshPreview(), 500);
        },

        /* actions 由 page-config.js 注入 */
        async loadSettings() {},
        async saveFilterSettings() {},
        async saveSystemSettings() {},
        async saveModuleSettings() {},
        async loadTemplates() {},
        async selectPreset(name) {},
        async selectCustom(id) {},
        async saveTemplate(id, data) {},
        async deleteTemplate(id) {},
        async refreshPreview() {},
        setPreviewMode(mode) {},
    });

    /* ─── home store ──────────────────────────────────────────────── */
    Alpine.store('home', {
        traffic: null,
        subBaseUrl: '',
        subV2rayUrl: '',
        activeTab: 'ios',

        /* actions 由 page-home.js 注入 */
        async load() {},
        async resetUuid() {},
    });

    /* ─── logs store ──────────────────────────────────────────────── */
    Alpine.store('logs', {
        items: [],
        total: 0,
        page: 1,
        pageSize: 50,
        totalPages: 1,
        filter: { ip: '', date_from: '', date_to: '' },

        /* actions 由 page-logs.js 注入 */
        async load(p) {},
        async clearLogs() {},
        applyFilter() {},
        resetFilter() {},
    });

});
