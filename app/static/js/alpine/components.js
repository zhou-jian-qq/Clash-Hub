/**
 * Alpine.js 共享组件工厂
 * 在 alpine:init 之前同步注册，供各页面 x-data 引用。
 *
 * 使用示例：
 *   <div x-data="modal()" x-show="open" ...>
 */

/**
 * 通用弹窗组件：维护 open / form 状态，支持 Esc 关闭。
 * @param {object} defaultForm - 弹窗内表单的默认字段
 */
function modal(defaultForm = {}) {
    return {
        open: false,
        form: { ...defaultForm },
        _defaultForm: { ...defaultForm },

        show(data = {}) {
            this.form = { ...this._defaultForm, ...data };
            this.open = true;
            this.$nextTick(() => {
                const first = this.$el.querySelector('input,textarea,select');
                if (first) first.focus();
            });
        },
        close() {
            this.open = false;
        },
        onKeydown(e) {
            if (e.key === 'Escape') this.close();
        },
    };
}

/**
 * 延迟状态 mixin：嵌入到节点对象或局部组件中，管理测速的三态。
 */
function latencyState() {
    return {
        _checking: false,
        _latencyMs: null,
        _latencyErr: null,
        _available: null,

        get latencyColor() {
            if (this._available === null) return 'bg-slate-500';
            if (!this._available) return 'bg-red-500';
            const ms = this._latencyMs;
            if (ms <= 200) return 'bg-green-500';
            if (ms <= 500) return 'bg-yellow-400';
            return 'bg-red-500';
        },
        get latencyTextColor() {
            return this.latencyColor.replace('bg-', 'text-');
        },
        get latencyLabel() {
            if (this._checking) return '测速中...';
            if (this._available === null) return '-';
            if (!this._available) return '失败';
            return Math.round(this._latencyMs) + ' ms';
        },
        get latencyBarWidth() {
            if (!this._available || this._latencyMs == null) return 0;
            const ms = this._latencyMs;
            if (ms <= 200) return Math.min(100, (ms / 200) * 40 + 10);
            if (ms <= 500) return Math.min(100, 40 + ((ms - 200) / 300) * 30);
            return Math.min(100, 70 + Math.min(30, ((ms - 500) / 500) * 30));
        },

        setChecking() {
            this._checking = true;
            this._available = null;
            this._latencyMs = null;
            this._latencyErr = null;
        },
        setResult(r) {
            this._checking = false;
            this._available = r.available;
            this._latencyMs = r.latency_ms ?? null;
            this._latencyErr = r.error || (r.available ? null : (r.message || '失败'));
        },
        setError(msg) {
            this._checking = false;
            this._available = false;
            this._latencyErr = msg;
        },
    };
}
