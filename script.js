// ══════════════════════════════════════════════════════════════
// XAU/USD Scalp Terminal — script.js
// FIX: Chart ocupa 100% del espacio — resize correcto con rAF
// ══════════════════════════════════════════════════════════════

const statusBar      = document.getElementById('status-bar');
const chartContainer = document.getElementById('tv-chart');
const zoomOverlay    = document.getElementById('zoom-overlay');
const objectsList    = document.getElementById('objects-list');

let chartData            = [];
let lineasDinamicas      = [];
let lineasTendenciaAuto  = [];
let lineasLiquidez       = [];
let hasLoadedHistory     = false;

function setStatus(text, tipo = 'info') {
    if (!statusBar) return;
    statusBar.textContent = text;
    const colores = {
        info:   { bg: '#0d1117', color: '#00d4ff' },
        buy:    { bg: '#0a1f14', color: '#00e676' },
        sell:   { bg: '#1a0a0d', color: '#ff1744' },
        error:  { bg: '#1a0a0d', color: '#ff1744' },
        search: { bg: '#0d1117', color: '#f5a623' },
    };
    const c = colores[tipo] || colores.info;
    statusBar.style.background = c.bg;
    statusBar.style.color      = c.color;
}

// ── 1. CHART — creado con dimensiones reales del contenedor ───
// FIX: usamos innerWidth/Height como fallback si el layout aún no pintó
const chart = window.LightweightCharts.createChart(chartContainer, {
    width:  chartContainer.clientWidth  > 0 ? chartContainer.clientWidth  : window.innerWidth  - 320,
    height: chartContainer.clientHeight > 0 ? chartContainer.clientHeight : window.innerHeight - 110,
    layout: { background: { color: '#080b10' }, textColor: '#94a3b8' },
    grid:   { vertLines: { color: '#0f1923' }, horzLines: { color: '#0f1923' } },
    crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#1a2235' },
    timeScale: { borderColor: '#1a2235', timeVisible: true, secondsVisible: false },
});

const candleSeries = chart.addCandlestickSeries({
    upColor:       '#00f5fd', downColor:      '#fffbfc',
    borderVisible: false,
    wickUpColor:   '#00e676', wickDownColor:  '#ff1744',
});

function resizeChart(forceFit = false) {
    const w = chartContainer.clientWidth > 0 ? chartContainer.clientWidth : window.innerWidth - 320;
    const h = chartContainer.clientHeight > 0 ? chartContainer.clientHeight : window.innerHeight - 110;
    if (w <= 0 || h <= 0) return;
    chart.resize(w, h);
    if (forceFit) chart.timeScale().fitContent();
}

window.addEventListener('load', () => resizeChart(true));
requestAnimationFrame(() => resizeChart(true));
window.addEventListener('resize', () => resizeChart(true));

// ── 2. WEBSOCKET ───────────────────────────────────────────────
const socket = new WebSocket('ws://localhost:8091');
let currentTF = '2m';

socket.onopen = () => {
    setStatus('Conectado — Escaneo automático activado...', 'info');

    // FIX: Actualizar el live dot en el header
    const dot = document.getElementById('live-dot');
    const txt = document.getElementById('live-txt');
    if (dot) dot.classList.add('on');
    if (txt) txt.textContent = 'EN VIVO';

    socket.send(JSON.stringify({ action: 'get_history', timeframe: currentTF }));
};

socket.onclose = () => {
    setStatus('Desconectado — verifica que mt5.py esté corriendo.', 'error');
    const dot = document.getElementById('live-dot');
    const txt = document.getElementById('live-txt');
    if (dot) dot.classList.remove('on');
    if (txt) txt.textContent = 'Desconectado';
};

socket.onerror = () => setStatus('Error de conexión.', 'error');

