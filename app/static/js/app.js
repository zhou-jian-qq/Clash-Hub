/**
 * 应用入口：Tab 路由、预览区防抖刷新
 * 依赖：core.js 与各 page-*.js 中的 load* 与全局函数（供 HTML onclick 使用）
 */
function switchPage(name, el_or_push = true) {
    const pages = document.querySelectorAll('[id^="page-"]');
    if (pages.length === 0) return; // 登录页无内容
    
    pages.forEach(p => p.classList.add('hidden'));
    const page = document.getElementById('page-' + name);
    if (page) page.classList.remove('hidden');
    
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navEl = document.querySelector(`.nav-item[data-page="${name}"]`);
    if (navEl) navEl.classList.add('active');
    
    if (name === 'home') loadHome();
    if (name === 'subs') loadSubs();
    if (name === 'imports') loadImportBatches();
    if (name === 'config') loadConfigPage();

    // 兼容原有的 el 参数
    let push = typeof el_or_push === 'boolean' ? el_or_push : true;
    
    if (push) {
        const url = name === 'home' ? '/overview' : '/' + name;
        if (window.location.pathname !== url) {
            history.pushState({ page: name }, '', url);
        }
    }
}

function handleRoute() {
    const path = window.location.pathname;
    let name = 'home';
    if (path.startsWith('/overview')) name = 'home';
    else if (path.startsWith('/subs')) name = 'subs';
    else if (path.startsWith('/imports')) name = 'imports';
    else if (path.startsWith('/config')) name = 'config';
    switchPage(name, false);
}

window.addEventListener('popstate', () => {
    handleRoute();
});

function schedulePreviewRefresh() {
    const pc = document.getElementById('page-config');
    if (!pc || pc.classList.contains('hidden')) return;
    clearTimeout(_previewTimer);
    _previewTimer = setTimeout(() => refreshPreview(), 500);
}

(async () => {
    if (!window.location.pathname.startsWith('/login')) {
        try { 
            await api('/api/settings'); 
            handleRoute();
        }
        catch (e) { 
            console.error('初始化失败', e);
        }
    }
})();
