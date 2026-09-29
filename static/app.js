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
const MAP_STYLE = {
    version: 8,
    sources: {
        osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '&copy; OpenStreetMap'
        }
    },
    layers: [
        { id: 'osm', type: 'raster', source: 'osm' }
    ]
};

const state = {
    region: 'vostok',
    mode: 'improved',
    dist: 'haversine',
    regions: [],
    plan: null,        // PlanResponse
    requests: [],      // Request[]
    engineers: [],     // Engineer[]
    map: null,
    popup: null,       // maplibre popup (клик по точке)
    tip: null,         // maplibre popup (тултип маршрута на наведении)
    markers: [],       // DOM-маркеры (maplibregl.Marker)
    lineCoords: {},    // engineer index -> [[lng,lat],...]
    rid2eng: {},       // request_id -> {engIdx, stop}
    reqPoints: {},     // request_id -> [lng, lat]
    showAllRequests: true,   // «Показывать все заявки» (все маршруты — принудительно вкл.)
    showUnassigned: false,   // «Показывать неназначенные»
    showDone: false,         // «Показывать выполненные» (Выполнена/Отменена)
    selectedEng: null,    // выбранный инженер (null = все маршруты)
    hiddenStatuses: new Set(), // статусы, скрытые в легенде
    legendList: [],       // статусы для легенды
    lastScenario: null,
    // Расчёт плана: пока /api/plan в полёте, показываем индикатор и гасим
    // кнопку «Обновить». Без этого повторный клик во время долгого расчёта
    // OR-Tools (до 5 с) запускал бы второй запрос вхолостую.
    planLoading: false,
    planAbort: null,     // AbortController предыдущего запроса
    planStartedAt: 0,
    planTimer: null,
    planReqId: 0,        // номер текущего запроса — защита от устаревших ответов
    // Правка данных в БД.
    dbStatus: null,      // GET /api/db/status — сколько изменилось против импорта
    placing: false,      // режим «клик по карте, чтобы поставить заявку»
    editReqId: null,     // id редактируемой заявки (null = создание новой)
    editReqCoords: null, // координаты, заданные кликом по карте
    editEngId: null,     // id редактируемого инженера
    // Геометрия по дорогам приезжает отдельным запросом уже после плана.
    geometryKey: null,   // 'region|mode|dist' — для отбрасывания устаревшего ответа
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

// ===== Индикатор расчёта =====
// Пока считается, страница не должна выглядеть зависшей: показываем, что идёт
// работа и сколько она уже длится. Для OR-Tools это честно — там реально идёт
// поиск, у него есть потолок времени, и ускорить можно только честной надписью.
const PLAN_LOADER_MESSAGES = {
    benchmark_ortools: 'OR-Tools ищет решение — до 5 с',
};

function planLoadingText() {
    const base = PLAN_LOADER_MESSAGES[state.mode] || 'Считаем план…';
    const sec = Math.round((Date.now() - state.planStartedAt) / 1000);
    return sec > 0 ? `${base} (${sec} с)` : base;
}

function startPlanLoading() {
    state.planLoading = true;
    state.planStartedAt = Date.now();
    ['region', 'mode'].forEach(id => { if ($(id)) $(id).disabled = true; });
    if ($('btnRefresh')) $('btnRefresh').disabled = true;
    if ($('planLoader')) $('planLoader').hidden = false;
    clearInterval(state.planTimer);
    state.planTimer = setInterval(() => {
        const el = $('planLoaderText');
        if (el) el.textContent = planLoadingText();
    }, 200);
}

function stopPlanLoading() {
    state.planLoading = false;
    clearInterval(state.planTimer);
    state.planTimer = null;
    ['region', 'mode'].forEach(id => { if ($(id)) $(id).disabled = false; });
    if ($('btnRefresh')) $('btnRefresh').disabled = false;
    if ($('planLoader')) $('planLoader').hidden = true;
}

// Счётчик запросов: пока считался план для прежнего региона, счётчик уже другой,
// и такой ответ надо выбросить, а не подставить в state.plan.
function nextPlanReqId() {
    state.planReqId = (state.planReqId || 0) + 1;
    return state.planReqId;
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
    state.map = new maplibregl.Map({
        container: 'mainMap',
        style: MAP_STYLE,
        center: [37.70, 55.68],
        zoom: 11,
        attributionControl: false,
    });
    state.map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    state.map.addControl(new maplibregl.AttributionControl({ compact: true }));
    state.popup = new maplibregl.Popup({ closeButton: false, maxWidth: '340px', offset: 12 });
    state.tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'ml-tip' });
    state.map.on('load', () => {
        state.map.addSource('routes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        state.map.addLayer({
            id: 'route-lines', type: 'line', source: 'routes',
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'w'], 'line-opacity': 0.85 },
        });
        state.map.on('click', 'route-lines', (e) => {
            if (state.placing) return;   // в режиме постановки клик принадлежит карте, не линии
            const p = e.features[0].properties;
            if (p && p.engIdx != null) focusEngineer(p.engIdx);
        });
        state.map.on('mousemove', 'route-lines', (e) => {
            state.map.getCanvas().style.cursor = 'pointer';
            const p = e.features[0].properties;
            if (p && p.tip) {
                if (state.tip._html !== p.tip) { state.tip.setHTML(p.tip); state.tip._html = p.tip; }
                state.tip.setLngLat(e.lngLat);
                if (!state.tipShown) { state.tip.addTo(state.map); state.tipShown = true; }
            }
        });
        state.map.on('mouseleave', 'route-lines', () => {
            state.map.getCanvas().style.cursor = '';
            state.tip.remove();
            state.tipShown = false;
        });
        // Клик по карте в режиме «поставить заявку». Вешаем на канвас, а не на
        // слой маршрутов: клик должен ловиться в любом месте, включая поверх
        // точек и линий.
        state.map.on('click', (e) => {
            if (state.placing) placeRequestAt(e.lngLat);
        });
        if (state.plan) renderMap();
    });
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
    let ortools = null;   // серверный ответ по benchmark_ortools
    try {
        const solvers = await api('/api/solvers');
        ortools = solvers.find(s => s.name === 'benchmark_ortools') || null;
        opts = solvers.map(s => {
            const hint = s.enabled === false ? ' title="Требуется пакет ortools (см. requirements-benchmark.txt) — не установлен"' : '';
            return `<option value="${esc(s.name)}"${s.enabled === false ? ' disabled' : ''}${hint}>${esc(s.label)}${s.enabled === false ? ' — не установлен' : ''}</option>`;
        });
    } catch (e) {
        // бэкенд без /api/solvers (старый процесс) — фолбэк на известные алгоритмы
        opts = [`<option value="baseline_fifo">Базовый (FIFO)</option>`,
                `<option value="improved">Улучшенный</option>`];
    }
    if (!ortools) {
        // бэкенд не знает про режим или он выключен — показываем один невыбираемый пункт
        opts.push(`<option value="benchmark_ortools" disabled title="Требуется пакет ortools (см. requirements-benchmark.txt) — не установлен">OR-Tools — не установлен</option>`);
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
    try {
        // dist_mode не отправляем: переключателя расчёта в интерфейсе нет,
        // а значение приходит с сервера. Расписание в любом случае считается
        // на haversine — см. /api/plan/geometry в app/api.py.
        await api('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ region: state.region, solver_mode: state.mode }),
        });
        await loadPlan();
    } catch (e) {
        toast('Ошибка настроек: ' + e.message, 'error');
    }
}