socket.onmessage = ({ data }) => {
    try {
        const msg = JSON.parse(data);

        if (msg.action === 'ticks') {
            if (!hasLoadedHistory || !Array.isArray(msg.data) || msg.data.length === 0) {
                return;
            }

            const ultimaVela = msg.data[msg.data.length - 1];
            candleSeries.update({
                time:  ultimaVela.time,
                open:  ultimaVela.open,
                high:  ultimaVela.high,
                low:   ultimaVela.low,
                close: ultimaVela.close
            });
            return;
        }

        if (msg.action === 'refresh') {
            limpiarLineas();

            if (!hasLoadedHistory && Array.isArray(msg.data) && msg.data.length > 0) {
                chartData = msg.data.map(v => ({
                    time:  v.time,
                    open:  v.open,
                    high:  v.high,
                    low:   v.low,
                    close: v.close
                }));
                candleSeries.setData(chartData);
                requestAnimationFrame(() => {
                    chart.timeScale().fitContent();
                });
                hasLoadedHistory = true;
            }

            if (msg.tendencias?.length)      pintarTendenciasAuto(msg.tendencias);
            if (msg.lineas_horizontales?.length) pintarLineasOperativas(msg.lineas_horizontales, msg.sesgo);

            if (Array.isArray(msg.marcador) && msg.marcador.length > 0) {
                candleSeries.setMarkers(msg.marcador.map(marker => ({
                    time:     marker.time,
                    position: marker.position,
                    color:    marker.color,
                    shape:    marker.shape,
                    text:     marker.text,
                    size:     marker.size || 2
                })));
            } else {
                candleSeries.setMarkers([]);
            }

            renderizarPanel(msg.bloques_ordenes, msg.sesgo, msg.ia_probabilidad, msg.model_accuracy);
            renderSignalHistory(msg.signal_history);

            if (msg.alerta) {
                const tipo = msg.sesgo === 'VENTA' ? 'sell' : msg.sesgo === 'COMPRA' ? 'buy' : 'search';
                setStatus(msg.alerta.msg, tipo);
            } else {
                setStatus('Escaneando el mercado en tiempo real...', 'search');
            }
        }

    } catch (e) {
        console.error('Error procesando socket:', e);
    }
};

// Cambio de timeframe
document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', e => {
        document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentTF   = e.target.dataset.tf;
        isFirstLoad = true;        // forzar recarga completa + fitContent
        hasLoadedHistory = false; // permitir cargar nuevo timeframe
        limpiarLineas();
        candleSeries.setData([]);
        socket.send(JSON.stringify({ action: 'get_history', timeframe: currentTF }));
    });
});

// ── 3. DIBUJOS DINÁMICOS Y AUTOMÁTICOS ────────────────────────
function limpiarLineas() {
    lineasDinamicas.forEach(l     => { try { candleSeries.removePriceLine(l); } catch {} });
    lineasTendenciaAuto.forEach(s => { try { chart.removeSeries(s);           } catch {} });
    lineasLiquidez.forEach(l      => { try { candleSeries.removePriceLine(l); } catch {} });
    lineasDinamicas     = [];
    lineasTendenciaAuto = [];
    lineasLiquidez      = [];
}

function pintarTendenciasAuto(tendencias) {
    tendencias.forEach(t => {
        const serieTL = chart.addLineSeries({
            color:             t.color,
            lineWidth:         3,
            lineStyle:         0,
            priceLineVisible:  false,
            lastValueVisible:  false
        });
        serieTL.setData([
            { time: t.p1.time, value: t.p1.price },
            { time: t.p2.time, value: t.p2.price }
        ]);
        lineasTendenciaAuto.push(serieTL);
    });
}

function pintarLineasOperativas(lineas, sesgo) {
    const esVenta = sesgo === 'VENTA';
    lineas.forEach(l => {
        let color, width, style;
        if      (l.label.includes('BARRIDO'))                              { color = '#ff5722'; width = 1; style = 3; }
        else if (l.label.includes('ENTRADA'))                              { color = '#f5a623'; width = 2; style = 0; }
        else if (l.label.includes('TARGET') || l.label.includes('TP'))    { color = esVenta ? '#00fc15' : '#00e676'; width = 2; style = 2; }
        const line = candleSeries.createPriceLine({
            price:            l.precio,
            color,
            lineWidth:        width,
            lineStyle:        style,
            axisLabelVisible: true,
            title:            l.label
        });
        lineasDinamicas.push(line);
    });
}

