/* Билайн Бизнес — диспетчер полевых инженеров.
   Фронт = клиент API по контракту (app/schemas.py).
   Никакой логики распределения здесь нет: только отрисовка «ответа». */

const COLORS = ['#2d9c7a', '#3498db', '#e67e22', '#9b59b6', '#16a085', '#c0392b', '#2980b9', '#8e44ad', '#d35400', '#27ae60'];
const TRANSPORT_ICON = { auto: '🚗', transit: '🚌', bike: '🚲', walk: '🚶' };

const state = {
    region: 'vostok',
    mode: 'improved',
    dist: 'haversine',
    regions: [],
    plan: null,        // PlanResponse
    requests: [],      // Request[]
    engineers: [],     // Engineer[]
    map: null,
    layerGroup: null,
    lines: {},         // engineer index -> L.polyline
    rid2eng: {},       // request_id -> {engIdx, stop}
    lastScenario: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    if (!r.ok) {
        let msg = r.statusText;
        try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
    }
    return r.json();
}

// ===== Вспомогательные =====
function fmtMin(min) {
    if (min == null) return '—';
    return `${Math.floor(min / 60)}ч ${min % 60}м`;
}
function toast(msg, type = 'success') {
    const t = $('toast');
    t.textContent = msg;
    t.className = 'toast show ' + type;
    clearTimeout(t._h);
    t._h = setTimeout(() => { t.className = 'toast'; }, 3000);
}

// ===== Инициализация =====
async function init() {
    try {
        const settings = await api('/api/settings');
        state.region = settings.region;
        state.mode = settings.solver_mode;
        state.dist = settings.dist_mode;
        $('mode').value = state.mode;
        $('dist').value = state.dist;

        state.regions = await api('/api/regions');
        const sel = $('region');
        sel.innerHTML = state.regions.map(r => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
        sel.value = state.region;

        initMap();
        await loadPlan();
        showMapHint('📡 План построен. Кликните на точку для деталей');
    } catch (e) {
        toast('Ошибка загрузки: ' + e.message, 'error');
    }
}

function initMap() {
    if (state.map) return;
    state.map = L.map('mainMap').setView([55.68, 37.70], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18,
        attribution: '© OpenStreetMap',
    }).addTo(state.map);
    state.layerGroup = L.layerGroup().addTo(state.map);
}

function showMapHint(text) {
    const h = $('mapHint');
    h.textContent = text;
    h.classList.add('show');
    clearTimeout(h._t);
    h._t = setTimeout(() => h.classList.remove('show'), 3500);
}

// ===== Настройки / пересчёт =====
async function onRegionChange() {
    await applySettings();
}
async function onSettingsChange() {
    await applySettings();
}
async function applySettings() {
    state.region = $('region').value;
    state.mode = $('mode').value;
    state.dist = $('dist').value;
    try {
        await api('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ region: state.region, solver_mode: state.mode, dist_mode: state.dist }),
        });
        await loadPlan();
    } catch (e) {
        toast('Ошибка настроек: ' + e.message, 'error');
    }
}

async function loadPlan() {
    try {
        const [plan, requests, engineers] = await Promise.all([
            api(`/api/plan?region=${state.region}&mode=${state.mode}&dist=${state.dist}`),
            api(`/api/requests?region=${state.region}`),
            api(`/api/engineers?region=${state.region}`),
        ]);
        state.plan = plan;
        state.requests = requests;
        state.engineers = engineers;
        state.lastScenario = null;
        renderAll();
    } catch (e) {
        toast('Ошибка плана: ' + e.message, 'error');
    }
}

// ===== Секции =====
function switchSection(section, el) {
    document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
    if (el) el.classList.add('active');
    document.querySelectorAll('.section-content').forEach(s => s.classList.remove('active'));
    $('section-' + section).classList.add('active');
    $('pageTitle').textContent = { routes: 'Маршруты', requests: 'Заявки', engineers: 'Инженеры', metrics: 'Метрики и сравнение', scenarios: 'Сценарии перепланирования' }[section];
    renderDynamicPanel();
    if (section === 'routes' && state.map) setTimeout(() => state.map.invalidateSize(), 80);
    renderSectionMain(section);
}

