/**
 * 应用入口：Tab 路由（基于 Alpine store）、移动端导航
 * 依赖：core.js（api）、alpine/store.js（各 store 的 load action）
 */

const VALID_PAGES = ['home', 'subs', 'imports', 'profiles', 'templates', 'settings', 'logs'];

document.addEventListener('alpine:init', () => {

    /** 路由 store：维护当前激活的 Tab 名称 */
    Alpine.store('router', {
        current: 'home',

        /** 切换页面：显示/隐藏 page-* 元素，更新导航高亮，触发对应 store.load，推送 history */
        async go(name, push = true) {
            /* 向后兼容：旧路径 config → settings */
            if (name === 'config') name = 'settings';
            if (!VALID_PAGES.includes(name)) name = 'home';

            /* 显示/隐藏 */
            document.querySelectorAll('[id^="page-"]').forEach(p => p.classList.add('hidden'));
            const page = document.getElementById('page-' + name);
            if (page) page.classList.remove('hidden');

            /* 导航高亮 */
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            const navEl = document.querySelector(`.nav-item[data-page="${name}"]`);
            if (navEl) navEl.classList.add('active');

            this.current = name;
            closeMobileNav();

            /* 加载数据 */
            if (name === 'home')      await Alpine.store('home').load();
            if (name === 'subs')      await Alpine.store('subs').load();
            if (name === 'imports')   await Alpine.store('imports').load();
            if (name === 'profiles')  await Alpine.store('profiles').load();
            if (name === 'templates') await Alpine.store('templates').load();
            if (name === 'settings')  await Alpine.store('settings').load();
            if (name === 'logs')      await Alpine.store('logs').load(1);

            /* 推送 URL */
            if (push) {
                const url = name === 'home' ? '/overview' : '/' + name;
                if (window.location.pathname !== url) {
                    history.pushState({ page: name }, '', url);
                }
            }

            /* 重建 Lucide 图标 */
            if (typeof lucide !== 'undefined') {
                setTimeout(() => lucide.createIcons(), 30);
            }
        },

        fromPath() {
            const path = window.location.pathname;
            if (path.startsWith('/subs'))      return 'subs';
            if (path.startsWith('/imports'))   return 'imports';
            if (path.startsWith('/profiles'))  return 'profiles';
            if (path.startsWith('/templates')) return 'templates';
            if (path.startsWith('/settings'))  return 'settings';
            if (path.startsWith('/config'))    return 'settings';  /* 兼容旧路径 */
            if (path.startsWith('/logs'))      return 'logs';
            return 'home';
        },
    });
});

/* ── 移动端导航（保留全局函数供 main_header.html 的 onclick 使用） ── */
function setMobileNav(open) {
    document.body.classList.toggle('nav-open', open);
    const btn = document.querySelector('.nav-toggle');
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}
function toggleMobileNav() { setMobileNav(!document.body.classList.contains('nav-open')); }
function closeMobileNav()  { setMobileNav(false); }

window.setMobileNav    = setMobileNav;
window.toggleMobileNav = toggleMobileNav;
window.closeMobileNav  = closeMobileNav;

window.addEventListener('keydown', e => { if (e.key === 'Escape') closeMobileNav(); });
window.addEventListener('popstate', () => {
    const name = Alpine.store('router').fromPath();
    Alpine.store('router').go(name, false);
});

/* ── 启动路由（等 Alpine 完成初始化再跑） ── */
document.addEventListener('alpine:initialized', async () => {
    if (window.location.pathname.startsWith('/login')) return;
    try {
        await api('/api/settings');
        const name = Alpine.store('router').fromPath();
        await Alpine.store('router').go(name, false);
        /* 顶栏健康灯（非阻塞） */
        _refreshHeaderHealth();
    } catch (e) {
        console.error('初始化失败', e);
    }
});

/** 更新顶栏健康灯 */
async function _refreshHeaderHealth() {
    try {
        const h = await api('/api/system/health');
        const dot = document.getElementById('headerHealthDot');
        const txt = document.getElementById('headerHealthText');
        if (!dot) return;
        const mihOk = h.mihomo && h.mihomo.available;
        const dbOk  = h.db && h.db.ok;
        const allOk = dbOk && h.scheduler && h.scheduler.running;
        dot.className = 'health-dot ' + (allOk ? 'ok' : !dbOk ? 'error' : 'warn');
        if (txt) txt.textContent = allOk ? '系统正常' : !dbOk ? '数据库异常' : '部分异常';
    } catch (_) {}
}

/** 供旧 loadConfigPage() 调用的兼容桩（settings store 的 load 内部会执行同样的逻辑） */
async function loadConfigPage() {
    await Alpine.store('settings').load();
}