// ── 4. PANEL LATERAL ──────────────────────────────────────────
// SE AGREGÓ probIA y modelAccuracy como parámetros
function renderizarPanel(bloques, sesgo, probIA, modelAccuracy) {
    const contenedor = document.getElementById('ops-content') || objectsList;
    if (!contenedor) return;

    const esBuy  = sesgo === 'COMPRA';
    const esSell = sesgo === 'VENTA';
    const colorSesgo = esSell ? '#ff1744' : esBuy ? '#00e676' : '#f5a623';

    let html = `
    <div class="panel-sesgo" style="border-color:${colorSesgo}22; background:${colorSesgo}0a;">
        <div class="sesgo-info">
            <span class="sesgo-label">DIRECCIÓN ACTUAL</span>
            <span class="sesgo-value" style="color:${colorSesgo}">${sesgo}</span>
        </div>
    </div>`;

    // ── NUEVO: INYECCIÓN VISUAL DE LA PREDICCIÓN DE IA ──
    if (probIA) {
        let bgColor = '#0d1117', txtColor = '#94a3b8', aiStatus = '';
        const pct = parseFloat(probIA);
        
        if (!isNaN(pct)) {
            if (pct >= 75.0) { bgColor = '#0a1f14'; txtColor = '#00e676'; aiStatus = '◆ ALTA CONFIANZA'; }
            else if (pct >= 50.0) { bgColor = '#111827'; txtColor = '#e2e8f0'; aiStatus = '◆ EVALUANDO'; }
            else { bgColor = '#1a0a0d'; txtColor = '#ff1744'; aiStatus = '◆ RIESGO ALTO'; }
        }

        html += `
        <div style="background:${bgColor}; color:${txtColor}; padding:10px; margin-top:10px; border-radius:6px; border:1px solid #1a2235; font-family: 'JetBrains Mono', monospace; font-size:12px; text-align:center; transition: all 0.3s ease;">
            🧠 <strong>IA Precisión:</strong> ${probIA} ${aiStatus}
        </div>`;
    }

    if (modelAccuracy) {
        html += `
        <div style="background:#081014; color:#94a3b8; padding:10px; margin-top:10px; border-radius:6px; border:1px solid #1a2235; font-family: 'JetBrains Mono', monospace; font-size:12px; text-align:center;">
            📈 <strong>Precisión del modelo entrenado:</strong> ${modelAccuracy}
        </div>`;
    }

    if (bloques?.length) {
        const t  = bloques[0];
        const dc = esBuy ? '#00e676' : '#ff1744';

        html += `
        <div class="trade-card" style="border-color:${dc}33; background:${dc}08; margin-top:15px;">
            <div class="trade-head" style="color:${dc}">
                <span>${esBuy ? '⬆ COMPRA ACTIVA' : '⬇ VENTA ACTIVA'}</span>
                <span class="trade-badge" style="background:${dc}20;border-color:${dc}40;color:${dc};">⚡ ${t.motivo}</span>
            </div>
            <div class="trade-row" style="margin-top:10px;">
                <span class="trade-lbl">❌ Barrido</span>
                <span class="trade-val" style="color:#ff5722">${t.eliminado}</span>
            </div>
            <div class="trade-row">
                <span class="trade-lbl">🔹 Entrada</span>
                <strong class="trade-val" style="color:#f5a623">${t.entrada.toFixed(2)}</strong>
            </div>
            <div class="trade-row">
                <span class="trade-lbl">🎯 TP Objetivo (75%)</span>
                <strong class="trade-val" style="color:${dc}">${t.target.toFixed(2)}</strong>
            </div>
            <div class="trade-tp-bar" style="background:${dc}15;border-color:${dc}30; margin-top:10px;">
                <span style="color:#94a3b8">Cazando Liquidez</span>
                <strong style="color:${dc}">+${t.puntos.toLocaleString()} pts proyectados</strong>
            </div>
        </div>`;

    } else {
        html += `
        <div class="panel-waiting" style="margin-top:20px;">
            <div class="wait-icon">📡</div>
            <p>Radar Activo</p>
            <small>Analizando gráficas automáticamente. Se colocará una flecha en el gráfico cuando se confirme la dirección tras barrer un nivel importante.</small>
        </div>`;
    }

    contenedor.innerHTML = html;
}

