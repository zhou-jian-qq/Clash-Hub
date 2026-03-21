/**
 * 应用入口：登录后主界面切换、Tab 路由、预览区防抖刷新
 * 依赖：core.js 与各 page-*.js 中的 load* 与全局函数（供 HTML onclick 使用）
 */
async function showMain() {
    document.getElementById('loginPage').classList.add('hidden');
    document.getElementById('mainPage').classList.remove('hidden');
    const navHome = document.querySelector('.nav-item[data-page="home"]');
    switchPage('home', navHome);
}

function switchPage(name, el) {
    document.querySelectorAll('[id^="page-"]').forEach(p => p.classList.add('hidden'));
    const page = document.getElementById('page-' + name);
    page.classList.remove('hidden');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');
    if (name === 'home') loadHome();
    if (name === 'subs') loadSubs();
    if (name === 'imports') loadImportBatches();
    if (name === 'config') loadConfigPage();
}

function schedulePreviewRefresh() {
    const pc = document.getElementById('page-config');
    if (!pc || pc.classList.contains('hidden')) return;
    clearTimeout(_previewTimer);
    _previewTimer = setTimeout(() => refreshPreview(), 500);
}

(async () => {
    if (TOKEN) {
        try { await api('/api/settings'); showMain(); }
        catch { doLogout(); }
    }
})();