function renderSectionMain(section) {
    if (!state.plan) return;
    if (section === 'requests') $('requestsList').innerHTML = renderRequests();
    if (section === 'engineers') $('engineersList').innerHTML = renderEngineers();
    if (section === 'metrics') renderMetrics();
    if (section === 'scenarios') renderScenarios();
}

function currentSection() {
    const act = document.querySelector('.section-content.active');
    return act ? act.id.replace('section-', '') : 'routes';
}

function renderDynamicPanel() {
    const p = $('dynamicPanel');
    if (currentSection() === 'routes') {
        p.innerHTML = renderRoutesPanel();
    } else {
        p.innerHTML = '';
    }
}

function renderAll() {
    if (!state.plan) return;
    renderStats();
    renderMap();
    renderDynamicPanel();
    renderSectionMain(currentSection());
}

// ===== Статистика =====
function renderStats() {
    const m = state.plan.metrics;
    $('statAssigned').textContent = m.assigned_count + ' / ' + m.total_requests;
    $('statEngineers').textContent = m.engineers_used + ' шт';
    $('statKm').textContent = m.total_km.toFixed(1);
    $('statMinutes').textContent = fmtMin(m.total_minutes);
    $('statUnassigned').textContent = m.unassigned_count;
}

// ===== Карта =====
function renderMap() {
    if (!state.map || !state.layerGroup) return;
    state.layerGroup.clearLayers();
    state.lines = {};
    state.rid2eng = {};
    const plan = state.plan;

    const allBounds = [];
    plan.engineers.forEach((eng, idx) => {
        const color = COLORS[idx % COLORS.length];
        const pts = [eng.start, ...eng.stops.map(s => ({ lat: s.lat, lng: s.lng }))];
        const line = L.polyline(pts.map(p => [p.lat, p.lng]), {
            color, weight: 3, opacity: 0.85, dashArray: null,
        }).addTo(state.layerGroup);
        state.lines[idx] = line;
        allBounds.push(...line.getLatLngs());

        const stopPts = [eng.start, ...eng.stops];
        stopPts.forEach((p, i) => {
            if (i === 0) {
                L.marker([p.lat, p.lng], { icon: officeIcon() })
                    .addTo(state.layerGroup)
                    .bindPopup(`<b>🏢 Офис (старт)</b><br>${esc(p.name || '')}`);
                return;
            }
            const st = eng.stops[i - 1];
            const req = planRid2Req(st.request_id);
            L.marker([st.lat, st.lng], {
                icon: stopIcon(color, String(st.step)),
            }).addTo(state.layerGroup).bindPopup(
                `<b>Заявка ${esc(st.request_id)}</b><br>${esc(st.address)}<br>` +
                `Окно: <b>${esc(st.window[0])}–${esc(st.window[1])}</b><br>` +
                `Прибытие: ${esc(st.arrival)} · Работы: ${esc(st.start_work)}–${esc(st.finish)}<br>` +
                `Инженер: <b>${esc(eng.name)}</b> (${esc(eng.transport_label)})<br>` +
                (req ? `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}` : ''), { maxWidth: 320 });
            state.rid2eng[st.request_id] = { engIdx: idx, stop: st };
        });
    });

    // Неназначенные
    plan.unassigned.forEach(u => {
        const req = planRid2Req(u.request_id);
        if (!req || req.lat == null) return;
        L.marker([req.lat, req.lng], { icon: unassignedIcon() })
            .addTo(state.layerGroup)
            .bindPopup(`<b>Заявка ${esc(u.request_id)}</b><br>${esc(u.address)}<br>❌ <b>Не назначена</b><br>Причина: ${esc(u.reason)}`);
    });

    if (allBounds.length) state.map.fitBounds(L.latLngBounds(allBounds).pad(0.15));
}

function renderRoutesPanel() {
    const plan = state.plan;
    if (!plan || !plan.engineers.length) return '<div class="panel-title">Маршруты</div><div class="item-meta">План пуст</div>';
    let html = '<div class="panel-header"><div class="panel-title">📋 Маршруты</div></div><div class="items-list">';
    plan.engineers.forEach((eng, idx) => {
        const color = COLORS[idx % COLORS.length];
        html += `
        <div class="item-card" onclick="focusEngineer(${idx})">
            <div class="item-header">
                <div class="item-icon" style="background:${color}22;color:${color};">${idx + 1}</div>
                <div class="item-title">${esc(eng.name)}</div>
            </div>
            <div class="item-meta">
                <span>${TRANSPORT_ICON[eng.transport] || '🚶'} ${esc(eng.transport_label)}</span>
                <span>📍 ${eng.stops.length} заявок</span>
                <span>📏 ${eng.km.toFixed(1)} км</span>
                <span>⏱ ${fmtMin(eng.minutes)}</span>
            </div>
        </div>`;
    });
    html += '</div>';
    return html;
}

