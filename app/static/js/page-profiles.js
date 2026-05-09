/**
 * 订阅方案（SubProfile）页 — Alpine store action 实现
 * 依赖：core.js（api / toast）
 *       alpine/store.js（profiles store 骨架）
 */

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('profiles');

    store.load = async function () {
        try {
            this.items = await api('/api/profiles');
        } catch (e) { toast(e.message, 'error'); }
    };

    store.create = async function (data) {
        if (!data.name || !data.name.trim()) { toast('方案名称不能为空', 'error'); return; }
        try {
            const created = await api('/api/profiles', {
                method: 'POST',
                body: JSON.stringify({
                    name: data.name.trim(),
                    template_name: data.template_name || '标准版',
                    tag_filter: data.tag_filter || '',
                    custom_template_id: data.custom_template_id || null,
                }),
            });
            this.items = [...this.items, created];
            toast('已创建订阅方案「' + created.name + '」');
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.update = async function (id, data) {
        try {
            const updated = await api('/api/profiles/' + id, {
                method: 'PUT',
                body: JSON.stringify({
                    name: data.name.trim(),
                    template_name: data.template_name || '标准版',
                    tag_filter: data.tag_filter || '',
                    custom_template_id: data.custom_template_id || null,
                }),
            });
            const idx = this.items.findIndex(p => p.id === id);
            if (idx >= 0) this.items[idx] = updated;
            this.items = [...this.items];
            toast('已更新');
        } catch (e) { toast(e.message, 'error'); throw e; }
    };

    store.deleteItem = async function (id) {
        if (!confirm('确定删除该订阅方案？相关链接将立即失效。')) return;
        try {
            await api('/api/profiles/' + id, { method: 'DELETE' });
            this.items = this.items.filter(p => p.id !== id);
            toast('已删除');
        } catch (e) { toast(e.message, 'error'); }
    };
});