async function loadPlan() {
    if (state.planAbort) state.planAbort.abort();   // предыдущий расчёт больше не нужен
    const ctrl = new AbortController();
    state.planAbort = ctrl;
    const reqId = nextPlanReqId();
    const sel = { region: state.region, mode: state.mode, dist: state.dist };
    startPlanLoading();
    try {
        const [plan, requests, engineers, dbStatus] = await Promise.all([
            api(`/api/plan?region=${sel.region}&mode=${sel.mode}&dist=${sel.dist}`, { signal: ctrl.signal }),
            api(`/api/requests?region=${sel.region}`, { signal: ctrl.signal }),
            api(`/api/engineers?region=${sel.region}`, { signal: ctrl.signal }),
            // Счётчики правок едем вместе с планом: и смена региона, и любая
            // правка данных проходят через loadPlan, поэтому статус не может
            // разъехаться с данными. Отдельной ошибкой не роняем план.
            api(`/api/db/status?region=${sel.region}`, { signal: ctrl.signal })
                .catch(() => null),
        ]);
        if (reqId !== state.planReqId) return;   // пока считалось, успели переключить
        state.plan = plan;
        state.requests = requests;
        state.engineers = engineers;
        state.dbStatus = dbStatus;
        state.lastScenario = null;
        state.selectedEng = null;
        state.hiddenStatuses = new Set();
        state.legendList = [...new Set(requests.map(r => r.status).filter(Boolean))];
        closeExplain();  // план пересчитан — прежнее объяснение больше не про этот план
        renderAll();
        renderDbStatus();
        // Полилинии по дорогам — отдельный запрос, уже после отрисовки плана:
        // карта сначала рисует прямые линии, потом заменяет их дорожными.
        loadGeometry(sel, reqId);
    } catch (e) {
        if (e.name !== 'AbortError') toast('Ошибка плана: ' + e.message, 'error');
    } finally {
        // Гасим индикатор в любом исходе, но только если это ещё актуальный
        // расчёт: устаревший ответ не должен погасить спиннер свежего.
        if (reqId === state.planReqId) stopPlanLoading();
    }
}