function focusEngineer(idx) {
    const line = state.lines[idx];
    if (!line) return;
    Object.entries(state.lines).forEach(([i, l]) => {
        l.setStyle(i == idx ? { weight: 7, opacity: 1 } : { weight: 2, opacity: 0.45 });
    });
    state.map.fitBounds(line.getBounds().pad(0.2));
}

// ===== Заявки =====
function renderRequests() {
    const plan = state.plan;
    if (!plan) return '<div class="item-meta">План не загружен</div>';
    const q = ($('reqSearch')?.value || '').toLowerCase().trim();
    const assigned = plan.engineers.flatMap(e => e.stops.map(s => ({ ...s, eng: e })));
    const unassigned = plan.unassigned;

    const pass = (req, key) => !q || req.id.toLowerCase().includes(q) || req.address.toLowerCase().includes(q);

    let html = '<div class="group-title">Назначенные (' + assigned.length + ')</div>';
    const byId = new Map(state.requests.map(r => [r.id, r]));
    const assignedRows = [];
    assigned.forEach(s => { if (pass({ id: s.request_id, address: s.address }, q)) assignedRows.push(s); });
    assignedRows.forEach(s => {
        const req = byId.get(s.request_id);
        html += `
        <div class="item-card">
            <div class="item-header">
                <div class="item-icon" style="background:#e8f5f0;color:#2d9c7a;">✓</div>
                <div class="item-title">${esc(s.request_id)} — ${esc(s.address)}</div>
                <div class="status status-done">${esc(s.window[0])}–${esc(s.window[1])}</div>
            </div>
            <div class="item-meta">
                <span>👤 ${esc(s.eng.name)}</span>
                <span>🕐 старт ${esc(s.start_work)}</span>
                ${req ? `<span>${esc(req.skill_label)} · ${esc(req.bk_type)}</span>` : ''}
            </div>
        </div>`;
    });
    html += '<div class="group-title">Неназначенные (' + unassigned.length + ')</div>';
    unassigned.forEach(u => {
        const req = byId.get(u.request_id);
        if (!pass(u, q)) return;
        html += `
        <div class="item-card">
            <div class="item-header">
                <div class="item-icon" style="background:#fde8e8;color:#e74c3c;">✕</div>
                <div class="item-title">${esc(u.request_id)} — ${esc(u.address)}</div>
                <div class="status status-error">не назначена</div>
            </div>
            <div class="item-meta">
                <span>⚠ ${esc(u.reason)}</span>
                ${req ? `<span>${esc(req.skill_label)}</span>` : ''}
            </div>
        </div>`;
    });
    return html;
}

// ===== Инженеры =====
function renderEngineers() {
    const plan = state.plan;
    if (!plan) return '<div class="item-meta">План не загружен</div>';
    const byIdx = {}; // инженеры, участвующие в плане
    plan.engineers.forEach((e, i) => byIdx[e.id] = i);

    let html = '<div class="group-title">Инженеры региона (' + state.engineers.length + ')</div>';
    state.engineers.forEach(e => {
        const idx = byIdx[e.id];
        const inPlan = idx !== undefined;
        const stops = inPlan ? plan.engineers[idx].stops : [];
        const km = inPlan ? plan.engineers[idx].km : 0;
        html += `
        <div class="item-card">
            <div class="item-header">
                <div class="item-icon" style="background:${inPlan ? '#e8f5f0' : '#f0f4f8'};color:${inPlan ? '#2d9c7a' : '#95a5a6'};">${inPlan ? '✓' : '—'}</div>
                <div class="item-title">${esc(e.name)}</div>
                <div class="status ${inPlan ? 'status-done' : 'status-pending'}">${inPlan ? 'в рейсе' : 'свободен'}</div>
            </div>
            <div class="item-meta">
                <span>${TRANSPORT_ICON[e.transport] || '🚶'} ${esc(e.transport_label)}</span>
                <span>🛠 ${esc(e.skills_label.join(', '))}</span>
                ${inPlan ? `<span>📍 ${stops.length} заявок</span><span>📏 ${km.toFixed(1)} км</span>` : ''}
            </div>
        </div>`;
    });
    return html;
}