function renderSignalHistory(history) {
    const container = document.getElementById('signal-history');
    if (!container) return;
    if (!Array.isArray(history) || history.length === 0) {
        container.innerHTML = `
            <div class="signal-hint">
                <strong>Historial de señales</strong>
                <p>No hay señales recientes.</p>
            </div>`;
        return;
    }

    const items = history.slice(0, 12).map(item => {
        const isWin = item.resultado === 'ganador';
        const isLose = item.resultado === 'perdedor' || item.resultado === 'rechazada';
        const color = isWin ? '#00e676' : isLose ? '#ff1744' : '#f5a623';
        const bg = isWin ? 'rgba(0,230,118,.08)' : isLose ? 'rgba(255,23,68,.08)' : 'rgba(245,166,35,.08)';
        const title = item.event === 'cierre' ? `Cierre ${item.resultado}` : item.event === 'entrada' ? 'Entrada abierta' : 'Rechazo IA';
        const prob = item.probabilidad || item.prob || 'N/A';
        return `
            <div class="signal-item" style="border-color:${color}; background:${bg};">
                <div class="signal-item-title">${title}</div>
                <div class="signal-item-row">
                    <span>Entrada: ${item.entrada?.toFixed?.(2) ?? '-'}</span>
                    <span>TP: ${item.target?.toFixed?.(2) ?? '-'}</span>
                    <span>SL: ${item.stoploss?.toFixed?.(2) ?? '-'}</span>
                </div>
                <div class="signal-item-row signal-item-meta">
                    <span>IA: ${prob}</span>
                    <span>${item.time ? new Date(item.time).toLocaleTimeString() : ''}</span>
                </div>
            </div>`;
    }).join('');

    container.innerHTML = `<div class="signal-history-header">Historial de señales</div>${items}`;
}

// ── 5. HERRAMIENTAS MANUALES ───────────────────────────────────
let currentTool = null, firstPoint = null, manualObjects = {}, objCounter = 0;

document.querySelectorAll('.tool-btn').forEach(btn => {
    btn.addEventListener('click', e => {
        const id = e.target.closest('button').id;
        if (id === 'zoom-in')  { chart.timeScale().zoomIn();  return; }
        if (id === 'zoom-out') { chart.timeScale().zoomOut(); return; }
        if (id === 'box-zoom') { toggleBoxZoom();             return; }
        document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active-tool'));
        currentTool = currentTool === id ? null : id;
        if (currentTool) e.target.closest('button').classList.add('active-tool');
        firstPoint = null;
    });
});

chart.subscribeClick(param => {
    if (!currentTool || !param.point || !param.time) return;
    const price = candleSeries.coordinateToPrice(param.point.y);
    if (!price) return;

    if (currentTool === 'draw-hline') { crearHLine(price); desactivar(); return; }

    if (['draw-trendline', 'draw-box', 'draw-gann'].includes(currentTool)) {
        if (!firstPoint) {
            firstPoint = { time: param.time, value: price };
            setStatus('Punto 1 marcado — haz clic en el segundo punto', 'info');
        } else {
            const p2 = { time: param.time, value: price };
            if (currentTool === 'draw-trendline') crearTrend(firstPoint, p2);
            if (currentTool === 'draw-box')       crearBox(firstPoint, p2);
            if (currentTool === 'draw-gann')      crearGann(firstPoint, p2);
            desactivar();
        }
    }
});

