/**
 * 首页：流量概览、订阅链接 Tab、各客户端一键导入与复制
 * 依赖：core.js（api、toast、formatBytes 等）
 */
/** 与后端 SUBSCRIPTION_PROFILE_NAME 一致 */
const SUB_CLIENT_NAME = 'clash_hub';

function buildSubscriptionUrl(subUuid) {
    return location.origin + '/sub/' + subUuid + '?name=' + encodeURIComponent(SUB_CLIENT_NAME);
}

async function loadHome() {
    try {
        const t = await api('/api/traffic');
        const pctNum = t.total_total > 0 ? (t.total_used / t.total_total * 100) : 0;
        const pct = pctNum.toFixed(1);
        /* 使用率分段：绿 → 黄绿 → 黄 → 琥珀 → 橙 → 红 */
        const color = pctNum > 92 ? '#dc2626'
            : pctNum > 82 ? '#ef4444'
                : pctNum > 70 ? '#f97316'
                    : pctNum > 55 ? '#f59e0b'
                        : pctNum > 40 ? '#eab308'
                            : pctNum > 25 ? '#84cc16'
                                : '#22c55e';
        document.getElementById('homeTraffic').innerHTML = `
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
<div class="card text-center"><div class="text-2xl font-bold text-green-500">${formatBytes(t.remaining)}</div><div class="text-sm text-slate-400 mt-1">剩余流量</div></div>
<div class="card text-center"><div class="text-2xl font-bold" style="color:${color}">${formatBytes(t.total_used)}</div><div class="text-sm text-slate-400 mt-1">使用流量</div></div>
<div class="card text-center"><div class="text-2xl font-bold" style="color:var(--accent)">${formatBytes(t.total_total)}</div><div class="text-sm text-slate-400 mt-1">总流量</div></div>
      </div>
      <div class="card">
<div class="flex justify-between text-sm mb-2"><span>使用率</span><span>${pct}%</span></div>
<div class="progress-bar" style="height:.75rem"><div class="progress-fill" style="width:${pct}%;background:${color}"></div></div>
<div class="text-sm text-slate-400 mt-3">最早到期: ${t.expire_date || '-'}</div>
      </div>`;
        const s = await api('/api/settings');
        window._subBaseUrl = buildSubscriptionUrl(s.sub_uuid);
        renderHomeClientPanels();
    } catch (e) { toast(e.message, 'error'); }
}

function clashImportUrl() {
    const subUrl = window._subBaseUrl || '';
    const u = encodeURIComponent(subUrl);
    return 'clash://install-config?url=' + u + '&name=' + encodeURIComponent(SUB_CLIENT_NAME);
}

function clashMetaImportUrl() {
    return clashImportUrl();
}

function shadowrocketImportUrl() {
    const raw = window._subBaseUrl || '';
    try {
        const b = btoa(unescape(encodeURIComponent(raw)));
        return 'sub://' + b;
    } catch (_) {
        return 'sub://' + btoa(raw);
    }
}

function clientRow(iconSvg, name, actionsHtml) {
    return `<div class="client-row">
    <div class="w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-700/50 flex items-center justify-center shrink-0 text-lg shadow-sm">${iconSvg}</div>
    <div class="flex-1 min-w-0 font-medium">${esc(name)}</div>
    <div class="flex flex-wrap gap-2 justify-end">${actionsHtml}</div>
  </div>`;
}

function renderHomeClientPanels() {
    const oneImport = `<button type="button" class="btn btn-primary btn-sm" onclick="openImportClash()">一键导入</button>`;
    const oneImportMeta = `<button type="button" class="btn btn-primary btn-sm" onclick="openImportClashMeta()">一键导入</button>`;
    const copySub = `<button type="button" class="btn btn-outline-accent btn-sm" onclick="copyGenericSub()">复制订阅</button>`;
    const sr = `<button type="button" class="btn btn-primary btn-sm" onclick="openShadowrocket()">一键导入</button>`;

    const iconClash = '<i data-lucide="cat" class="w-5 h-5 text-indigo-400"></i>';
    const iconM = '<i data-lucide="cpu" class="w-5 h-5 text-violet-400"></i>';
    const iconV = '<i data-lucide="zap" class="w-5 h-5 text-blue-400"></i>';
    const iconR = '<i data-lucide="rocket" class="w-5 h-5 text-orange-400"></i>';

    document.getElementById('homeSubPanelIos').innerHTML =
        clientRow(iconClash, 'Clash（iOS / Stash 等）', oneImport) +
        clientRow(iconM, 'Clash Meta', oneImportMeta) +
        clientRow(iconR, 'Shadowrocket', sr);

    document.getElementById('homeSubPanelAndroid').innerHTML =
        clientRow(iconV, 'V2rayNG', copySub) +
        clientRow(iconM, 'Clash Meta for Android', oneImportMeta);

    document.getElementById('homeSubPanelDesktop').innerHTML =
        clientRow(iconClash, 'Clash for Windows / macOS', oneImport) +
        clientRow(iconM, 'Clash Meta', oneImportMeta) +
        clientRow(iconV, 'v2rayN', copySub);

    if (window.lucide) {
        lucide.createIcons();
    }
}

function switchHomeSubTab(tab, btn) {
    document.querySelectorAll('.home-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('homeSubPanelIos').classList.toggle('hidden', tab !== 'ios');
    document.getElementById('homeSubPanelAndroid').classList.toggle('hidden', tab !== 'android');
    document.getElementById('homeSubPanelDesktop').classList.toggle('hidden', tab !== 'desktop');
}

function openImportClash() { window.location.href = clashImportUrl(); }
function openImportClashMeta() { window.location.href = clashMetaImportUrl(); }
function openShadowrocket() { window.location.href = shadowrocketImportUrl(); }

async function copyGenericSub() {
    const u = window._subBaseUrl;
    if (!u) { toast('请先加载首页', 'error'); return; }
    try {
        await navigator.clipboard.writeText(u);
        toast('已复制通用订阅链接');
    } catch (_) { toast('复制失败', 'error'); }
}

async function resetSubUuid() {
    if (!confirm('重置后旧订阅链接将立即失效，客户端需重新导入。确定？')) return;
    try {
        const d = await api('/api/settings/reset-uuid', { method: 'POST', body: '{}' });
        window._subBaseUrl = buildSubscriptionUrl(d.sub_uuid);
        toast('已重置密钥');
    } catch (e) { toast(e.message, 'error'); }
}
