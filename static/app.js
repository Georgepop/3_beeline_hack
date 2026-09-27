/* Билайн Бизнес — диспетчер полевых инженеров.
   Фронт = клиент API по контракту (app/schemas.py).
   Никакой логики распределения здесь нет: только отрисовка «ответа». */

const COLORS = ['#2d9c7a', '#3498db', '#e67e22', '#9b59b6', '#16a085', '#c0392b', '#2980b9', '#8e44ad', '#d35400', '#27ae60'];
const TRANSPORT_ICON = { auto: '🚗', transit: '🚌', bike: '🚲', walk: '🚶' };
// Статусы заявок (remote) -> цвет маркера непланируемых/фоновых
const STATUS_COLORS = {
    'Отправлена': '#2d9c7a', 'В работе': '#e67e22', 'В пути': '#9b59b6',
    'Не отправлена': '#7f8c8d', 'Просрочена': '#c0392b',
    'Выполнена': '#b8d8c0', 'Отменена': '#636e72',
};
const STATUS_DEFAULT = '#b0bec5';
const DONE_STATUSES = ['Выполнена', 'Отменена'];

const state = {
    region: 'vostok',
    mode: 'improved',
    dist: 'haversine',
    source: 'csv',
    regions: [],
    plan: null,        // PlanResponse
    requests: [],      // Request[]
    engineers: [],     // Engineer[]
    map: null,
    layerGroup: null,
    lines: {},         // engineer index -> L.polyline
    rid2eng: {},       // request_id -> {engIdx, stop}
    reqMarkers: {},    // request_id -> L.marker (справочная заявка источника)
    showAllRequests: true,   // «Показывать все заявки» (все маршруты — принудительно вкл.)
    showUnassigned: false,   // «Показывать неназначенные»
    showDone: false,         // «Показывать выполненные» (Выполнена/Отменена)
    selectedEng: null,    // выбранный инженер (null = все маршруты)
    hiddenStatuses: new Set(), // статусы, скрытые в легенде
    legendList: [],       // статусы для легенды
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
        state.source = settings.data_source || 'csv';
        $('source').value = state.source;
        $('dist').value = state.dist;
        await fillSolvers();

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

// ===== Селектор алгоритмов (реестр на бэкенде: GET /api/solvers) =====
async function fillSolvers() {
    let opts = [];
    try {
        const solvers = await api('/api/solvers');
        opts = solvers.map(s => `<option value="${esc(s.name)}">${esc(s.label)}</option>`);
    } catch (e) {
        // бэкенд без /api/solvers (старый процесс) — фолбэк на известные алгоритмы
        opts = [`<option value="baseline_fifo">Базовый (FIFO)</option>`,
                `<option value="improved">Улучшенный</option>`];
    }
    $('mode').innerHTML = opts.join('');
    const chosen = String(state.mode || 'improved');
    if (!$('mode').querySelector(`option[value="${chosen}"]`)) {
        // текущий mode из настроек (например benchmark_ortools из env) — показать выбранным
        $('mode').insertAdjacentHTML('beforeend', `<option value="${esc(chosen)}">${esc(chosen)}</option>`);
    }
    $('mode').value = chosen;
    if (!opts.length) $('mode').insertAdjacentHTML('beforeend',
        `<option value="improved" disabled>Алгоритмы не загрузились</option>`);
    // OR-Tools не установлен — видим в списке, но выбрать нельзя (с подсказкой).
    $('mode').insertAdjacentHTML('beforeend',
        `<option value="benchmark_ortools" disabled title="Требуется пакет ortools (см. requirements-benchmark.txt) — не установлен, план считается improved">OR-Tools* — не установлен</option>`);
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
    state.source = $('source').value;
    try {
        await api('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ region: state.region, solver_mode: state.mode, dist_mode: state.dist, data_source: state.source }),
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
        state.selectedEng = null;
        state.hiddenStatuses = new Set();
        state.legendList = [...new Set(requests.map(r => r.status).filter(Boolean))];
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
    if (!state.plan) {
        const box = { requests: 'requestsList', engineers: 'engineersList' }[section];
        if (box && $(box)) $(box).innerHTML = '<div class="item-meta">План не загружен — нажмите «Рассчитать план»</div>';
        if (section === 'metrics') renderMetrics();
        return;
    }
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
    renderMapTools();
    renderMap();
    renderDynamicPanel();
    renderSectionMain(currentSection());
}

// ===== Инструменты карты =====
function renderMapTools() {
    const box = $('mapTools');
    if (!box) return;
    const allRoutes = state.selectedEng == null;
    const effShowAll = allRoutes || state.showAllRequests;
    const withStatus = state.requests.some(r => r.status);
    const hasDone = DONE_STATUSES.some(s => state.legendList.includes(s));
    const doneCb = (withStatus && hasDone) ? ` <span class="mt-sep"></span><label class="mt-check"><input type="checkbox" id="showDone"${state.showDone ? ' checked' : ''} onchange="toggleDone(this.checked)"> Показывать выполненные</label>` : '';
    const legend = (withStatus && effShowAll)
        ? ' <span class="mt-sep"></span><span class="mt-label">Статусы:</span>' +
          state.legendList.map((st, i) => {
              if (!state.showDone && DONE_STATUSES.includes(st)) return '';
              return `<span class="legend-chip${state.hiddenStatuses.has(st) ? ' off' : ''}" onclick="toggleStatus(${i})" style="color:${STATUS_COLORS[st] || STATUS_DEFAULT}">${esc(st)}</span>`;
          }).join('')
        : '';
    const sel = (state.selectedEng != null && state.plan && state.plan.engineers[state.selectedEng]);
    const hint = sel
        ? ` <span class="mt-hint">— выбран маршрут ${esc(sel.name)} (${sel.stops.length} заявок); клик по нему ещё раз вернёт все маршруты</span>`
        : '';
    box.innerHTML =
        `<label class="mt-check"><input type="checkbox" id="showAll"${effShowAll ? ' checked' : ''} ${allRoutes ? 'disabled' : ''} onchange="toggleAllRequests(this.checked)"> Показывать все заявки</label>` +
        ` <span class="mt-sep"></span><label class="mt-check"><input type="checkbox" id="showUn"${state.showUnassigned ? ' checked' : ''} onchange="toggleUnassigned(this.checked)"> Показывать неназначенные</label>` +
        doneCb + legend + hint;
    renderRouteSteps();
}

// ===== Маршрут выбранного инженера по шагам (под картой) =====
function renderRouteSteps() {
    const box = $('routeSteps');
    if (!box) return;
    const plan = state.plan;
    const eng = (state.selectedEng != null && plan && plan.engineers[state.selectedEng])
        ? plan.engineers[state.selectedEng] : null;
    if (!eng) {
        box.innerHTML = '<div class="route-steps-empty">Кликните на инженера в списке — здесь появится его маршрут по шагам.</div>';
        return;
    }
    const color = COLORS[state.selectedEng % COLORS.length];
    let rows = '';
    eng.stops.forEach(st => {
        rows += `<tr onclick="focusStop('${esc(st.request_id)}')">
            <td><span class="rs-badge" style="background:${color}22;color:${color};">${st.step}</span></td>
            <td><b>${esc(st.request_id)}</b></td>
            <td>${esc(st.address)}</td>
            <td>${esc(st.window[0])}–${esc(st.window[1])}</td>
            <td>${esc(st.arrival)}</td>
            <td>${esc(st.start_work)}–${esc(st.finish)}</td>
        </tr>`;
    });
    box.innerHTML =
        `<div class="route-steps-title">🚗 Маршрут: <b>${esc(eng.name)}</b> (${TRANSPORT_ICON[eng.transport] || '🚶'} ${esc(eng.transport_label)}) · ` +
        `${eng.stops.length} заявок · ${eng.km.toFixed(1)} км · ${fmtMin(eng.minutes)} · Офис → ${esc(eng.stops[0] ? eng.stops[0].address : '—')}</div>` +
        `<table class="route-table"><thead><tr><th>№</th><th>Заявка</th><th>Адрес</th><th>Окно</th><th>Прибытие</th><th>Работы</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function focusStop(id) {
    const hit = state.rid2eng[id];
    if (hit && hit.stop) state.map.flyTo([hit.stop.lat, hit.stop.lng], 15);
    else {
        const req = planRid2Req(id);
        if (req && req.lat != null) state.map.flyTo([req.lat, req.lng], 15);
    }
}

function syncMapTools(fit) {
    renderMapTools();
    renderMap(fit);
}

function toggleAllRequests(on) {
    state.showAllRequests = on;
    syncMapTools();
}

function toggleUnassigned(on) {
    state.showUnassigned = on;
    syncMapTools();
}

function toggleDone(on) {
    state.showDone = on;
    syncMapTools();
}

function toggleStatus(i) {
    const st = state.legendList[i];
    if (!st) return;
    if (state.hiddenStatuses.has(st)) state.hiddenStatuses.delete(st);
    else state.hiddenStatuses.add(st);
    syncMapTools();
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
// «Все заявки» при выбранном инженере: его маршрут и точки как раньше,
// остальные заявки — точками в цвете их инженера (неназначенные — серые).
function drawAssignmentLayer(plan, idx) {
    const asn = new Map();
    plan.engineers.forEach((eng, i) => {
        eng.stops.forEach(s => asn.set(s.request_id, { eng, idx: i, stop: s }));
    });
    const unassignedIds = new Set(plan.unassigned.map(u => u.request_id));
    const selEng = plan.engineers[idx];
    const color = COLORS[idx % COLORS.length];

    // Маршрут и точки выбранного инженера — как обычно.
    const linePts = (Array.isArray(selEng.route) && selEng.route.length > 1)
        ? selEng.route
        : [selEng.start, ...selEng.stops.map(s => [s.lat, s.lng])];
    const line = L.polyline(linePts.map(p => [p[0], p[1]]), {
        color, weight: 3, opacity: 0.85,
    }).addTo(state.layerGroup);
    line.bindTooltip(
        `Инженер ${idx + 1} · ${selEng.stops.length} заявок · ${selEng.km.toFixed(1)} км`,
        { sticky: true, direction: 'top', offset: [0, -6], className: 'route-tip' });
    state.lines[idx] = line;

    L.marker([selEng.start.lat, selEng.start.lng], { icon: officeIcon() })
        .addTo(state.layerGroup)
        .bindPopup(`<b>🏢 Офис (${esc(plan.region_name)})</b><br>${esc(selEng.start.name || '')}`);

    const shownStops = new Set();
    selEng.stops.forEach(st => {
        shownStops.add(st.request_id);
        const req = planRid2Req(st.request_id);
        L.marker([st.lat, st.lng], { icon: stopIcon(color, String(st.step)) })
            .addTo(state.layerGroup).bindPopup(
                `<b>Заявка ${esc(st.request_id)}</b><br>${esc(st.address)}<br>` +
                `Окно: <b>${esc(st.window[0])}–${esc(st.window[1])}</b><br>` +
                `Прибытие: ${esc(st.arrival)} · Работы: ${esc(st.start_work)}–${esc(st.finish)}<br>` +
                `Инженер: <b>${esc(selEng.name)}</b> (${esc(selEng.transport_label)})<br>` +
                (req ? `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}` : ''), { maxWidth: 320 });
        state.rid2eng[st.request_id] = { engIdx: idx, stop: st };
    });

    // Остальные заявки: назначенные другим инженерам — с нумерацией их маршрута,
    // неназначенные — серые, вне плана — цвет по статусу.
    state.requests.forEach(req => {
        if (req.lat == null || shownStops.has(req.id)) return;
        if (state.hiddenStatuses.has(req.status)) return;
        if (!state.showDone && DONE_STATUSES.includes(req.status)) return;
        const isUn = unassignedIds.has(req.id);
        if (isUn && !state.showUnassigned) return;
        const hit = asn.get(req.id);
        let icon, m;
        if (hit) {
            const c = COLORS[hit.idx % COLORS.length];
            icon = stopIcon(c, String(hit.stop.step));
            m = L.marker([req.lat, req.lng], { icon })
                .addTo(state.layerGroup)
                .bindPopup(
                    `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>` +
                    `Окно: <b>${esc(req.window_start)}–${esc(req.window_end)}</b><br>` +
                    `Инженер: <b>${esc(hit.eng.name)}</b> (${esc(hit.eng.transport_label)})<br>` +
                    (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                    `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}`,
                    { maxWidth: 320 });
        } else if (isUn) {
            icon = statusIcon('#95a5a6');
            m = L.marker([req.lat, req.lng], { icon })
                .addTo(state.layerGroup)
                .bindPopup(
                    `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>❌ <b>Не назначена</b><br>` +
                    `Окно: <b>${esc(req.window_start)}–${esc(req.window_end)}</b><br>` +
                    (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                    `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}`,
                    { maxWidth: 320 });
        } else {
            const c = STATUS_COLORS[req.status] || STATUS_DEFAULT;
            icon = statusIcon(c);
            m = L.marker([req.lat, req.lng], { icon })
                .addTo(state.layerGroup)
                .bindPopup(
                    `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>` +
                    (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                    (req.control_brigade ? `Контроль: ${esc(req.control_brigade)}<br>` : '') +
                    (req.gigabit ? 'Гигабитное подключение' : ''),
                    { maxWidth: 320 });
        }
        state.reqMarkers[req.id] = m;
    });
}

// renderMap(fit): fit=false — перерисовка без перецентровки (переключение чекбоксов).
function renderMap(fit) {
    if (!state.map || !state.layerGroup) return;
    state.layerGroup.clearLayers();
    state.lines = {};
    state.rid2eng = {};
    state.reqMarkers = {};
    const plan = state.plan;
    const singleIdx = (state.selectedEng != null && state.selectedEng < plan.engineers.length)
        ? state.selectedEng : null;
    const allRoutes = singleIdx == null;
    const effShowAll = allRoutes || state.showAllRequests;

    if (singleIdx != null && state.showAllRequests) {
        drawAssignmentLayer(plan, singleIdx);
        return;
    }

    const allBounds = [];
    const displayed = new Set();

    plan.engineers.forEach((eng, idx) => {
        if (singleIdx != null && idx !== singleIdx) return;
        const color = COLORS[idx % COLORS.length];
        // Дороги из OSRM (eng.route) или прямая линия как раньше.
        const linePts = (Array.isArray(eng.route) && eng.route.length > 1)
            ? eng.route
            : [eng.start, ...eng.stops.map(s => [s.lat, s.lng])];
        const highlighted = singleIdx == null && state.selectedEng === idx;
        const line = L.polyline(linePts.map(p => [p[0], p[1]]), {
            color, weight: highlighted ? 6 : 3, opacity: 0.85,
        }).addTo(state.layerGroup);
        line.on('click', () => focusEngineer(idx));
        line.bindTooltip(
            `Инженер ${idx + 1} · ${eng.stops.length} заявок · ${eng.km.toFixed(1)} км`,
            { sticky: true, direction: 'top', offset: [0, -6], className: 'route-tip' });
        state.lines[idx] = line;
        allBounds.push(...line.getLatLngs());

        L.marker([eng.start.lat, eng.start.lng], { icon: officeIcon() })
            .addTo(state.layerGroup)
            .bindPopup(`<b>🏢 Офис (${esc(plan.region_name)})</b><br>${esc(eng.start.name || '')}`);

        eng.stops.forEach(st => {
            displayed.add(st.request_id);
            const req = planRid2Req(st.request_id);
            L.marker([st.lat, st.lng], { icon: stopIcon(color, String(st.step)) })
                .addTo(state.layerGroup).bindPopup(
                    `<b>Заявка ${esc(st.request_id)}</b><br>${esc(st.address)}<br>` +
                    `Окно: <b>${esc(st.window[0])}–${esc(st.window[1])}</b><br>` +
                    `Прибытие: ${esc(st.arrival)} · Работы: ${esc(st.start_work)}–${esc(st.finish)}<br>` +
                    `Инженер: <b>${esc(eng.name)}</b> (${esc(eng.transport_label)})<br>` +
                    (req ? `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}` : ''), { maxWidth: 320 });
            state.rid2eng[st.request_id] = { engIdx: idx, stop: st };
        });
    });

    // Неназначенные (чекбокс «Показывать неназначенные»)
    const unassignedIds = new Set();
    plan.unassigned.forEach(u => { unassignedIds.add(u.request_id); });
    if (state.showUnassigned) {
        plan.unassigned.forEach(u => {
            const req = planRid2Req(u.request_id);
            if (!req || req.lat == null) return;
            displayed.add(u.request_id);
            L.marker([req.lat, req.lng], { icon: unassignedIcon() })
                .addTo(state.layerGroup)
                .bindPopup(`<b>Заявка ${esc(u.request_id)}</b><br>${esc(u.address)}<br>❌ <b>Не назначена</b><br>Причина: ${esc(u.reason)}`);
        });
    }

    // «Показывать все заявки»: все заявки источника; показанные планом не дублируем.
    if (effShowAll) {
        state.requests.forEach(req => {
            if (displayed.has(req.id) || req.lat == null) return;
            if (!state.showUnassigned && unassignedIds.has(req.id)) return;
            if (state.hiddenStatuses.has(req.status)) return;
            if (!state.showDone && DONE_STATUSES.includes(req.status)) return;
            const color = STATUS_COLORS[req.status] || STATUS_DEFAULT;
            const m = L.marker([req.lat, req.lng], { icon: statusIcon(color) })
                .addTo(state.layerGroup)
                .bindPopup(
                    `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>Статус: <b>${esc(req.status || '—')}</b>` +
                    (req.control_brigade ? `<br>Контроль: ${esc(req.control_brigade)}` : '') +
                    (req.gigabit ? '<br>Гигабитное подключение' : ''), { maxWidth: 320 });
            state.reqMarkers[req.id] = m;
        });
    }

    if (fit && singleIdx != null && state.lines[singleIdx]) {
        state.map.fitBounds(state.lines[singleIdx].getBounds().pad(0.25));
    } else if (fit && allBounds.length) {
        state.map.fitBounds(L.latLngBounds(allBounds).pad(0.15));
    }
}

function renderRoutesPanel() {
    const plan = state.plan;
    if (!plan || !plan.engineers.length) return '<div class="panel-title">Маршруты</div><div class="item-meta">План пуст</div>';
    let html = `<div class="panel-header"><div class="panel-title">📋 Маршруты</div><div class="item-meta">${plan.metrics.assigned_count} заявок · ${plan.metrics.engineers_used} инж.</div></div><div class="items-list">`;
    plan.engineers.forEach((eng, idx) => {
        const color = COLORS[idx % COLORS.length];
        html += `
        <div class="item-card${state.selectedEng === idx ? ' selected' : ''}" onclick="focusEngineer(${idx})">
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
    const plan = state.plan;
    if (!plan || idx == null || idx >= plan.engineers.length) return;
    // Повторный клик по выбранному инженеру — показать все маршруты.
    state.selectedEng = (state.selectedEng === idx) ? null : idx;
    // При выборе инженера показываем только его заявки (снимаем «Показывать все заявки»).
    if (state.selectedEng != null) state.showAllRequests = false;
    renderMapTools();
    renderMap();
    renderDynamicPanel();
    renderSectionMain(currentSection());
    if (state.selectedEng != null && state.lines[idx]) {
        state.map.fitBounds(state.lines[idx].getBounds().pad(0.2));
    }
}

