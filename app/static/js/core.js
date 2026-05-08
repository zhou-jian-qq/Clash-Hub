/**
 * Clash Hub 管理后台 — 核心工具与全局状态
 * - 全局变量、api()/toast、时间格式化、转义、主题、登录/登出
 * - 依赖：无（先于其它 /static/js/* 加载）；内联 HTML 通过全局函数名调用
 */
const API = '';
let _subsCache = [];
let _importBatchesCache = [];
let _previewTimer = null;
let _previewMode = 'yaml';
let _lastPreview = null;

function headers() { return { 'Content-Type': 'application/json' }; }

async function api(path, opts = {}) {
    // 避免浏览器对 GET /api/... 使用磁盘/内存缓存，导致批量操作后列表仍是旧 enabled 状态
    const res = await fetch(API + path, { headers: headers(), cache: 'no-store', ...opts });
    if (res.status === 401) { doLogout(); throw new Error('未登录'); }
    if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        let d = e.detail || '请求失败';
        if (Array.isArray(d)) d = d.map(x => (x && x.msg) || String(x)).join('; ');
        throw new Error(typeof d === 'string' ? d : JSON.stringify(d));
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('json') ? res.json() : res.text();
}

function toast(msg, type = 'success') {
    const d = document.createElement('div');
    d.className = 'toast ' + (type === 'error' ? 'bg-red-600' : 'bg-green-600');
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 3000);
}

function copyTextFallback(text) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    // 隐藏文本框，防止页面滚动
    textArea.style.position = "fixed";
    textArea.style.top = "0";
    textArea.style.left = "0";
    textArea.style.opacity = "0";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            toast('已复制');
        } else {
            toast('复制失败: 浏览器阻止', 'error');
        }
    } catch (err) {
        toast('复制失败: ' + err.message, 'error');
    }
    document.body.removeChild(textArea);
}

async function copyText(text, successMsg = '已复制') {
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            toast(successMsg);
            return;
        } catch (err) {
            console.warn('navigator.clipboard 失败，尝试 fallback', err);
        }
    }
    // 回退方案
    copyTextFallback(text);
}

function formatBytes(b) {
    if (!b || b <= 0) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(b) / Math.log(1024));
    return (b / Math.pow(1024, i)).toFixed(2) + ' ' + u[i];
}

/** 界面时间统一按东八区（上海）展示，不随浏览器系统时区变化 */
const TZ_SHANGHAI = 'Asia/Shanghai';

function formatDate(ts) {
    if (!ts || ts <= 0) return '-';
    return new Date(ts * 1000).toLocaleDateString('zh-CN', { timeZone: TZ_SHANGHAI });
}

/** 将 API 返回的 ISO 时间格式化为上海墙钟时间（与浏览器/系统时区无关） */
function formatIsoTime(iso) {
    if (!iso) return '-';
    try {
        let s = String(iso).trim();
        if (s.includes(' ') && !s.includes('T')) s = s.replace(' ', 'T', 1);
        const hasTz = /(?:Z|[+-]\d{2}:\d{2}(?::\d{2})?)$/i.test(s);
        if (!hasTz) s = s + 'Z';
        return new Date(s).toLocaleString('zh-CN', { timeZone: TZ_SHANGHAI, hour12: false });
    } catch (_) { return String(iso); }
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function escHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&gt;').replace(/>/g, '&gt;');
}

/** 统一的检测结果弹窗样式 */
function showResultModal(title, content) {
    const existing = document.getElementById('resultModal');
    if (existing) existing.remove();
    
    // 把文字中的换行转换为 <br>
    const safeTitle = escHtml(title);
    const htmlContent = escHtml(content).replace(/\n/g, '<br/>');

    const html = `<div class="modal-bg" id="resultModal" onclick="if(event.target===this)this.remove()">
    <div class="card w-full max-w-md max-h-[90vh] overflow-y-auto">
      <h3 class="text-lg font-bold mb-4 border-b border-slate-700/50 pb-2">${safeTitle}</h3>
      <div class="text-sm text-slate-300 mb-6 leading-relaxed">
        ${htmlContent}
      </div>
      <div class="flex justify-end">
        <button type="button" class="btn btn-primary px-6" onclick="document.getElementById('resultModal').remove()">确定</button>
      </div>
    </div>
  </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

function initTheme() {
    const t = localStorage.getItem('ch_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', t);
    
    const iconSun = document.getElementById('iconSun');
    const iconMoon = document.getElementById('iconMoon');
    if (iconSun) iconSun.classList.toggle('hidden', t !== 'dark');
    if (iconMoon) iconMoon.classList.toggle('hidden', t === 'dark');
}

function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const next = cur === 'dark' ? 'light' : 'dark';
    localStorage.setItem('ch_theme', next);
    document.documentElement.setAttribute('data-theme', next);
    
    const iconSun = document.getElementById('iconSun');
    const iconMoon = document.getElementById('iconMoon');
    if (iconSun) iconSun.classList.toggle('hidden', next !== 'dark');
    if (iconMoon) iconMoon.classList.toggle('hidden', next === 'dark');
}

initTheme();

async function doLogin() {
    const pwd = document.getElementById('pwdInput').value;
    try {
        const r = await fetch(API + '/api/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd })
        });
        if (!r.ok) { document.getElementById('loginErr').textContent = '密码错误'; document.getElementById('loginErr').classList.remove('hidden'); return; }
        window.location.href = '/';
    } catch (e) { toast(e.message, 'error'); }
}

async function doLogout() {
    try {
        await fetch(API + '/api/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (e) { toast(e.message, 'error'); }
}

/**
 * Phase 4.3：延迟颜色条可视化
 * @param {number|null} ms - 延迟毫秒数（null 表示未知或失败）
 * @param {boolean} [compact=false] - compact 模式仅返回彩色小圆点 + 数字
 * @returns {string} HTML 片段
 */
function renderLatencyBar(ms, compact = false) {
    if (ms == null || ms < 0) {
        return compact
            ? '<span class="inline-flex items-center gap-1 text-xs text-[var(--muted)]"><span class="w-2 h-2 rounded-full bg-slate-500"></span>-</span>'
            : '<div class="latency-bar-wrap"><div class="latency-bar bg-slate-600" style="width:0%"></div><span class="latency-label text-[var(--muted)]">-</span></div>';
    }
    const rounded = Math.round(ms);
    let colorClass, barWidth;
    if (rounded <= 200) {
        colorClass = 'bg-green-500';
        barWidth = Math.min(100, (rounded / 200) * 40 + 10);
    } else if (rounded <= 500) {
        colorClass = 'bg-yellow-400';
        barWidth = Math.min(100, 40 + ((rounded - 200) / 300) * 30);
    } else {
        colorClass = 'bg-red-500';
        barWidth = Math.min(100, 70 + Math.min(30, ((rounded - 500) / 500) * 30));
    }
    const label = `${rounded} ms`;
    if (compact) {
        return `<span class="inline-flex items-center gap-1 text-xs"><span class="w-2 h-2 rounded-full ${colorClass}"></span><span class="${colorClass.replace('bg-', 'text-')}">${label}</span></span>`;
    }
    return `<div class="latency-bar-wrap flex items-center gap-2" title="${label}">
  <div class="latency-bar-track flex-1 h-1.5 rounded-full bg-slate-700 overflow-hidden" style="max-width:80px">
    <div class="h-full rounded-full ${colorClass} transition-all" style="width:${barWidth}%"></div>
  </div>
  <span class="latency-label text-xs ${colorClass.replace('bg-', 'text-')} font-mono tabular-nums">${label}</span>
</div>`;
}