function desactivar() {
    currentTool = null; firstPoint = null;
    document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active-tool'));
}

function registrarObjeto(id, titulo, tipo, ref, grosor = 2) {
    manualObjects[id] = { tipo, obj: ref };
    const pane = document.getElementById('pane-draw') || objectsList;
    const lista = pane.querySelector('#objects-list') || pane;

    const item = document.createElement('div');
    item.id = `item_${id}`;
    item.className = 'object-item';
    item.innerHTML = `
        <span>${titulo}</span>
        <div class="item-controls">
            <input type="number" id="w_${id}" value="${grosor}" min="1" max="5" class="level-width">
            <button id="del_${id}" class="level-delete">✕</button>
        </div>`;
    lista.appendChild(item);

    document.getElementById(`w_${id}`).addEventListener('change', e => {
        const g = parseInt(e.target.value);
        const o = manualObjects[id];
        if (o.tipo === 'hline' || o.tipo === 'trend') o.obj.applyOptions({ lineWidth: g });
        else if (Array.isArray(o.obj)) o.obj.forEach(s => s.applyOptions({ lineWidth: g }));
    });

    document.getElementById(`del_${id}`).addEventListener('click', () => {
        const o = manualObjects[id];
        if      (o.tipo === 'hline')      candleSeries.removePriceLine(o.obj);
        else if (o.tipo === 'trend')      chart.removeSeries(o.obj);
        else if (Array.isArray(o.obj))    o.obj.forEach(s => chart.removeSeries(s));
        document.getElementById(`item_${id}`)?.remove();
        delete manualObjects[id];
    });
}

function crearHLine(p) {
    objCounter++;
    registrarObjeto(
        `hl_${objCounter}`, `— Nivel ${p.toFixed(2)}`, 'hline',
        candleSeries.createPriceLine({ price: p, color: '#00d4ff', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: 'Nivel' })
    );
}

function crearTrend(p1, p2) {
    objCounter++;
    const s = chart.addLineSeries({ color: '#f5a623', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    s.setData([p1, p2]);
    registrarObjeto(`tr_${objCounter}`, `⟋ Tendencia`, 'trend', s);
}

function crearBox(p1, p2) {
    objCounter++;
    const top = chart.addLineSeries({ color: '#9b6dff', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const bot = chart.addLineSeries({ color: '#9b6dff', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    top.setData([{ time: p1.time, value: p1.value }, { time: p2.time, value: p1.value }]);
    bot.setData([{ time: p1.time, value: p2.value }, { time: p2.time, value: p2.value }]);
    registrarObjeto(`bx_${objCounter}`, `▭ Zona`, 'box', [top, bot]);
}

function crearGann(p1, p2) {
    objCounter++;
    const mid = (p1.value + p2.value) / 2;
    const s1  = chart.addLineSeries({ color: '#00d4ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const s2  = chart.addLineSeries({ color: '#00d4ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const s3  = chart.addLineSeries({ color: '#00d4ff', lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });
    s1.setData([{ time: p1.time, value: p1.value }, { time: p2.time, value: p1.value }]);
    s2.setData([{ time: p1.time, value: p2.value }, { time: p2.time, value: p2.value }]);
    s3.setData([{ time: p1.time, value: mid       }, { time: p2.time, value: mid       }]);
    registrarObjeto(`gn_${objCounter}`, `⊞ Gann 50%`, 'gann', [s1, s2, s3], 1);
}

// ── 6. BOX ZOOM ───────────────────────────────────────────────
let boxZoomActive = false;
function toggleBoxZoom() {
    boxZoomActive = !boxZoomActive;
    zoomOverlay.style.display = boxZoomActive ? 'block' : 'none';
    setStatus(boxZoomActive ? 'Box Zoom activo — haz clic y arrastra en el gráfico' : '', 'info');
}