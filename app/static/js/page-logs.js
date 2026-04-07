/**
 * 订阅访问日志页
 * 依赖：core.js（api、toast、formatIsoTime、esc）
 */

window._logsPage = 1;
window._logsPageSize = 50;
window._logsTotalPages = 1;
window._logsFilter = { ip: '', date_from: '', date_to: '' };

function applyLogsFilter() {
    window._logsFilter.ip = (document.getElementById('logsIpFilter') || {}).value || '';
    window._logsFilter.date_from = (document.getElementById('logsDateFrom') || {}).value || '';
    window._logsFilter.date_to = (document.getElementById('logsDateTo') || {}).value || '';
    window._logsTotalPages = 1;
    loadSubLogs(1);
}

function resetLogsFilter() {
    const ipEl = document.getElementById('logsIpFilter');
    const fromEl = document.getElementById('logsDateFrom');
    const toEl = document.getElementById('logsDateTo');
    if (ipEl) ipEl.value = '';
    if (fromEl) fromEl.value = '';
    if (toEl) toEl.value = '';
    window._logsFilter = { ip: '', date_from: '', date_to: '' };
    window._logsTotalPages = 1;
    loadSubLogs(1);
}

async function loadSubLogs(page) {
    page = Math.max(1, page || 1);
    if (page > window._logsTotalPages && window._logsTotalPages > 0) return;
    window._logsPage = page;

    const tbody = document.getElementById('logsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-slate-400 dark:text-slate-500">加载中…</td></tr>';

    try {
        const params = new URLSearchParams({ page, page_size: window._logsPageSize });
        const f = window._logsFilter;
        if (f.ip) params.set('ip', f.ip);
        if (f.date_from) params.set('date_from', f.date_from);
        if (f.date_to) params.set('date_to', f.date_to);
        const data = await api(`/api/sub-access-logs?${params}`);
        const total = data.total || 0;
        const items = data.items || [];
        window._logsTotalPages = Math.max(1, Math.ceil(total / window._logsPageSize));

        const label = document.getElementById('logsTotalLabel');
        if (label) label.textContent = `共 ${total} 条记录`;

        const pageLabel = document.getElementById('logsPageLabel');
        if (pageLabel) pageLabel.textContent = `${page} / ${window._logsTotalPages}`;

        const prevBtn = document.getElementById('logsPrevBtn');
        const nextBtn = document.getElementById('logsNextBtn');
        if (prevBtn) prevBtn.disabled = page <= 1;
        if (nextBtn) nextBtn.disabled = page >= window._logsTotalPages;

        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-8 text-center text-slate-400 dark:text-slate-500">暂无访问记录</td></tr>';
            return;
        }

        const offset = (page - 1) * window._logsPageSize;
        tbody.innerHTML = items.map((item, idx) => {
            const num = offset + idx + 1;
            const time = formatIsoTime(item.accessed_at);
            const displayIp = item.display_ip || item.real_ip || item.ip || '-';
            const ipCell = item.real_ip
                ? `<span class="font-mono text-xs text-emerald-600 dark:text-emerald-400">${esc(displayIp)}</span>`
                : `<span class="font-mono text-xs">${esc(displayIp)}</span>`;
            const ua = item.user_agent || '';
            const uaShort = ua.length > 80 ? ua.slice(0, 80) + '…' : ua;
            const uaClient = parseClientName(ua);
            return `<tr class="border-t border-slate-100 dark:border-slate-700/60 hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <td class="px-4 py-2 text-slate-400 text-xs">${num}</td>
                <td class="px-4 py-2 whitespace-nowrap text-xs">${time}</td>
                <td class="px-4 py-2 whitespace-nowrap text-xs">${ipCell}</td>
                <td class="px-4 py-2 text-xs">
                    ${uaClient ? `<span class="inline-block px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs font-medium mr-1">${esc(uaClient)}</span>` : ''}
                    <span class="text-slate-400 dark:text-slate-500" title="${esc(ua)}">${esc(uaShort) || '—'}</span>
                </td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="px-4 py-6 text-center text-red-500">${esc(e.message)}</td></tr>`;
    }
}

/**
 * 从 User-Agent 中提取客户端名称标签，方便识别 Clash Verge / Meta / Stash 等。
 */
function parseClientName(ua) {
    if (!ua) return '';
    const lower = ua.toLowerCase();
    if (/clash[\s\-_]?verge/.test(lower)) {
        const m = ua.match(/[Cc]lash[\s\-_][Vv]erge[\/\s]*([\d.]+)/);
        return 'Clash Verge' + (m ? ' ' + m[1] : '');
    }
    if (/clashmetaforandroid/.test(lower)) {
        const m = ua.match(/ClashMetaForAndroid\/([\d.]+)/i);
        return 'Clash Meta (Android)' + (m ? ' ' + m[1] : '');
    }
    if (/clash\.meta/.test(lower) || /clashmeta/.test(lower)) {
        return 'Clash Meta';
    }
    if (/mihomo/.test(lower)) {
        const m = ua.match(/mihomo\/([\d.]+)/i);
        return 'Mihomo' + (m ? ' ' + m[1] : '');
    }
    if (/stash/.test(lower)) {
        const m = ua.match(/[Ss]tash\/([\d.]+)/);
        return 'Stash' + (m ? ' ' + m[1] : '');
    }
    if (/clash\//.test(lower) || /clash-premium/.test(lower)) {
        return 'Clash';
    }
    if (/surge/.test(lower)) return 'Surge';
    if (/quantumult/.test(lower)) return 'Quantumult';
    if (/shadowrocket/.test(lower)) return 'Shadowrocket';
    if (/loon/.test(lower)) return 'Loon';
    return '';
}

async function clearSubLogs() {
    if (!confirm('确定要清空所有访问日志吗？此操作不可恢复。')) return;
    try {
        await api('/api/sub-access-logs', { method: 'DELETE' });
        toast('日志已清空');
        await loadSubLogs(1);
    } catch (e) {
        toast('清空失败：' + e.message, 'error');
    }
}
