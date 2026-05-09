/**
 * 设置页 — Alpine store action 实现（6 子 Tab）
 * 依赖：core.js（api / toast）
 *       alpine/store.js（settings store 骨架）
 */

document.addEventListener('alpine:init', () => {
    const store = Alpine.store('settings');

    store.load = async function () {
        try {
            const [s, h] = await Promise.all([
                api('/api/settings'),
                api('/api/system/health').catch(() => null),
            ]);
            this.data = {
                ...s,
                _autoExpiry: s.auto_disable_on_expiry !== 'false',
                _autoEmpty:  s.auto_disable_on_empty !== 'false',
                _corpDnsEnabled: s.corp_dns_enabled === 'true',
                fetch_timeout: s.fetch_timeout || '30',
                refresh_interval_hours: (s.refresh_interval_hours != null && s.refresh_interval_hours !== '') ? s.refresh_interval_hours : '6',
            };
            this.health = h;
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveSystem = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify({
                    fetch_timeout: this.data.fetch_timeout,
                    refresh_interval_hours: this.data.refresh_interval_hours,
                    mihomo_path: this.data.mihomo_path || '',
                    auto_disable_on_expiry: this.data._autoExpiry ? 'true' : 'false',
                    auto_disable_on_empty:  this.data._autoEmpty  ? 'true' : 'false',
                }),
            });
            toast('系统设置已保存');
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveFilter = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify({
                    include_types: this.data.include_types || '',
                    exclude_types: this.data.exclude_types || '',
                    exclude_keywords: this.data.exclude_keywords || '',
                }),
            });
            toast('过滤设置已保存');
            Alpine.store('templates').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveModule = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify({
                    module_base_override_yaml: this.data.module_base_override_yaml || '',
                    module_tun_override_yaml:  this.data.module_tun_override_yaml  || '',
                    module_dns_override_yaml:  this.data.module_dns_override_yaml  || '',
                    corp_dns_enabled: this.data._corpDnsEnabled ? 'true' : 'false',
                    corp_dns_servers:    this.data.corp_dns_servers || '',
                    corp_domain_suffixes: this.data.corp_domain_suffixes || '',
                    corp_ipcidrs:        this.data.corp_ipcidrs || '',
                    rules_tail:          this.data.rules_tail || '',
                }),
            });
            toast('模块配置已保存');
            Alpine.store('templates').schedulePreview();
        } catch (e) { toast(e.message, 'error'); }
    };

    store.saveNotify = async function () {
        try {
            await api('/api/settings', {
                method: 'PUT',
                body: JSON.stringify({
                    bark_url:      this.data.bark_url      || '',
                    tg_bot_token:  this.data.tg_bot_token  || '',
                    tg_chat_id:    this.data.tg_chat_id    || '',
                    smtp_host:     this.data.smtp_host     || '',
                    smtp_port:     this.data.smtp_port     || '',
                    smtp_user:     this.data.smtp_user     || '',
                    smtp_pass:     this.data.smtp_pass     || '',
                    smtp_to:       this.data.smtp_to       || '',
                    webhook_url:   this.data.webhook_url   || '',
                }),
            });
        } catch (e) {
            /* saveNotify 由 blur 触发，静默失败 */
            console.warn('保存通知设置失败', e);
        }
    };

    store.loadHealth = async function () {
        try {
            this.health = await api('/api/system/health');
        } catch (_) { this.health = null; }
    };
});