// ===== Метрики =====
function metricRow(label, val, hl) {
    return `<tr${hl ? ' class="hl"' : ''}><td>${esc(label)}</td><td>${val}</td></tr>`;
}
function metricsTable(title, m, hl) {
    if (!m) return `<div class="metric-table"><h3>${esc(title)}</h3><div class="item-meta">не считалось</div></div>`;
    return `
    <div class="metric-table">
        <h3>${esc(title)}</h3>
        <table>
            ${metricRow('Назначено заявок', `${m.assigned_count} / ${m.total_requests}`, hl)}
            ${metricRow('Неназначено', m.unassigned_count, hl)}
            ${metricRow('Инженеров в рейсе', m.engineers_used, hl)}
            ${metricRow('Общий пробег, км', m.total_km.toFixed(1), hl)}
            ${metricRow('Время работ, мин', fmtMin(m.total_minutes), hl)}
        </table>
    </div>`;
}
function renderMetrics() {
    const p = state.plan;
    const c = p.comparison || {};
    const def = p.metrics;
    let html = '<div class="metrics-compare">';
    html += metricsTable('Улучшенный план (текущий)', def, true);
    html += metricsTable('Базовый план (FIFO)', c.baseline, false);
    html += metricsTable('Контрольное распределение*', c.control ? { ...def, total_km: c.control.km ?? def.total_km } : null, false);
    html += '</div>';
    $('metricsCompare').innerHTML = html;
}

// ===== Сценарии =====
function renderScenarios() {
    const plan = state.plan;
    if (!plan) return;
    // отмена заявки — назначенные
    const assigned = plan.engineers.flatMap(e => e.stops.map(s => s.request_id));
    $('cancelReq').innerHTML = assigned.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('') || '<option>нет назначенных</option>';
    // недоступен инженер — используемые
    $('unavailEng').innerHTML = plan.engineers.map(e => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('') || '<option>нет инженеров</option>';
}

async function runScenario(type) {
    const body = { type };
    if (type === 'cancel') body.request_id = $('cancelReq').value;
    if (type === 'unavailable') body.engineer_id = $('unavailEng').value;
    try {
        const res = await api(`/api/scenario?region=${state.region}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        state.plan = res.plan;
        state.lastScenario = res;
        renderAll();
        renderScenarios();
        toast('Сценарий: ' + res.event, 'success');

        let html = '<div class="panel-header"><div class="panel-title">Результат перепланирования</div></div>';
        html += '<div class="diff-row">🔁 ' + esc(res.event) + '</div>';
        if (!res.diff.length) html += '<div class="diff-row">План не изменился</div>';
        res.diff.forEach(d => {
            if (d.kind === 'assigned') html += `<div class="diff-row">➕ <b>${esc(d.request_id)}</b> назначена → ${esc(d.to)}</div>`;
            else if (d.kind === 'unassigned') html += `<div class="diff-row">➖ <b>${esc(d.request_id)}</b> больше не назначена (была ${esc(d.from)})</div>`;
            else html += `<div class="diff-row">↔ <b>${esc(d.request_id)}</b>: ${esc(d.from)} → ${esc(d.to)}</div>`;
        });
        $('scenarioResult').innerHTML = html;
        switchSection('scenarios', null);
    } catch (e) {
        toast('Ошибка сценария: ' + e.message, 'error');
    }
}

// ===== Хелперы карты =====
function planRid2Req(reqId) {
    return state.requests.find(r => r.id === reqId);
}
function stopIcon(color, num) {
    return L.divIcon({
        className: '',
        html: `<div class="stop-ico" style="border-color:${color};color:${color};">${esc(num)}</div>`,
        iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -13],
    });
}
function officeIcon() {
    return L.divIcon({ className: '', html: '<div class="office-ico">🏢</div>', iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -16] });
}
function unassignedIcon() {
    return L.divIcon({ className: '', html: '<div class="un-ico">✕</div>', iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -13] });
}

init();