// Догружает геометрию маршрутов по дорогам. Не блокирует показ плана: сетевой
// запрос к OSRM идёт после того, как маршруты уже нарисованы прямыми линиями.
// Ответ для устаревшей комбинации (region/mode) выбрасывается.
async function loadGeometry(sel, reqId) {
    if (!state.plan) return;
    try {
        const g = await api(`/api/plan/geometry?region=${sel.region}&mode=${sel.mode}&dist=${sel.dist}`);
        if (reqId !== state.planReqId || !state.plan) return;
        const byId = new Map(g.routes.map(r => [r.engineer_id, r.route]));
        let filled = 0;
        state.plan.engineers.forEach(e => {
            if (e.stops.length && byId.has(e.id)) {
                const r = byId.get(e.id);
                e.route = r;
                if (r) filled++;
            }
        });
        renderMap(false);   // перерисовка без перецентровки — вьюпорт не прыгает
        const hint = $('mapHint');
        if (hint && filled === 0 && state.plan.engineers.some(e => e.stops.length)) {
            hint.textContent = 'Дорожные маршруты недоступны — показаны прямые линии';
        }
    } catch (e) {
        // Не ошибка плана: без геометрии карта рисует прямые линии.
        console.warn('Геометрия маршрутов не загрузилась:', e.message);
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
    if (section === 'routes' && state.map) setTimeout(() => state.map.resize(), 80);
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
    renderSummary();
    renderComparison();
    renderMapTools();
    renderMap(true);
    renderDynamicPanel();
    renderSectionMain(currentSection());
}

// ===== Сводка по плану (ТЗ 2.4.2) =====
// ===== Сравнение с базовым FIFO (ТЗ 2.4.3) =====
function renderComparison() {
    const box = $('planCompare');
    if (!box) return;
    const cmp = state.plan.comparison;
    const base = cmp && cmp.baseline;
    if (!base) { box.hidden = true; box.innerHTML = ''; return; }
    const cur = cmp.improved;
    box.hidden = false;

    const rows = [
        { k: 'Выполнено заявок', b: base.assigned_count, c: cur ? cur.assigned_count : null, better: 'up' },
        { k: 'Неназначено', b: base.unassigned_count, c: cur ? cur.unassigned_count : null, better: 'down' },
        { k: 'Исполнителей в рейсе', b: base.engineers_used, c: cur ? cur.engineers_used : null, better: 'down' },
        { k: 'Пробег, км', b: base.total_km, c: cur ? cur.total_km : null, better: 'down', dp: 1 },
        { k: 'Пробег на заявку, км', b: base.assigned_count ? base.total_km / base.assigned_count : 0,
          c: cur && cur.assigned_count ? cur.total_km / cur.assigned_count : null, better: 'down', dp: 2 },
    ];

    let html = '<div class="cmp-title">Сравнение с базовым FIFO</div>';
    html += '<table class="cmp-table"><tr><th>Показатель</th><th>FIFO</th>'
        + (cur ? '<th>Улучшенный</th><th>Δ</th>' : '') + '</tr>';
    rows.forEach(r => {
        const fmt = v => v == null ? '—' : (r.dp ? v.toFixed(r.dp) : v);
        let delta = '';
        if (cur && r.c != null) {
            const d = r.c - r.b;
            const good = r.better === 'up' ? d > 0 : d < 0;
            delta = d === 0 ? '=' : (good ? '▲' : '▼') + ' ' + fmt(Math.abs(d));
        }
        html += `<tr><td>${esc(r.k)}</td><td class="cmp-b">${fmt(r.b)}</td>`
            + (cur ? `<td class="cmp-c">${fmt(r.c)}</td><td class="cmp-d">${delta}</td>` : '') + '</tr>';
    });
    html += '</table>';
    if (cmp.note) html += `<div class="cmp-note">${esc(cmp.note)}</div>`;
    if (cmp.control) html += '<div class="cmp-note dim">Контрольное распределение доступно в разделе «Сравнение».</div>';
    box.innerHTML = html;
}

function renderSummary() {
    const box = $('planSummary');
    if (!box) return;
    const s = state.plan.summary;
    if (!s) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    let html = '<div class="summary-lead">' + esc(s.headline) + '</div>';
    html += '<div class="summary-text">' + esc(s.text) + '</div>';
    if (s.factors && s.factors.length) {
        html += '<ul class="summary-factors">' + s.factors.map(f => '<li>' + esc(f) + '</li>').join('') + '</ul>';
    }
    box.innerHTML = html;
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
    if (hit && hit.stop) state.map.flyTo({ center: [hit.stop.lng, hit.stop.lat], zoom: 15, duration: 800 });
    else {
        const req = planRid2Req(id);
        if (req && req.lat != null) state.map.flyTo({ center: [req.lng, req.lat], zoom: 15, duration: 800 });
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

// ===== Карта (MapLibre GL) =====
// Маршруты — GeoJSON-слой «line», точки — DOM-маркеры (maplibregl.Marker).

function _clearPoints() {
    for (const k in (state.markerPool || {})) state.markerPool[k].stale = true;
}

function _prunePoints() {
    for (const k in (state.markerPool || {})) {
        if (state.markerPool[k].stale) {
            state.markerPool[k].m.remove();
            delete state.markerPool[k];
        }
    }
}

function _addPoint(key, lng, lat, html, popupHtml) {
    if (!state.markerPool) state.markerPool = {};
    let rec = state.markerPool[key];
    if (!rec) {
        const el = document.createElement('div');
        el.style.cursor = 'pointer';
        const m = new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(state.map);
        rec = { m, el, key };
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (rec.popupHtml) state.popup.setLngLat(rec.lngLat).setHTML(rec.popupHtml).addTo(state.map);
        });
        state.markerPool[key] = rec;
    }
    rec.stale = false;
    rec.lngLat = [lng, lat];
    rec.m.setLngLat([lng, lat]);
    if (rec.html !== html) { rec.html = html; rec.el.innerHTML = html; }
    rec.popupHtml = popupHtml;
    return rec.m;
}

function _setRoutes(features) {
    const src = state.map && state.map.getSource('routes');
    if (src) src.setData({ type: 'FeatureCollection', features });
}

function fitCoords(coords, pad) {
    if (!coords || !coords.length) return;
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
    coords.forEach(c => {
        if (c[0] < minLng) minLng = c[0];
        if (c[1] < minLat) minLat = c[1];
        if (c[0] > maxLng) maxLng = c[0];
        if (c[1] > maxLat) maxLat = c[1];
    });
    const dLng = (maxLng - minLng) * (pad || 0.15);
    const dLat = (maxLat - minLat) * (pad || 0.15);
    state.map.fitBounds([[minLng - dLng, minLat - dLat], [maxLng + dLng, maxLat + dLat]], { duration: 600 });
}

function _lineCoords(eng) {
    const pts = (Array.isArray(eng.route) && eng.route.length > 1)
        ? eng.route
        : [eng.start, ...eng.stops.map(s => ({ lat: s.lat, lng: s.lng }))];
    return pts.map(p => Array.isArray(p) ? [p[1], p[0]] : [p.lng, p.lat]);
}

function _routeFeature(eng, idx, highlighted) {
    return {
        type: 'Feature',
        properties: {
            color: COLORS[idx % COLORS.length],
            w: highlighted ? 6 : 3,
            engIdx: idx,
            tip: `Инженер ${idx + 1} · ${eng.stops.length} заявок · ${eng.km.toFixed(1)} км`,
        },
        geometry: { type: 'LineString', coordinates: _lineCoords(eng) },
    };
}

// Кнопка «Почему так» в попапе заявки на карте. Клик по попапу сам по себе
// ничего не делает — показываем разбор плана по этой заявке.
function _whyBtn(rid) {
    return `<div class="popup-actions"><button class="popup-why" onclick="whyFromPopup('${esc(rid)}')">Почему так</button></div>`;
}

function whyFromPopup(id) {
    if (state.popup) state.popup.remove();
    explainRequest(id);
}

function _stopPopup(st, eng) {
    const req = planRid2Req(st.request_id);
    return `<b>Заявка ${esc(st.request_id)}</b><br>${esc(st.address)}<br>` +
        `Окно: <b>${esc(st.window[0])}–${esc(st.window[1])}</b><br>` +
        `Прибытие: ${esc(st.arrival)} · Работы: ${esc(st.start_work)}–${esc(st.finish)}<br>` +
        `Инженер: <b>${esc(eng.name)}</b> (${esc(eng.transport_label)})<br>` +
        (req ? `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}` : '') +
        _whyBtn(st.request_id);
}

function _officePopup(plan, start) {
    return `<b>🏢 Офис (${esc(plan.region_name)})</b><br>${esc(start.name || '')}`;
}

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

    _setRoutes([_routeFeature(selEng, idx, false)]);
    state.lineCoords[idx] = _lineCoords(selEng);

    _addPoint('ofc-' + idx, selEng.start.lng, selEng.start.lat, officeIconHtml(), _officePopup(plan, selEng.start));

    const shownStops = new Set();
    selEng.stops.forEach(st => {
        shownStops.add(st.request_id);
        _addPoint('stp-' + idx + '-' + st.request_id, st.lng, st.lat, stopIconHtml(color, String(st.step)), _stopPopup(st, selEng));
        state.rid2eng[st.request_id] = { engIdx: idx, stop: st };
        state.reqPoints[st.request_id] = [st.lng, st.lat];
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
        let html, popup;
        if (hit) {
            html = stopIconHtml(COLORS[hit.idx % COLORS.length], String(hit.stop.step));
            popup = `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>` +
                `Окно: <b>${esc(req.window_start)}–${esc(req.window_end)}</b><br>` +
                `Инженер: <b>${esc(hit.eng.name)}</b> (${esc(hit.eng.transport_label)})<br>` +
                (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}`;
            _addPoint('stp-' + hit.idx + '-' + req.id, req.lng, req.lat, html, popup);
        } else if (isUn) {
            html = statusIconHtml('#95a5a6');
            popup = `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>❌ <b>Не назначена</b><br>` +
                `Окно: <b>${esc(req.window_start)}–${esc(req.window_end)}</b><br>` +
                (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                `Тип: ${esc(req.skill_label)} · ${esc(req.bk_type)}`;
            _addPoint('un-' + req.id, req.lng, req.lat, html, popup);
        } else {
            html = statusIconHtml(STATUS_COLORS[req.status] || STATUS_DEFAULT);
            popup = `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>` +
                (req.status ? `Статус: <b>${esc(req.status)}</b><br>` : '') +
                (req.control_brigade ? `Контроль: ${esc(req.control_brigade)}<br>` : '') +
                (req.gigabit ? 'Гигабитное подключение' : '');
            _addPoint('rs-' + req.id, req.lng, req.lat, html, popup);
        }
        state.reqPoints[req.id] = [req.lng, req.lat];
    });
}

// renderMap(fit): fit=false — перерисовка без перецентровки (переключение чекбоксов).
function renderMap(fit) {
    if (!state.map) return;
    _clearPoints();
    state.lineCoords = {};
    state.rid2eng = {};
    state.reqPoints = {};
    const plan = state.plan;
    const singleIdx = (state.selectedEng != null && state.selectedEng < plan.engineers.length)
        ? state.selectedEng : null;
    const allRoutes = singleIdx == null;
    const effShowAll = allRoutes || state.showAllRequests;

    if (singleIdx != null && state.showAllRequests) {
        drawAssignmentLayer(plan, singleIdx);
        if (fit) fitCoords(state.lineCoords[singleIdx], 0.2);
        _prunePoints();
        return;
    }

    const features = [];
    const allCoords = [];
    const displayed = new Set();

    plan.engineers.forEach((eng, idx) => {
        if (singleIdx != null && idx !== singleIdx) return;
        const color = COLORS[idx % COLORS.length];
        const highlighted = singleIdx == null && state.selectedEng === idx;
        const coords = _lineCoords(eng);
        features.push(_routeFeature(eng, idx, highlighted));
        state.lineCoords[idx] = coords;
        allCoords.push(...coords);

        _addPoint('ofc-' + idx, eng.start.lng, eng.start.lat, officeIconHtml(), _officePopup(plan, eng.start));

        eng.stops.forEach(st => {
            displayed.add(st.request_id);
            _addPoint('stp-' + idx + '-' + st.request_id, st.lng, st.lat, stopIconHtml(color, String(st.step)), _stopPopup(st, eng));
            state.rid2eng[st.request_id] = { engIdx: idx, stop: st };
            state.reqPoints[st.request_id] = [st.lng, st.lat];
        });
    });

    // Неназначенные (чекбокс «Показывать неназначенные»)
    const unassignedIds = new Set();
    plan.unassigned.forEach(u => unassignedIds.add(u.request_id));
    if (state.showUnassigned) {
        plan.unassigned.forEach(u => {
            const req = planRid2Req(u.request_id);
            if (!req || req.lat == null) return;
            displayed.add(u.request_id);
            _addPoint('un-' + u.request_id, req.lng, req.lat, unassignedIconHtml(),
                `<b>Заявка ${esc(u.request_id)}</b><br>${esc(u.address)}<br>❌ <b>Не назначена</b><br>Причина: ${esc(u.reason)}`
                + (u.reason_detail ? `<br><span style="opacity:.8">${esc(u.reason_detail)}</span>` : '')
                + _whyBtn(u.request_id));
            state.reqPoints[u.request_id] = [req.lng, req.lat];
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
            _addPoint('rs-' + req.id, req.lng, req.lat, statusIconHtml(color),
                `<b>Заявка ${esc(req.id)}</b><br>${esc(req.address)}<br>Статус: <b>${esc(req.status || '—')}</b>` +
                (req.control_brigade ? `<br>Контроль: ${esc(req.control_brigade)}` : '') +
                (req.gigabit ? '<br>Гигабитное подключение' : '') +
                (req.is_active === false ? '<br>⊘ <b>Не планируется</b>' : '') +
                _whyBtn(req.id));
            state.reqPoints[req.id] = [req.lng, req.lat];
        });
    }

    _setRoutes(features);
    _prunePoints();

    if (fit && singleIdx != null && state.lineCoords[singleIdx]) fitCoords(state.lineCoords[singleIdx], 0.2);
    else if (fit && allCoords.length) fitCoords(allCoords, 0.15);
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
    if (state.selectedEng != null && state.lineCoords[idx]) {
        fitCoords(state.lineCoords[idx], 0.2);
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
        const pt = state.reqPoints[id];
        state.map.flyTo({ center: pt || [req.lng, req.lat], zoom: 15, duration: 800 });
    }
}

// ===== Объяснение решения (ТЗ 2.1.7) =====
async function explainRequest(id) {
    const card = $('explainCard');
    if (!card) return;
    card.hidden = false;
    card.innerHTML = '<div class="explain-title">Объяснение по заявке ' + esc(id) + '…</div>';
    switchSection('routes', null);
    try {
        const qs = new URLSearchParams({ region: state.region, request_id: id, mode: state.mode, dist: state.dist });
        const e = await api('/api/explain?' + qs.toString());
        renderExplain(e);
    } catch (err) {
        card.innerHTML = '<div class="explain-head err">Объяснение недоступно</div><div class="explain-hint">' + esc(err.message) + '</div>';
    }
}

function renderExplain(e) {
    const card = $('explainCard');
    const badge = e.assigned
        ? '<span class="explain-badge ok">назначена</span>'
        : '<span class="explain-badge bad">' + esc(e.reason || 'не назначена') + '</span>';
    let html = '<div class="explain-head">' + badge
        + '<span class="explain-title">' + esc(e.request_id) + ' — ' + esc(e.address) + '</span>'
        + '<button class="explain-close" onclick="closeExplain()">✕</button></div>';
    html += '<div class="explain-lead">' + esc(e.headline) + '</div>';
    if (e.detail) html += '<div class="explain-why">' + esc(e.detail) + '</div>';
    if (e.facts && e.facts.length) {
        html += '<ul class="explain-list">' + e.facts.map(f => '<li>' + esc(f) + '</li>').join('') + '</ul>';
    }
    if (e.alternatives && e.alternatives.length) {
        html += '<div class="explain-sub">Кто ещё мог взять заявку</div><ul class="explain-list">'
            + e.alternatives.map(a => '<li>' + esc(a.engineer) + ' (' + esc(a.transport) + ') — добавка '
                + (a.added_km >= 0 ? '+' : '') + esc(a.added_km) + ' км</li>').join('') + '</ul>';
    }
    if (e.rejected && e.rejected.length) {
        html += '<div class="explain-sub">Не подошли</div><ul class="explain-list dim">'
            + e.rejected.map(r => '<li>' + esc(r.engineer) + ' — ' + esc(r.why) + '</li>').join('') + '</ul>';
    }
    if (e.hint) html += '<div class="explain-hint">💡 ' + esc(e.hint) + '</div>';
    card.innerHTML = html;
}

function closeExplain() {
    const card = $('explainCard');
    if (card) { card.hidden = true; card.innerHTML = ''; }
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
        <div class="item-card clickable" onclick="explainRequest('${esc(s.request_id)}')">
            <div class="item-header">
                <div class="item-icon" style="background:#e8f5f0;color:#2d9c7a;">✓</div>
                <div class="item-title">${esc(s.request_id)} — ${esc(s.address)}</div>
                <div class="status status-done">${esc(s.window[0])}–${esc(s.window[1])}</div>
                <button class="map-btn edit" onclick="event.stopPropagation();openRequestEditor('${esc(s.request_id)}')">Изменить</button>
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
        <div class="item-card clickable" onclick="explainRequest('${esc(u.request_id)}')">
            <div class="item-header">
                <div class="item-icon" style="background:#fde8e8;color:#e74c3c;">✕</div>
                <div class="item-title">${esc(u.request_id)} — ${esc(u.address)}</div>
                <div class="status status-error">не назначена</div>
                <button class="map-btn edit" onclick="event.stopPropagation();openRequestEditor('${esc(u.request_id)}')">Изменить</button>
                <button class="map-btn" onclick="showRequestOnMap('${esc(u.request_id)}', event)">Карта</button>
            </div>
            <div class="item-meta">
                <span>⚠ ${esc(u.reason)}</span>
                ${req ? `<span>${esc(req.skill_label)}</span>` : ''}
                ${req && req.status ? `<span>${statusChip(req.status)}</span>` : ''}
                ${req && req.control_brigade ? `<span>Контроль: ${esc(req.control_brigade)}</span>` : ''}
            </div>
            ${u.reason_detail ? `<div class="item-detail">${esc(u.reason_detail)}</div>` : ''}
        </div>`;
    });

    // Справочно: все заявки источника (статусы/контроль), не вошедшие в план.
    const inPlan = new Set([...assigned.map(s => s.request_id), ...unassigned.map(u => u.request_id)]);
    const rest = state.requests.filter(r => !inPlan.has(r.id) && pass(r, q));
    // Отменённые показываем отдельно: они не планируются намеренно, и вернуть
    // их в рейс можно только отсюда — сняв галочку в редакторе.
    const cancelled = rest.filter(r => r.is_active === false);
    const other = rest.filter(r => r.is_active !== false);
    if (cancelled.length) {
        html += '<div class="group-title">Отменённые (' + cancelled.length + ')</div>';
        cancelled.forEach(r => {
            html += `
            <div class="item-card clickable muted" onclick="explainRequest('${esc(r.id)}')">
                <div class="item-header">
                    <div class="item-icon" style="background:#eef2f5;color:#90a4ae;">⊘</div>
                    <div class="item-title">${esc(r.id)} — ${esc(r.address)}</div>
                    <div class="status status-pending">не планируется</div>
                    <button class="map-btn edit" onclick="event.stopPropagation();openRequestEditor('${esc(r.id)}')">Вернуть в план</button>
                    <button class="map-btn" onclick="showRequestOnMap('${esc(r.id)}', event)">Карта</button>
                </div>
                <div class="item-meta">
                    <span>${esc(r.skill_label)} ${esc(r.bk_type)}</span>
                    <span>Окно ${esc(r.window_start)}–${esc(r.window_end)}</span>
                </div>
            </div>`;
        });
    }
    if (other.length) {
        html += '<div class="group-title">Справочно (источник): ' + other.length + '</div>';
        other.forEach(r => {
            html += `
            <div class="item-card clickable" onclick="explainRequest('${esc(r.id)}')">
                <div class="item-header">
                    <div class="item-icon" style="background:#eef2f5;color:#90a4ae;">·</div>
                    <div class="item-title">${esc(r.id)} — ${esc(r.address)}</div>
                    <div class="status status-pending">${statusChip(r.status)}</div>
                    <button class="map-btn edit" onclick="event.stopPropagation();openRequestEditor('${esc(r.id)}')">Изменить</button>
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
                <button class="map-btn edit" onclick="event.stopPropagation();openEngineerEditor('${esc(e.id)}')">Изменить</button>
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
    const modeLabel = { baseline_fifo: 'Базовый (FIFO)', improved: 'Улучшенный', benchmark_ortools: 'OR-Tools' }[state.mode] || state.mode;
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
    const reqId = nextPlanReqId();   // прошлый ответ по геометрии теперь неактуален
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
        // Маршруты перестроились — дорожные линии тоже надо обновить.
        loadGeometry({ region: state.region, mode: state.mode, dist: state.dist }, reqId);
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
function stopIconHtml(color, num) {
    return `<div class="stop-ico" style="border-color:${color};color:${color};">${esc(num)}</div>`;
}
function officeIconHtml() {
    return '<div class="office-ico">🏢</div>';
}
function unassignedIconHtml() {
    return '<div class="un-ico">✕</div>';
}
function statusIconHtml(color) {
    return `<div class="status-ico" style="border-color:${color};color:${color};">◉</div>`;
}

// ===== Правка данных в БД =====

// Типы заявок и нормативы дублируют app/regions.py: справочник живёт на
// сервере, а здесь нужен для подсказок и предзаполнения формы. Источник
// истины — норматив, который выставляет сервер, поэтому подсказка помечена
// «по типу», а не задаёт значение жёстко.
const BK_TYPES = [
    { value: 'Подключение', min: 100, skill: 'Подключение и дозаказы' },
    { value: 'Дозаказ', min: 90, skill: 'Подключение и дозаказы' },
    { value: 'Локальная заявка', min: 50, skill: 'Локальные работы' },
    { value: 'Глобальная проблема', min: 40, skill: 'Аварийные работы' },
];
const SKILL_KEYS = [
    { key: 'podklyuchenie', label: 'Подключение и дозаказы' },
    { key: 'lokalnye', label: 'Локальные работы' },
    { key: 'avariynye', label: 'Аварийные работы' },
];
const TRANSPORT_KEYS = [
    { key: 'auto', label: 'Автомобиль' },
    { key: 'transit', label: 'Общественный транспорт' },
    { key: 'bike', label: 'Велосипед' },
    { key: 'walk', label: 'Пешком' },
];

function typeNorm(bkType) {
    const t = BK_TYPES.find(x => x.value === bkType);
    return t ? t.min : 60;
}

function syncRequestType() {
    // Подсказка, а не запрет: норматив можно задать вручную, если дом длинный.
    $('reqDurationHint').textContent = 'По типу заявки: ' + typeNorm($('reqBkType').value) + ' мин';
}

// --- Заявка ---

function openRequestEditor(id = null, coords = null) {
    state.editReqId = id;
    state.editReqCoords = coords;
    const req = id ? state.requests.find(r => r.id === id) : null;

    $('reqModalTitle').textContent = req ? `Заявка ${id}` : 'Новая заявка';
    $('reqAddress').value = req ? req.address : (coords?.address || '');
    $('reqDistrict').value = req ? (req.district || '') : (coords?.district || '');
    $('reqBkType').value = req ? req.bk_type : 'Локальная заявка';
    $('reqWinStart').value = req ? (req.window_start || '') : '09:00';
    $('reqWinEnd').value = req ? (req.window_end || '') : '18:00';
    $('reqTech').value = req ? (req.tech || '') : '';
    $('reqTransport').value = req ? (req.required_transport || '') : '';
    $('reqGigabit').checked = !!req?.gigabit;
    $('reqActive').checked = req ? req.is_active !== false : true;
    // У отменённой заявки приоритет и норматив надо показать как есть, а не
    // предлагать значения по умолчанию.
    $('reqPriority').value = req ? (req.priority || 'normal') : 'auto';
    $('reqDuration').value = req ? (req.duration_min || '') : '';
    $('reqDeleteBtn').hidden = !req;
    $('reqActiveWrap').hidden = !req;   // у новой заявки отменять нечего
    // Почему так — вопрос к плану, а к новой заявки в плане ещё нет.
    $('reqExplainBtn').hidden = !req;
    $('reqExplainBtn').title = 'Объяснение по сохранённому в базе плану. ' +
        'Несохранённые правки формы в объяснение не попадут.';
    $('reqSaveBtn').textContent = req ? 'Сохранить' : 'Создать';

    syncCoordsHint(req, coords);
    syncRequestType();
    $('reqModal').hidden = false;
    setTimeout(() => $('reqAddress').focus(), 0);
}

// Модалка лежит поверх карты, поэтому закрываем её и уходим в раздел
// «Маршруты», где объяснение и живёт (explainRequest переключает раздел сам).
function explainFromEditor() {
    const id = state.editReqId;
    if (!id) return;
    closeRequestEditor();
    explainRequest(id);
}

function syncCoordsHint(req, coords) {
    const hint = $('reqCoordsHint');
    const lat = req ? req.lat : coords?.lat;
    const lng = req ? req.lng : coords?.lng;
    if (lat == null || lng == null) {
        hint.textContent = 'Координат нет — сервер определит их по адресу.';
        hint.classList.remove('warn');
        return;
    }
    let text = `Координаты: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    // Обратное геокодирование отдаёт ближайший объект, а не точный дом, поэтому
    // адрес из клика всегда стоит проверить глазами до сохранения.
    if (coords) {
        text += ' — из клика по карте';
        if (coords.address) {
            text += '. Рядом может быть соседний дом — проверьте адрес.';
            hint.classList.add('warn');
        }
    }
    hint.textContent = text;
    if (!coords) hint.classList.remove('warn');
}

function closeRequestEditor() {
    $('reqModal').hidden = true;
    state.editReqId = null;
    state.editReqCoords = null;
}

async function saveRequest() {
    const id = state.editReqId;
    const address = $('reqAddress').value.trim();
    if (!address) { toast('Укажите адрес', 'error'); $('reqAddress').focus(); return; }
    const winStart = $('reqWinStart').value;
    const winEnd = $('reqWinEnd').value;
    if (!winStart || !winEnd) { toast('Укажите окно работ: обе границы', 'error'); return; }

    const body = {
        address,
        bk_type: $('reqBkType').value,
        district: $('reqDistrict').value.trim(),
        window_start: winStart,
        window_end: winEnd,
        tech: $('reqTech').value || null,
        required_transport: $('reqTransport').value || null,
        gigabit: $('reqGigabit').checked,
    };
    const dur = $('reqDuration').value.trim();
    if (dur) body.duration_min = Number(dur);
    if ($('reqPriority').value !== 'auto') body.priority = $('reqPriority').value;
    if (id) body.is_active = $('reqActive').checked;

    $('reqSaveBtn').disabled = true;
    try {
        if (id) {
            await api(`/api/requests/${encodeURIComponent(id)}?region=${state.region}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            toast('Заявка сохранена');
        } else {
            const payload = { ...body };
            if (state.editReqCoords) {
                payload.lat = state.editReqCoords.lat;
                payload.lng = state.editReqCoords.lng;
            }
            await api(`/api/requests?region=${state.region}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast('Заявка добавлена');
        }
        closeRequestEditor();
        await afterMutation();
    } catch (e) {
        toast('Не сохранено: ' + e.message, 'error');
    } finally {
        $('reqSaveBtn').disabled = false;
    }
}

async function deleteRequest() {
    const id = state.editReqId;
    if (!id) return;
    if (!confirm(`Удалить заявку ${id}? Действие необратимо до сброса данных.`)) return;
    try {
        await api(`/api/requests/${encodeURIComponent(id)}?region=${state.region}`, { method: 'DELETE' });
        toast('Заявка удалена');
        closeRequestEditor();
        await afterMutation();
    } catch (e) {
        toast('Не удалено: ' + e.message, 'error');
    }
}

// --- Постановка заявки кликом по карте ---

function startPlacingRequest() {
    state.placing = true;
    switchSection('routes', null);
    $('mainMap').classList.add('map-placing');
    showPlaceBanner(true);
    showMapHint('📍 Кликните по карте, чтобы поставить заявку в этом месте');
}

function showPlaceBanner(on) {
    let b = $('placeBanner');
    if (!on) { if (b) b.remove(); $('btnPlaceRequest')?.classList.remove('active'); return; }
    if (!b) {
        b = document.createElement('div');
        b.id = 'placeBanner';
        b.className = 'place-banner';
        b.innerHTML = '<span>Кликните по карте, чтобы поставить заявку</span>' +
            '<button onclick="stopPlacingRequest()">Отмена</button>';
        $('mainMap').parentElement.appendChild(b);
    }
}

function stopPlacingRequest() {
    state.placing = false;
    $('mainMap')?.classList.remove('map-placing');
    showPlaceBanner(false);
}

async function placeRequestAt(lngLat) {
    stopPlacingRequest();
    const lat = lngLat.lat, lng = lngLat.lng;
    showMapHint('📍 Определяем адрес…');
    let rev = { ok: false, address: '', district: '' };
    try {
        rev = await api('/api/geo/reverse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat, lng }),
        });
    } catch (e) {
        // Обратное геокодирование — необязательный шаг: клик по карте уже дал
        // координаты, поэтому неудача с сетью не должна срывать постановку.
        toast('Адрес не определился, впишите его вручную', 'error');
    }
    openRequestEditor(null, { lat, lng, address: rev.address || '', district: rev.district || '' });
}

// --- Инженер ---

function openEngineerEditor(id) {
    const eng = state.engineers.find(e => e.id === id);
    if (!eng) { toast('Инженер не найден', 'error'); return; }
    state.editEngId = id;
    $('engModalTitle').textContent = `${eng.name}`;
    $('engName').value = eng.name || '';
    $('engTransport').value = eng.transport || 'auto';
    $('engSpeed').value = eng.speed_kph == null ? '' : eng.speed_kph;
    $('engShiftStart').value = eng.shift_start || '08:00';
    $('engShiftEnd').value = eng.shift_end || '20:00';
    $('engSkills').innerHTML = SKILL_KEYS.map(s => `
        <label class="form-check"><input type="checkbox" value="${esc(s.key)}"${eng.skills.includes(s.key) ? ' checked' : ''}><span>${esc(s.label)}</span></label>
    `).join('');
    $('engModal').hidden = false;
}

function closeEngineerEditor() {
    $('engModal').hidden = true;
    state.editEngId = null;
}

async function saveEngineer() {
    const id = state.editEngId;
    if (!id) return;
    const skills = [...$('engSkills').querySelectorAll('input:checked')].map(i => i.value);
    const body = {
        name: $('engName').value.trim(),
        skills,
        transport: $('engTransport').value,
        shift_start: $('engShiftStart').value,
        shift_end: $('engShiftEnd').value,
    };
    const sp = $('engSpeed').value.trim();
    body.speed_kph = sp ? Number(sp) : null;
    $('engSaveBtn').disabled = true;
    try {
        await api(`/api/engineers/${encodeURIComponent(id)}?region=${state.region}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        toast('Инженер сохранён');
        closeEngineerEditor();
        await afterMutation();
    } catch (e) {
        toast('Не сохранено: ' + e.message, 'error');
    } finally {
        $('engSaveBtn').disabled = false;
    }
}

// --- Сброс к исходным данным ---

function openResetDialog() {
    const st = state.dbStatus;
    const r = st?.requests, e = st?.engineers;
    $('resetLead').innerHTML = st
        ? `В регионе «${esc(st.region_name || state.region)}» относительно импорта: ` +
          `заявок <b>добавлено ${r.added}</b>, <b>изменено ${r.edited}</b>, <b>удалено ${r.deleted}</b>; ` +
          `инженеров изменено <b>${e.edited}</b>. Отметьте, что вернуть к исходному виду.`
        : 'Отметьте, что вернуть к исходному виду.';
    $('resetRequests').checked = true;
    $('resetEngineers').checked = true;
    $('resetReqCount').textContent = r ? `(в базе ${r.total})` : '';
    $('resetEngCount').textContent = e ? `(в базе ${e.total})` : '';
    $('resetModal').hidden = false;
    updateResetConfirm();
}

function closeResetDialog() {
    $('resetModal').hidden = true;
}

function updateResetConfirm() {
    const any = $('resetRequests').checked || $('resetEngineers').checked;
    $('resetConfirmBtn').disabled = !any;
    $('resetWarn').hidden = any;
}

async function doReset() {
    const body = { requests: $('resetRequests').checked, engineers: $('resetEngineers').checked };
    if (!body.requests && !body.engineers) { toast('Отметьте заявки или инженеров', 'error'); return; }
    $('resetConfirmBtn').disabled = true;
    try {
        const r = await api(`/api/db/reset?region=${state.region}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const what = [];
        if (body.requests) what.push(`заявок — ${r.requests.total}`);
        if (body.engineers) what.push(`инженеров — ${r.engineers.total}`);
        toast('Возвращено к импорту: ' + what.join(', '));
        closeResetDialog();
        await afterMutation();
    } catch (e) {
        toast('Не сброшено: ' + e.message, 'error');
    } finally {
        $('resetConfirmBtn').disabled = false;
    }
}

// --- Общее после любой правки ---
// План пересчитывается целиком: кеш ключуется отпечатком данных и после
// правки промахивается сам, поэтому ручного сброса кеша здесь нет.

async function afterMutation() {
    await loadPlan();
}

function renderDbStatus() {
    const st = state.dbStatus;
    const chip = (c, label) => c
        ? `<span class="chip">${label}: +${c.added} ~${c.edited} −${c.deleted}</span>` : '';
    for (const [el, c, label] of [
        ['dbStatusRequests', st?.requests, 'заявки'],
        ['dbStatusEngineers', st?.engineers, 'инженеры'],
    ]) {
        const node = $(el);
        if (!node) continue;
        node.className = 'db-status' + (st?.dirty ? ' dirty' : '');
        node.innerHTML = st
            ? (c ? `Правлено ${label} ${chip(c, label)}` : `Правлено ${label}: нет`)
            : '';
    }
    const btn = $('btnResetData');
    if (btn) {
        btn.disabled = !(st && st.dirty);
        btn.title = st && st.dirty
            ? 'Вернуть импортированные значения'
            : 'Правок нет — сбрасывать нечего';
    }
}

// Esc закрывает открытую форму — привычно и не требует тянуться к мыши.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('reqModal').hidden) closeRequestEditor();
    else if (!$('engModal').hidden) closeEngineerEditor();
    else if (!$('resetModal').hidden) closeResetDialog();
    else if (state.placing) stopPlacingRequest();
});
$('resetRequests').addEventListener('change', updateResetConfirm);
$('resetEngineers').addEventListener('change', updateResetConfirm);

init();