// Переход на карту маршрутов и показ маршрута инженера (клик из вкладки «Инженеры»)
function showEngineerOnMap(idx) {
    focusEngineer(idx);
    switchSection('routes', null);
}

// Показать заявку на карте: назначенную — её маршрут; остальные — точку-статус.
function showRequestOnMap(id, ev) {
    if (ev) ev.stopPropagation();
    switchSection('routes', null);
    const hit = state.rid2eng[id];
    if (hit) { focusEngineer(hit.engIdx); return; }
    if (state.plan && state.plan.unassigned.some(u => u.request_id === id) && !state.showUnassigned) {
        state.showUnassigned = true;
        renderMapTools();
        renderMap();
    }
    if (!state.showAllRequests) {
        state.showAllRequests = true;
        renderMapTools();
        renderMap();
    }
    const req = planRid2Req(id);
    if (req && req.lat != null) {
        const m = state.reqMarkers[id];
        if (m) state.map.flyTo(m.getLatLng(), 15);
        else state.map.flyTo([req.lat, req.lng], 15);
    }
}

// ===== Заявки =====
function filterRequests() {
    if ($('requestsList')) $('requestsList').innerHTML = renderRequests();
}

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
                <button class="map-btn" onclick="showRequestOnMap('${esc(s.request_id)}', event)">Карта</button>
            </div>
            <div class="item-meta">
                <span>👤 ${esc(s.eng.name)}</span>
                <span>🕐 старт ${esc(s.start_work)}</span>
                ${req ? `<span>${esc(req.skill_label)} · ${esc(req.bk_type)}</span>` : ''}
                ${req && req.status ? `<span>${statusChip(req.status)}</span>` : ''}
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
                <button class="map-btn" onclick="showRequestOnMap('${esc(u.request_id)}', event)">Карта</button>
            </div>
            <div class="item-meta">
                <span>⚠ ${esc(u.reason)}</span>
                ${req ? `<span>${esc(req.skill_label)}</span>` : ''}
                ${req && req.status ? `<span>${statusChip(req.status)}</span>` : ''}
                ${req && req.control_brigade ? `<span>Контроль: ${esc(req.control_brigade)}</span>` : ''}
            </div>
        </div>`;
    });

    // Справочно: все заявки источника (статусы/контроль), не вошедшие в план.
    const inPlan = new Set([...assigned.map(s => s.request_id), ...unassigned.map(u => u.request_id)]);
    const rest = state.requests.filter(r => !inPlan.has(r.id) && pass(r, q));
    if (rest.length) {
        html += '<div class="group-title">Справочно (источник): ' + rest.length + '</div>';
        rest.forEach(r => {
            html += `
            <div class="item-card">
                <div class="item-header">
                    <div class="item-icon" style="background:#eef2f5;color:#90a4ae;">·</div>
                    <div class="item-title">${esc(r.id)} — ${esc(r.address)}</div>
                    <div class="status status-pending">${statusChip(r.status)}</div>
                    <button class="map-btn" onclick="showRequestOnMap('${esc(r.id)}', event)">Карта</button>
                </div>
                <div class="item-meta">
                    <span>${esc(r.skill_label)} ${esc(r.bk_type)}</span>
                    <span>Окно ${esc(r.window_start)}–${esc(r.window_end)}</span>
                    ${r.control_brigade ? `<span>Контроль: ${esc(r.control_brigade)}</span>` : ''}
                </div>
            </div>`;
        });
    }
    return html;
}

function statusChip(status) {
    const color = STATUS_COLORS[status] || STATUS_DEFAULT;
    return `<span class="status-chip" style="color:${color};border-color:${color}55;background:${color}18;">${esc(status)}</span>`;
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
        <div class="item-card${inPlan && state.selectedEng === idx ? ' selected' : ''}" ${inPlan ? `onclick="showEngineerOnMap(${idx})"` : ''}>
            <div class="item-header">
                <div class="item-icon" style="background:${inPlan ? '#e8f5f0' : '#f0f4f8'};color:${inPlan ? '#2d9c7a' : '#95a5a6'};">${inPlan ? '✓' : '—'}</div>
                <div class="item-title">${esc(e.name)}</div>
                <div class="status ${inPlan ? 'status-done' : 'status-pending'}">${inPlan ? 'в рейсе' : 'свободен'}</div>
            </div>
            <div class="item-meta">
                <span>${TRANSPORT_ICON[e.transport] || '🚶'} ${esc(e.transport_label)}</span>
                ${e.speed_kph ? `<span>⏩ ${e.speed_kph} км/ч</span>` : ''}
                <span>🕐 ${esc(e.shift_start)}–${esc(e.shift_end)}</span>
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
    if (!p) {
        $('metricsCompare').innerHTML = '<div class="item-meta">План не загружен — нажмите «Рассчитать план»</div>';
        return;
    }
    const c = p.comparison || {};
    const modeLabel = { baseline_fifo: 'Базовый (FIFO)', improved: 'Улучшенный' }[state.mode] || state.mode;
    let html = '<div class="metrics-compare">';
    html += metricsTable('Наш план (' + modeLabel + ')', p.metrics, true);
    html += controlTable('Контрольное распределение* (бригады)', c.control);
    html += '</div>';
    $('metricsCompare').innerHTML = html;
}

function controlTable(title, c) {
    if (!c) return `<div class="metric-table"><h3>${esc(title)}</h3><div class="item-meta">не считалось</div></div>`;
    return `
    <div class="metric-table">
        <h3>${esc(title)}</h3>
        <table>
            ${metricRow('Назначено заявок', `${c.assigned_count ?? '—'} / ${c.total ?? c.assigned_count ?? '—'}`, false)}
            ${metricRow('Бригад в работе', c.engineers_used ?? '—', false)}
        </table>
    </div>`;
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
function statusIcon(color) {
    return L.divIcon({
        className: '',
        html: `<div class="status-ico" style="border-color:${color};color:${color};">◉</div>`,
        iconSize: [18, 18], iconAnchor: [9, 9], popupAnchor: [0, -10],
    });
}

init();