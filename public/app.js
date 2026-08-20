/**
 * CSS PRO INSTITUTIONAL WEB PLATFORM — ENGINE JAVASCRIPT
 * Gerencia renderização gráfica em Canvas de alta performance, Badges laterais dinâmicos,
 * modo Split-View, sincronização em tempo real e modais analíticos.
 */

// Estado Global da Aplicação
const state = {
    currencies: ["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"],
    activeCurrencies: new Set(["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"]),
    viewMode: "single", // 'single', 'split2', 'split3'
    chartTFs: {
        chart1: "H1",
        chart2: "H4",
        chart3: "D1"
    },
    data: null,
    pairsFilter: "ALL",
    pairsSearch: "",
    selectedDeepDive: "AUD",
    matrixActiveCcy: "USD",
    matrixActiveTF: "H1",
    autoRefreshTimer: null
};

// Bandeiras e Cores Oficiais
const CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"];

const CCY_FLAGS = {
    USD: "🇺🇸", EUR: "🇪🇺", GBP: "🇬🇧", CHF: "🇨🇭",
    JPY: "🇯🇵", AUD: "🇦🇺", CAD: "🇨🇦", NZD: "🇳🇿"
};

const CCY_COLORS = {
    USD: "#FF3B30",
    EUR: "#00BFFF",
    GBP: "#3872FF",
    CHF: "#00E5FF",
    JPY: "#FFD700",
    AUD: "#FF8C00",
    CAD: "#E0245E",
    NZD: "#D2B48C"
};

// ==========================================================================
// INICIALIZAÇÃO DO SISTEMA
// ==========================================================================
document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
    fetchData();
    // Auto-refresh a cada 3.5 segundos
    state.autoRefreshTimer = setInterval(fetchData, 3500);
});

function setupEventListeners() {
    // 1. Currency Toggles no Header
    document.querySelectorAll(".ccy-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const ccy = btn.dataset.ccy;
            if (state.activeCurrencies.has(ccy)) {
                if (state.activeCurrencies.size > 1) {
                    state.activeCurrencies.delete(ccy);
                    btn.classList.remove("active");
                }
            } else {
                state.activeCurrencies.add(ccy);
                btn.classList.add("active");
            }
            renderAllCharts();
        });
    });

    // Toggle All Currencies
    const btnToggleAll = document.getElementById("btnToggleAll");
    if (btnToggleAll) {
        btnToggleAll.addEventListener("click", () => {
            if (state.activeCurrencies.size === state.currencies.length) {
                // Manter apenas as duas primeiras
                state.activeCurrencies.clear();
                state.activeCurrencies.add("EUR");
                state.activeCurrencies.add("USD");
                document.querySelectorAll(".ccy-btn").forEach(b => {
                    b.classList.toggle("active", b.dataset.ccy === "EUR" || b.dataset.ccy === "USD");
                });
            } else {
                state.currencies.forEach(c => state.activeCurrencies.add(c));
                document.querySelectorAll(".ccy-btn").forEach(b => b.classList.add("active"));
            }
            renderAllCharts();
        });
    }

    // 2. View Mode Selector (Single, Split2, Split3)
    document.querySelectorAll(".mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            state.viewMode = btn.dataset.mode;
            updateViewModeLayout();
        });
    });

    // 3. Timeframe Tabs independentes em cada gráfico
    setupTFTabs("chart1TFTabs", "chart1");
    setupTFTabs("chart2TFTabs", "chart2");
    setupTFTabs("chart3TFTabs", "chart3");

    // 4. Botão de Refresh Manual
    const btnRefresh = document.getElementById("btnRefresh");
    if (btnRefresh) {
        btnRefresh.addEventListener("click", async () => {
            const icon = btnRefresh.querySelector(".refresh-icon");
            icon.classList.add("rotating");
            await forceRecalculate();
            setTimeout(() => icon.classList.remove("rotating"), 800);
        });
    }

    // 5. Modais
    setupModals();
    setupMatrixModal();
    setupTrackRecordModal();

    // 6. Redimensionamento de Janela
    window.addEventListener("resize", () => {
        renderAllCharts();
        renderMatrixChart();
        if (state.trackRecordData) {
            if (state.activeTrackTab === 'analytics') renderGlobalEquityCurve(state.trackRecordData.equity_curve || []);
            else if (state.activeTrackTab === 'audit' && state.auditSelectedSession) renderAuditDetailPanel(state.auditSelectedSession);
        }
    });
}

function setupTFTabs(containerId, chartKey) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.querySelectorAll(".tf-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            container.querySelectorAll(".tf-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const tf = tab.dataset.tf;
            state.chartTFs[chartKey] = tf;
            renderChart(chartKey);
        });
    });
}

function updateViewModeLayout() {
    const chartsContainer = document.getElementById("chartsContainer");
    const wrap2 = document.getElementById("chartCardWrapper2");
    const wrap3 = document.getElementById("chartCardWrapper3");

    chartsContainer.classList.remove("split-2", "split-3");

    if (state.viewMode === "single") {
        wrap2.classList.add("hidden");
        wrap3.classList.add("hidden");
    } else if (state.viewMode === "split2") {
        chartsContainer.classList.add("split-2");
        wrap2.classList.remove("hidden");
        wrap3.classList.add("hidden");
    } else if (state.viewMode === "split3") {
        chartsContainer.classList.add("split-3");
        wrap2.classList.remove("hidden");
        wrap3.classList.remove("hidden");
    }

    setTimeout(renderAllCharts, 50);
}

// ==========================================================================
// BUSCA DE DADOS (API REST)
// ==========================================================================
async function fetchData() {
    try {
        const res = await fetch("/api/css/all");
        if (!res.ok) throw new Error("Erro na resposta da API");
        const json = await res.json();
        state.data = json;
        
        updateHeaderStatus(json);
        renderTable(json.currencies);
        renderAllCharts();
        updateStrongSignalsCount(json.pairs);
        if (typeof renderMatrixChart === "function") renderMatrixChart();
    } catch (err) {
        console.warn("Falha ao atualizar dados via API:", err);
    }
}

async function forceRecalculate() {
    try {
        const res = await fetch("/api/refresh", { method: "POST" });
        if (res.ok) {
            await fetchData();
        }
    } catch (err) {
        console.error("Erro ao forçar recálculo:", err);
    }
}

function updateHeaderStatus(data) {
    const statusDot = document.querySelector(".status-dot");
    const statusTitle = document.getElementById("mt5StatusText");
    const statusTime = document.getElementById("lastUpdateTime");

    if (data.mt5_connected) {
        statusDot.className = "status-dot online";
        statusTitle.textContent = "MT5 LIVE";
    } else {
        statusDot.className = "status-dot offline";
        statusTitle.textContent = "CACHE OFFLINE";
    }

    if (data.timestamp) {
        statusTime.textContent = data.timestamp.split(" ")[1] || data.timestamp;
    }
}

function updateStrongSignalsCount(pairs) {
    if (!pairs) return;
    const strongCount = pairs.filter(p => p.conviction.includes("MÁXIMA") || p.recommendation.includes("STRONG")).length;
    const badge = document.getElementById("strongSignalsCount");
    if (badge) badge.textContent = strongCount;
}

// ==========================================================================
// RENDERIZAÇÃO DOS GRÁFICOS INTERATIVOS (CANVAS 2D DE ALTA PERFORMANCE)
// ==========================================================================
function renderAllCharts() {
    renderChart("chart1");
    if (state.viewMode === "split2" || state.viewMode === "split3") {
        renderChart("chart2");
    }
    if (state.viewMode === "split3") {
        renderChart("chart3");
    }
}

function renderChart(chartKey) {
    if (!state.data || !state.data.charts) return;

    const tf = state.chartTFs[chartKey];
    const chartData = state.data.charts[tf];
    if (!chartData) return;

    const canvasId = chartKey === "chart1" ? "cssCanvas1" : chartKey === "chart2" ? "cssCanvas2" : "cssCanvas3";
    const overlayId = chartKey === "chart1" ? "badgesOverlay1" : chartKey === "chart2" ? "badgesOverlay2" : "badgesOverlay3";
    
    const canvas = document.getElementById(canvasId);
    const overlay = document.getElementById(overlayId);
    if (!canvas || !overlay) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    // Configurar resolução de alta densidade
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;

    // Limpar tela
    ctx.clearRect(0, 0, width, height);

    const times = chartData.times;
    const numPoints = times.length;
    if (numPoints < 2) return;

    // Encontrar limites Min/Max verticais
    let minVal = -0.30;
    let maxVal = 0.30;

    state.currencies.forEach(ccy => {
        if (state.activeCurrencies.has(ccy) && chartData.series[ccy]) {
            const arr = chartData.series[ccy];
            minVal = Math.min(minVal, ...arr);
            maxVal = Math.max(maxVal, ...arr);
        }
    });

    // Margem dinâmica
    minVal -= 0.08;
    maxVal += 0.08;

    const isMobile = window.innerWidth <= 768;
    const isSmall = window.innerWidth <= 480;
    const padding = { 
        top: 25, 
        bottom: 25, 
        left: isSmall ? 8 : 15, 
        right: isSmall ? 80 : (isMobile ? 100 : 175) 
    };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const getY = (val) => {
        return padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));
    };

    const getX = (idx) => {
        return padding.left + (idx / (numPoints - 1)) * chartW;
    };

    // 1. Desenhar Linhas de Grade, Níveis Institucionais e Eixo de Horários
    drawInstitutionalLevels(ctx, width, getX, getY, minVal, maxVal, padding);
    drawTimeAxis(ctx, width, height, times, getX, padding, tf);

    // 2. Desenhar Curvas das Moedas Ativas
    const lastPoints = [];

    state.currencies.forEach(ccy => {
        if (!state.activeCurrencies.has(ccy) || !chartData.series[ccy]) return;

        const series = chartData.series[ccy];
        const color = CCY_COLORS[ccy] || "#FFF";

        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";

        // Glow sutil
        ctx.shadowColor = color;
        ctx.shadowBlur = 4;

        for (let i = 0; i < series.length; i++) {
            const x = getX(i);
            const y = getY(series[i]);
            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }
        ctx.stroke();
        ctx.restore();

        // Guardar ponto final para badge flutuante
        const lastVal = series[series.length - 1];
        const lastX = getX(series.length - 1);
        const lastY = getY(lastVal);
        lastPoints.push({
            ccy: ccy,
            val: lastVal,
            targetY: lastY,
            y: lastY,
            lastX: lastX,
            color: color,
            flag: CCY_FLAGS[ccy] || ""
        });
    });

    // 3. Renderizar Badges Laterais Flutuantes no Eixo Direito com Conectores
    renderRightSideBadges(ctx, overlay, lastPoints, height, width, padding);
}

function drawInstitutionalLevels(ctx, width, getX, getY, minVal, maxVal, padding) {
    // Linha Zero (Equilíbrio)
    if (minVal <= 0 && maxVal >= 0) {
        const y0 = getY(0.0);
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = "rgba(100, 116, 139, 0.6)";
        ctx.lineWidth = 1.2;
        ctx.setLineDash([4, 4]);
        ctx.moveTo(padding.left, y0);
        ctx.lineTo(width - padding.right, y0);
        ctx.stroke();
        ctx.restore();
    }

    // Linha Verde (+0.20) — Zona de Parada Superior
    if (minVal <= 0.20 && maxVal >= 0.20) {
        const yGreen = getY(0.20);
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = "rgba(0, 230, 118, 0.75)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.shadowColor = "#00E676";
        ctx.shadowBlur = 6;
        ctx.moveTo(padding.left, yGreen);
        ctx.lineTo(width - padding.right, yGreen);
        ctx.stroke();
        ctx.restore();
    }

    // Linha Vermelha (-0.20) — Zona de Parada Inferior
    if (minVal <= -0.20 && maxVal >= -0.20) {
        const yRed = getY(-0.20);
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = "rgba(255, 51, 75, 0.75)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.shadowColor = "#FF334B";
        ctx.shadowBlur = 6;
        ctx.moveTo(padding.left, yRed);
        ctx.lineTo(width - padding.right, yRed);
        ctx.stroke();
        ctx.restore();
    }

    // Linhas Secundárias (±0.50, ±1.00)
    const extraLevels = [0.50, -0.50, 1.00, -1.00];
    extraLevels.forEach(lvl => {
        if (minVal <= lvl && maxVal >= lvl) {
            const y = getY(lvl);
            ctx.save();
            ctx.beginPath();
            ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
            ctx.lineWidth = 1.0;
            ctx.setLineDash([2, 4]);
            ctx.moveTo(padding.left, y);
            ctx.lineTo(width - padding.right, y);
            ctx.stroke();
            ctx.restore();
        }
    });
}

function drawTimeAxis(ctx, width, height, times, getX, padding, tf) {
    if (!times || times.length < 2) return;

    ctx.save();
    ctx.font = "9.5px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";

    const isMobile = window.innerWidth <= 768;
    const isSmall = window.innerWidth <= 480;
    const targetLabelsCount = isSmall ? 3 : (isMobile ? 5 : 7);
    const step = Math.max(1, Math.floor((times.length - 1) / targetLabelsCount));

    for (let i = 0; i < times.length; i += step) {
        const tStr = times[i];
        if (!tStr) continue;

        const x = getX(i);
        
        // Linha de grade vertical sutil
        ctx.save();
        ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, height - padding.bottom);
        ctx.stroke();
        ctx.restore();

        // Formatação amigável do horário/data
        let label = tStr;
        if (tStr.includes(" ")) {
            const [datePart, timePart] = tStr.split(" ");
            const [year, month, day] = datePart.split("-");
            if (tf === "H1" || tf === "H4") {
                label = isSmall ? `${timePart}` : `${timePart} (${day}/${month})`;
            } else if (tf === "D1" || tf === "W1") {
                label = `${day}/${month}`;
            } else if (tf === "MN1") {
                label = `${month}/${year ? year.slice(2) : ''}`;
            }
        }

        ctx.save();
        ctx.fillStyle = "rgba(148, 163, 184, 0.85)";
        ctx.fillText(label, x, height - 7);
        ctx.restore();
    }
    ctx.restore();
}

function resolveBadgePositions(points, containerHeight, minSpacing = 24) {
    if (!points || points.length === 0) return;

    points.sort((a, b) => a.targetY - b.targetY);

    const minY = 18;
    const maxY = containerHeight - 18;
    const n = points.length;

    points.forEach(p => p.y = Math.max(minY, Math.min(maxY, p.targetY)));

    for (let pass = 0; pass < 20; pass++) {
        let changed = false;

        for (let i = 1; i < n; i++) {
            const desiredMin = points[i - 1].y + minSpacing;
            if (points[i].y < desiredMin) {
                points[i].y = desiredMin;
                changed = true;
            }
        }

        if (points[n - 1].y > maxY) {
            points[n - 1].y = maxY;
            for (let i = n - 2; i >= 0; i--) {
                const desiredMax = points[i + 1].y - minSpacing;
                if (points[i].y > desiredMax) {
                    points[i].y = desiredMax;
                    changed = true;
                }
            }
        }

        if (points[0].y < minY) {
            points[0].y = minY;
            for (let i = 1; i < n; i++) {
                const desiredMin = points[i - 1].y + minSpacing;
                if (points[i].y < desiredMin) {
                    points[i].y = desiredMin;
                    changed = true;
                }
            }
        }

        if (!changed) break;
    }
}

function renderRightSideBadges(ctx, overlay, points, containerHeight, width, padding) {
    overlay.innerHTML = "";
    if (!points || points.length === 0) return;

    const isMobile = window.innerWidth <= 768;
    const isSmall = window.innerWidth <= 480;
    const minSpacing = isSmall ? 18 : (isMobile ? 20 : 25);

    resolveBadgePositions(points, containerHeight, minSpacing);

    const badgeStartX = width - padding.right + (isSmall ? 4 : 10);

    points.forEach(p => {
        // 1. Desenhar ponto luminoso no fim da linha
        if (ctx && p.lastX !== undefined) {
            ctx.save();
            ctx.fillStyle = p.color;
            ctx.shadowColor = p.color;
            ctx.shadowBlur = 8;
            ctx.beginPath();
            ctx.arc(p.lastX, p.targetY, 3.5, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();

            // 2. Traçar guia suave conectando o final da linha ao badge
            ctx.save();
            ctx.strokeStyle = p.color;
            ctx.globalAlpha = 0.35;
            ctx.lineWidth = 1.2;
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.moveTo(p.lastX + 4, p.targetY);
            const midX = (p.lastX + badgeStartX) / 2;
            ctx.bezierCurveTo(midX, p.targetY, midX, p.y, badgeStartX, p.y);
            ctx.stroke();
            ctx.restore();
        }

        // 3. Renderizar o badge HTML
        const badge = document.createElement("div");
        badge.className = "floating-ccy-badge";
        badge.style.top = `${p.y}px`;
        badge.style.setProperty("--badge-color", p.color);

        const valStr = (p.val >= 0 ? "+" : "") + p.val.toFixed(2);
        badge.innerHTML = `
            <span>${p.flag}</span>
            <span>${p.ccy}</span>
            <span class="badge-val">${valStr}</span>
        `;
        overlay.appendChild(badge);
    });
}

// ==========================================================================
// RENDERIZAÇÃO DA TABELA INSTITUCIONAL DE MOEDAS (AS IN VIDEO)
// ==========================================================================
function renderTable(currencies) {
    const tbody = document.getElementById("currencyTableBody");
    if (!tbody || !currencies) return;

    tbody.innerHTML = "";

    currencies.forEach(item => {
        const tr = document.createElement("tr");

        // 1. MOEDA
        const ccyCell = document.createElement("td");
        ccyCell.innerHTML = `
            <div class="ccy-cell" onclick="openDeepDive('${item.symbol}')">
                <span class="ccy-flag">${item.flag}</span>
                <span style="color: ${item.color}">${item.symbol}</span>
            </div>
        `;

        // 2. SCORES
        const h1Score = formatScoreCell(item.h1_score);
        const h4Score = formatScoreCell(item.h4_score);
        const d1Score = formatScoreCell(item.d1_score);

        // 3. ANGULAÇÃO / ROC
        const angleCell = document.createElement("td");
        angleCell.className = "angle-cell";
        angleCell.innerHTML = formatAngleDisplay(item.active_h1_triad ? item.active_h1_triad.angle : "Sutil");

        // 4. REGIÃO NO BOX
        const regionCell = document.createElement("td");
        const rType = item.active_h1_triad ? item.active_h1_triad.region_type : "";
        let rClass = "neutral";
        if (rType && rType.includes("VERDE")) rClass = "green";
        else if (rType && rType.includes("VERMELHA")) rClass = "red";
        
        regionCell.innerHTML = `
            <span class="region-badge ${rClass}">
                ${item.active_h1_triad ? item.active_h1_triad.region : "Box"}
            </span>
        `;

        // 5. STATUS MULTI-TF (LEDS LUMINOSOS 5-TF)
        const ledsCell = document.createElement("td");
        ledsCell.className = "text-center";
        const leds = item.leds || {};
        ledsCell.innerHTML = `
            <div class="leds-container">
                <span class="led-dot ${leds.MN1 || 'yellow'}" title="MN1: ${leds.MN1}"></span>
                <span class="led-dot ${leds.W1 || 'yellow'}" title="W1: ${leds.W1}"></span>
                <span class="led-dot ${leds.D1 || 'yellow'}" title="D1: ${leds.D1}"></span>
                <span class="led-dot ${leds.H4 || 'yellow'}" title="H4: ${leds.H4}"></span>
                <span class="led-dot ${leds.H1 || 'yellow'}" title="H1: ${leds.H1}"></span>
            </div>
        `;

        // 6. SINAL BADGE (BUY / SELL / NEUTRAL)
        const signalCell = document.createElement("td");
        signalCell.className = "text-center";
        const badgeType = item.signal_badge ? item.signal_badge.toLowerCase() : "neutral";
        signalCell.innerHTML = `
            <span class="signal-pill ${badgeType}">
                ${item.signal_badge || "NEUTRAL"}
            </span>
        `;

        // 7. CICLO DEVENDO
        const owingCell = document.createElement("td");
        owingCell.className = "owing-cell";
        owingCell.textContent = item.active_h1_triad ? item.active_h1_triad.owing_cycle : "Devendo Alinhamento";

        // 8. AÇÕES (RAIO-X)
        const actionsCell = document.createElement("td");
        actionsCell.className = "text-center";
        actionsCell.innerHTML = `
            <button class="btn-raio-x" onclick="openDeepDive('${item.symbol}')">
                🔎 Raio-X 5-TF
            </button>
        `;

        tr.appendChild(ccyCell);
        tr.appendChild(h1Score);
        tr.appendChild(h4Score);
        tr.appendChild(d1Score);
        tr.appendChild(angleCell);
        tr.appendChild(regionCell);
        tr.appendChild(ledsCell);
        tr.appendChild(signalCell);
        tr.appendChild(owingCell);
        tr.appendChild(actionsCell);

        tbody.appendChild(tr);
    });
}

function formatScoreCell(val) {
    const td = document.createElement("td");
    td.className = "score-cell " + (val > 0.05 ? "positive" : val < -0.05 ? "negative" : "neutral");
    td.textContent = (val >= 0 ? "+" : "") + Number(val).toFixed(2);
    return td;
}

function formatAngleDisplay(angleStr) {
    if (!angleStr) return "Sutil";
    if (angleStr.includes("Foguete")) return `<span style="color: #00FF88;">🚀 FOGUETE (▲▲)</span>`;
    if (angleStr.includes("Montanha-Russa")) return `<span style="color: #FF334B;">🎢 QUEDA FORTE (▼▼)</span>`;
    if (angleStr.includes("UP") || angleStr.includes("Força")) return `<span style="color: #00E676;">▲ Inclinado UP</span>`;
    if (angleStr.includes("DN") || angleStr.includes("Fraqueza")) return `<span style="color: #FF5266;">▼ Inclinado DN</span>`;
    return `<span style="color: #94A3B8;">${angleStr}</span>`;
}

// ==========================================================================
// MODAIS (OPERAÇÕES 28 PARES, RAIO-X 5-TF, HISTÓRICO)
// ==========================================================================
function setupModals() {
    // 1. Modal 28 Pares
    const btnOpenPairs = document.getElementById("btnOpenPairsModal");
    const modalPairs = document.getElementById("pairsModal");
    const btnClosePairs = document.getElementById("btnClosePairsModal");

    if (btnOpenPairs && modalPairs) {
        btnOpenPairs.addEventListener("click", () => {
            renderPairsTable();
            modalPairs.classList.remove("hidden");
        });
        btnClosePairs.addEventListener("click", () => modalPairs.classList.add("hidden"));
        modalPairs.addEventListener("click", (e) => {
            if (e.target === modalPairs) modalPairs.classList.add("hidden");
        });
    }

    // Filtros de pares
    document.querySelectorAll(".filter-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            document.querySelectorAll(".filter-pill").forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            state.pairsFilter = pill.dataset.filter;
            renderPairsTable();
        });
    });

    const searchInput = document.getElementById("pairSearchInput");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            state.pairsSearch = e.target.value.toUpperCase();
            renderPairsTable();
        });
    }

    // 2. Modal Raio-X
    const modalDeepDive = document.getElementById("deepDiveModal");
    const btnCloseDeepDive = document.getElementById("btnCloseDeepDiveModal");
    if (modalDeepDive && btnCloseDeepDive) {
        btnCloseDeepDive.addEventListener("click", () => modalDeepDive.classList.add("hidden"));
        modalDeepDive.addEventListener("click", (e) => {
            if (e.target === modalDeepDive) modalDeepDive.classList.add("hidden");
        });
    }

    // 3. Modal Histórico
    const btnOpenHistory = document.getElementById("btnOpenHistoryModal");
    const modalHistory = document.getElementById("historyModal");
    const btnCloseHistory = document.getElementById("btnCloseHistoryModal");

    if (btnOpenHistory && modalHistory) {
        btnOpenHistory.addEventListener("click", async () => {
            await loadHistoryDates();
            modalHistory.classList.remove("hidden");
        });
        btnCloseHistory.addEventListener("click", () => modalHistory.classList.add("hidden"));
        modalHistory.addEventListener("click", (e) => {
            if (e.target === modalHistory) modalHistory.classList.add("hidden");
        });
    }
}

function setupMatrixModal() {
    const btnOpenMatrix = document.getElementById("btnOpenMatrixModal");
    const modalMatrix = document.getElementById("matrixModal");
    const btnCloseMatrix = document.getElementById("btnCloseMatrixModal");
    if (btnOpenMatrix && modalMatrix) {
        btnOpenMatrix.addEventListener("click", () => {
            modalMatrix.classList.remove("hidden");
            setTimeout(renderMatrixChart, 60);
        });
        btnCloseMatrix.addEventListener("click", () => modalMatrix.classList.add("hidden"));
        modalMatrix.addEventListener("click", (e) => {
            if (e.target === modalMatrix) modalMatrix.classList.add("hidden");
        });
    }

    const matrixSelector = document.getElementById("matrixCcySelector");
    if (matrixSelector) {
        matrixSelector.querySelectorAll(".filter-pill").forEach(pill => {
            pill.addEventListener("click", () => {
                matrixSelector.querySelectorAll(".filter-pill").forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                state.matrixActiveCcy = pill.dataset.ccy;
                renderMatrixChart();
            });
        });
    }

    const matrixTFTabs = document.getElementById("matrixTFTabs");
    if (matrixTFTabs) {
        matrixTFTabs.querySelectorAll(".tf-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                matrixTFTabs.querySelectorAll(".tf-tab").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                state.matrixActiveTF = tab.dataset.tf;
                renderMatrixChart();
            });
        });
    }
}

function renderMatrixChart() {
    if (!state.data || !state.data.pair_charts) return;

    const tf = state.matrixActiveTF;
    const ccy = state.matrixActiveCcy;
    const allPairCharts = state.data.pair_charts[tf];
    if (!allPairCharts) return;

    const canvas = document.getElementById("matrixCanvas");
    const overlay = document.getElementById("matrixBadgesOverlay");
    if (!canvas || !overlay) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    // Encontrar pares relacionados
    const activePairs = [];
    Object.keys(allPairCharts).forEach(sym => {
        if (sym.includes(ccy)) {
            activePairs.push(sym);
        }
    });

    if (activePairs.length === 0) return;

    // Achar os tempos (vamos pegar do charts normal)
    const times = state.data.charts[tf].times;
    const numPoints = times.length;
    if (numPoints < 2) return;

    // Calcular minMax
    let minVal = -0.05;
    let maxVal = 0.05;

    activePairs.forEach(sym => {
        const arr = allPairCharts[sym];
        minVal = Math.min(minVal, ...arr);
        maxVal = Math.max(maxVal, ...arr);
    });

    minVal -= 0.05;
    maxVal += 0.05;

    const isMobile = window.innerWidth <= 768;
    const isSmall = window.innerWidth <= 480;
    const padding = { 
        top: 25, 
        bottom: 28, 
        left: isSmall ? 8 : 15, 
        right: isSmall ? 80 : (isMobile ? 100 : 175) 
    };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));
    const getX = (idx) => padding.left + (idx / (numPoints - 1)) * chartW;

    drawInstitutionalLevels(ctx, width, getX, getY, minVal, maxVal, padding);
    drawTimeAxis(ctx, width, height, times, getX, padding, tf);

    const lastPoints = [];

    activePairs.forEach(sym => {
        const arr = allPairCharts[sym];
        const base = sym.substring(0,3);
        const quote = sym.substring(3,6);
        const otherCcy = base === ccy ? quote : base;
        const color = CCY_COLORS[otherCcy] || "#FFF";
        const flag = CCY_FLAGS[otherCcy] || "🏳️";

        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.0;
        ctx.lineJoin = "round";
        ctx.shadowColor = color;
        ctx.shadowBlur = 4;

        for (let i = 0; i < arr.length; i++) {
            const x = getX(i);
            const y = getY(arr[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.restore();

        const lastVal = arr[arr.length - 1];
        const lastX = getX(arr.length - 1);
        const lastY = getY(lastVal);
        lastPoints.push({
            ccy: sym,
            val: lastVal,
            targetY: lastY,
            y: lastY,
            lastX: lastX,
            color: color,
            flag: flag
        });
    });

    renderRightSideBadges(ctx, overlay, lastPoints, height, width, padding);
}

// SCREENER DOS 28 PARES
function renderPairsTable() {
    const tbody = document.getElementById("pairsTableBody");
    if (!tbody || !state.data || !state.data.pairs) return;

    let list = state.data.pairs;

    // Aplicar filtros
    if (state.pairsFilter === "BUY") {
        list = list.filter(p => p.recommendation.includes("BUY"));
    } else if (state.pairsFilter === "SELL") {
        list = list.filter(p => p.recommendation.includes("SELL"));
    } else if (state.pairsFilter === "STRONG") {
        list = list.filter(p => p.conviction.includes("MÁXIMA") || p.recommendation.includes("STRONG"));
    } else if (state.pairsFilter === "BOX") {
        list = list.filter(p => p.recommendation.includes("NEUTRO") || p.recommendation.includes("BOX"));
    }

    if (state.pairsSearch) {
        list = list.filter(p => p.pair.includes(state.pairsSearch));
    }

    tbody.innerHTML = "";

    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">Nenhum par encontrado para o filtro selecionado.</td></tr>`;
        return;
    }

    list.forEach((item, index) => {
        const tr = document.createElement("tr");

        let recClass = "neutral";
        if (item.badge_type === "STRONG_BUY") recClass = "strong-buy";
        else if (item.badge_type === "BUY") recClass = "buy";
        else if (item.badge_type === "STRONG_SELL") recClass = "strong-sell";
        else if (item.badge_type === "SELL") recClass = "sell";

        tr.innerHTML = `
            <td style="color: var(--text-muted); font-family: var(--font-mono);">${index + 1}</td>
            <td>
                <div class="pair-badge-cell">
                    <span>${item.base_flag}${item.quote_flag}</span>
                    <span style="color: #FFFFFF;">${item.pair}</span>
                </div>
            </td>
            <td><span class="rec-badge ${recClass}">${item.recommendation}</span></td>
            <td style="font-weight: 700; color: ${item.conviction.includes('MÁXIMA') ? '#00FF88' : '#94A3B8'}">${item.conviction}</td>
            <td class="score-cell ${item.total_score > 0 ? 'positive' : item.total_score < 0 ? 'negative' : 'neutral'}">
                ${(item.total_score >= 0 ? "+" : "") + item.total_score.toFixed(2)}
            </td>
            <td class="score-cell">${(item.macro_diff >= 0 ? "+" : "") + item.macro_diff.toFixed(2)}</td>
            <td class="score-cell">${(item.op_diff >= 0 ? "+" : "") + item.op_diff.toFixed(2)}</td>
            <td style="font-size: 11.5px; color: var(--text-secondary);">${item.thesis}</td>
        `;

        tbody.appendChild(tr);
    });
}

// RAIO-X 5-TIMEFRAMES DA MOEDA
window.openDeepDive = function(ccy) {
    if (!state.data || !state.data.currencies) return;
    const ccyData = state.data.currencies.find(c => c.symbol === ccy);
    if (!ccyData) return;

    document.getElementById("deepDiveFlag").textContent = ccyData.flag;
    document.getElementById("deepDiveTitle").textContent = `${ccyData.symbol} (${ccyData.trade_bias})`;
    document.getElementById("deepDiveSubtitle").textContent = ccyData.final_verdict;

    const verdictCard = document.getElementById("deepDiveVerdictCard");
    verdictCard.innerHTML = `
        <div>
            <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">Estado de Confluência:</div>
            <div style="font-size: 14px; font-weight: 800; color: #FFFFFF;">${ccyData.confluence_state}</div>
            ${ccyData.has_divergence ? `<div style="color: #FFD600; font-size: 11.5px; margin-top: 4px;">${ccyData.divergence_alert}</div>` : ''}
        </div>
        <div>
            <span class="signal-pill ${ccyData.signal_badge.toLowerCase()}">${ccyData.signal_badge}</span>
        </div>
    `;

    const triadsGrid = document.getElementById("deepDiveTriadsGrid");
    triadsGrid.innerHTML = "";

    const tfs = ["MN1", "W1", "D1", "H4", "H1"];
    tfs.forEach(tf => {
        const triad = ccyData.triads[tf];
        if (!triad) return;

        const card = document.createElement("div");
        card.className = "triad-card";
        card.innerHTML = `
            <div class="triad-card-header">
                <span class="triad-tf-title">${tf}</span>
                <span class="triad-score ${triad.score > 0 ? 'positive' : 'negative'}">${triad.score_str} ${triad.dir}</span>
            </div>
            <div class="triad-step">
                <div class="triad-step-label">1. Região no Box</div>
                <div style="color: #FFF;">${triad.region}</div>
            </div>
            <div class="triad-step">
                <div class="triad-step-label">2. Ciclo Atual</div>
                <div style="color: #00E5FF;">${triad.current_cycle}</div>
            </div>
            <div class="triad-step">
                <div class="triad-step-label">3. Ciclo Devendo</div>
                <div style="color: #FFD600;">${triad.owing_cycle}</div>
            </div>
            <div class="triad-step">
                <div class="triad-step-label">4. Angulação</div>
                <div>${triad.angle}</div>
            </div>
        `;
        triadsGrid.appendChild(card);
    });

    document.getElementById("deepDiveModal").classList.remove("hidden");
};

// HISTÓRICO DE RELATÓRIOS
async function loadHistoryDates() {
    const listContainer = document.getElementById("historyDatesList");
    listContainer.innerHTML = `<div style="color: var(--text-muted);">Carregando datas...</div>`;

    try {
        const res = await fetch("/api/history/dates");
        const json = await res.json();
        listContainer.innerHTML = "";

        if (!json.dates || json.dates.length === 0) {
            listContainer.innerHTML = `<div style="color: var(--text-muted);">Nenhuma data arquivada.</div>`;
            return;
        }

        json.dates.forEach((d, i) => {
            const item = document.createElement("div");
            item.className = "history-date-item" + (i === 0 ? " active" : "");
            item.textContent = `${d.slice(6,8)}/${d.slice(4,6)}/${d.slice(0,4)}`;
            item.addEventListener("click", () => {
                document.querySelectorAll(".history-date-item").forEach(el => el.classList.remove("active"));
                item.classList.add("active");
                loadReportContent(d);
            });
            listContainer.appendChild(item);
        });

        // Carregar o primeiro
        if (json.dates.length > 0) {
            loadReportContent(json.dates[0]);
        }
    } catch (err) {
        listContainer.innerHTML = `<div style="color: var(--color-red);">Erro ao carregar datas.</div>`;
    }
}

async function loadReportContent(dateStr) {
    const header = document.getElementById("historyViewerHeader");
    const body = document.getElementById("historyMarkdownBody");
    
    header.innerHTML = `<span>📅 Relatório Diário: <strong>${dateStr.slice(6,8)}/${dateStr.slice(4,6)}/${dateStr.slice(0,4)}</strong></span>`;
    body.innerHTML = `<p style="color: var(--text-muted);">Carregando relatório...</p>`;

    try {
        const res = await fetch(`/api/history/${dateStr}`);
        const json = await res.json();
        
        // Renderização básica de markdown simples para visualização rápida
        let html = json.content
            .replace(/^# (.*$)/gim, '<h1>$1</h1>')
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/gim, '<em>$1</em>')
            .replace(/\n\n/gim, '<br><br>')
            .replace(/\| (.*) \|/gim, (match) => {
                const cols = match.split('|').filter(c => c.trim() !== '');
                return '<tr>' + cols.map(c => `<td>${c.trim()}</td>`).join('') + '</tr>';
            });
        
        body.innerHTML = html;
    } catch (err) {
        body.innerHTML = `<p style="color: var(--color-red);">Erro ao carregar conteúdo do relatório.</p>`;
    }
}

// ==========================================================================
// TRACK RECORD & AUDITORIA MULTI-PORTFÓLIO (3 ABAS & MASTER-DETAIL)
// ==========================================================================

function setupTrackRecordModal() {
    const modal = document.getElementById("trackRecordModal");
    const btnOpen = document.getElementById("btnOpenTrackRecordModal");
    const btnClose = document.getElementById("btnCloseTrackRecordModal");
    const btnRecalc = document.getElementById("btnRecalculateTrackRecord");

    state.activeTrackTab = "live"; // 'live', 'audit', 'analytics'
    state.livePollingTimer = null;

    if (btnOpen && modal) {
        btnOpen.addEventListener("click", () => {
            modal.classList.remove("hidden");
            loadTrackRecord(state.trackRecordFilter || "ALL");
            startLivePolling();
        });
    }

    if (btnClose && modal) {
        btnClose.addEventListener("click", () => {
            modal.classList.add("hidden");
            stopLivePolling();
        });
    }

    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.add("hidden");
                stopLivePolling();
            }
        });
    }

    // 1. Alternância das 3 Abas Principais
    document.querySelectorAll(".track-nav-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".track-nav-tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".track-tab-pane").forEach(p => p.classList.remove("active"));

            tab.classList.add("active");
            const targetTab = tab.dataset.tab;
            state.activeTrackTab = targetTab;

            if (targetTab === "live") {
                const pane = document.getElementById("paneLive");
                if (pane) pane.classList.add("active");
                fetchLiveSessionData();
            } else if (targetTab === "audit") {
                const pane = document.getElementById("paneAudit");
                if (pane) pane.classList.add("active");
                if (state.trackRecordData) renderAuditTab(state.trackRecordData);
            } else if (targetTab === "analytics") {
                const pane = document.getElementById("paneAnalytics");
                if (pane) pane.classList.add("active");
                if (state.trackRecordData) renderAnalyticsTab(state.trackRecordData);
            }
        });
    });

    // 2. Filtro de Moeda na Aba de Auditoria
    const currSelector = document.getElementById("trackCurrencySelector");
    if (currSelector) {
        currSelector.querySelectorAll(".filter-pill").forEach(pill => {
            pill.addEventListener("click", async () => {
                currSelector.querySelectorAll(".filter-pill").forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                state.trackRecordFilter = pill.dataset.ccy;
                const label = document.getElementById("trackFilterActiveLabel");
                if (label) {
                    label.textContent = pill.dataset.ccy === "ALL" ? "Exibindo: Consolidado Geral" : `Exibindo: Cestas de ${pill.dataset.ccy}`;
                }
                await loadTrackRecord(pill.dataset.ccy);
            });
        });
    }

    // 3. Botão de Recalcular Backtest
    if (btnRecalc) {
        btnRecalc.addEventListener("click", async () => {
            btnRecalc.disabled = true;
            btnRecalc.innerHTML = `<span>⏳ Calculando...</span>`;
            try {
                await fetch("/api/track-record/recalculate", { method: "POST" });
                await loadTrackRecord(state.trackRecordFilter || "ALL");
            } catch (err) {
                console.error("Erro ao recalcular:", err);
            } finally {
                btnRecalc.disabled = false;
                btnRecalc.innerHTML = `<span>🔄 Recalcular Backtest</span>`;
            }
        });
    }
}

function startLivePolling() {
    stopLivePolling();
    fetchLiveSessionData();
    state.livePollingTimer = setInterval(fetchLiveSessionData, 3000);
}

function stopLivePolling() {
    if (state.livePollingTimer) {
        clearInterval(state.livePollingTimer);
        state.livePollingTimer = null;
    }
}

async function fetchLiveSessionData() {
    try {
        const res = await fetch("/api/track-record/live");
        if (!res.ok) return;
        const data = await res.json();
        if (data && data.session) {
            state.liveSessionData = data.session;
            renderLiveTab(data.session);
        }
    } catch (err) {
        console.error("Erro no polling da sessão ao vivo:", err);
    }
}

async function loadTrackRecord(ccy = "ALL") {
    try {
        const res = await fetch(`/api/track-record/summary?currency=${ccy}`);
        if (!res.ok) throw new Error("Erro ao buscar dados do track record");
        const json = await res.json();
        state.trackRecordData = json;

        // Atualizar aba ativa
        if (state.activeTrackTab === "live") {
            await fetchLiveSessionData();
        } else if (state.activeTrackTab === "audit") {
            renderAuditTab(json);
        } else if (state.activeTrackTab === "analytics") {
            renderAnalyticsTab(json);
        }
    } catch (err) {
        console.error("Falha ao carregar track record:", err);
    }
}

// ==========================================================================
// ABA 1: RENDERIZAÇÃO DA SESSÃO AO VIVO (EM ANDAMENTO)
// ==========================================================================

function renderLiveTab(session) {
    if (!session) return;

    const pnlEl = document.getElementById("liveTotalPnL");
    const pipsEl = document.getElementById("liveTotalPips");
    const mfeEl = document.getElementById("liveTotalMFE");
    const maeEl = document.getElementById("liveTotalMAE");
    const countEl = document.getElementById("liveActiveBasketsCount");
    const pairsCountEl = document.getElementById("liveActivePairsCount");
    const badgeTab = document.getElementById("livePortfoliosBadge");
    const timerBadge = document.getElementById("liveElapsedTimer");

    const isLive = !!session.is_in_progress;

    // Status text, pill e timer
    const statusTextEl = document.querySelector(".status-live-text");
    const timeInfoEl = document.getElementById("liveSessionTimeInfo");
    const pulseRing = document.querySelector(".pulse-ring-live");
    const kpiTitleEl = document.querySelector(".live-kpi-card.highlight .kpi-title");

    if (statusTextEl) {
        statusTextEl.textContent = isLive ? "🔴 PREGÃO DA MADRUGADA AO VIVO" : "🟢 SESSÃO ENCERRADA ÀS 08H00 BRT";
    }
    if (timeInfoEl) {
        timeInfoEl.textContent = session.session_info_str || (isLive ? "📅 Sessão Ao Vivo | Início: 21h00 ➔ Encerramento: 08h00 BRT" : "✅ Sessão da Madrugada Concluída às 08h00 BRT | Próxima Abertura às 21h00 BRT");
    }
    if (timerBadge) {
        timerBadge.textContent = isLive ? `⏱️ Em andamento (${session.time_remaining_str || '21h➔08h'})` : `⏳ Fechado às 08h00 | Próxima às 21h00 (${session.next_session_in || ''})`;
        timerBadge.style.background = isLive ? "rgba(255, 51, 75, 0.2)" : "rgba(0, 230, 118, 0.15)";
        timerBadge.style.color = isLive ? "#FF334B" : "#00E676";
        timerBadge.style.borderColor = isLive ? "rgba(255, 51, 75, 0.4)" : "rgba(0, 230, 118, 0.4)";
    }
    if (badgeTab) {
        badgeTab.textContent = isLive ? `${portfolios.length} Cestas Ativas` : `Fechada às 08h (${portfolios.length} Cestas)`;
        badgeTab.style.background = isLive ? "rgba(255, 51, 75, 0.2)" : "rgba(0, 230, 118, 0.15)";
        badgeTab.style.color = isLive ? "#FF334B" : "#00E676";
    }
    if (pulseRing) {
        pulseRing.style.borderColor = isLive ? "var(--color-red)" : "var(--color-green)";
    }
    if (kpiTitleEl) {
        kpiTitleEl.textContent = isLive ? "PnL Flutuante em Tempo Real" : "Resultado Consolidado da Noite (08h00)";
    }

    if (pnlEl) {
        pnlEl.textContent = (totalPnL >= 0 ? "+$" : "-$") + Math.abs(totalPnL).toFixed(2);
        pnlEl.className = "kpi-value " + (totalPnL >= 0 ? "positive" : "negative");
    }
    if (pipsEl) pipsEl.textContent = `${(totalPips >= 0 ? "+" : "") + totalPips.toFixed(1)} pips ${isLive ? 'flutuantes' : 'realizados'}`;
    if (mfeEl) mfeEl.textContent = `+$${(session.mfe_usd || 0).toFixed(2)}`;
    if (maeEl) maeEl.textContent = `-$${Math.abs(session.mae_usd || 0).toFixed(2)}`;
    if (countEl) countEl.textContent = `${portfolios.length} Cestas`;
    if (pairsCountEl) pairsCountEl.textContent = `${totalPairsCount} pares ${isLive ? 'operados' : 'encerrados'}`;

    // 1. Renderizar Cards de Cestas Ativas
    const basketsContainer = document.getElementById("liveBasketsCardsContainer");
    if (basketsContainer) {
        if (portfolios.length === 0) {
            basketsContainer.innerHTML = `
                <div style="grid-column: 1 / -1; padding: 20px; background: #0C101A; border-radius: 8px; color: var(--text-muted); text-align: center;">
                    🛡️ Nenhuma cesta atingiu 4+ TFs com ciclo válido às 21h00. Mercado sem alinhamento direcional (Preservação de Capital).
                </div>
            `;
        } else {
            basketsContainer.innerHTML = portfolios.map(port => {
                const pnl = port.pnl_usd || 0;
                const isPos = pnl >= 0;
                return `
                    <div class="live-basket-card">
                        <div style="display: flex; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 20px;">${port.flag || '🏳️'}</span>
                                <div>
                                    <span style="font-weight: 800; font-family: var(--font-mono); color: #FFF; font-size: 13px;">Cesta ${port.currency}</span>
                                    <span class="region-badge ${port.bias === 'BUY' ? 'green' : 'red'}" style="margin-left: 6px; font-size: 9px; padding: 1px 5px;">${port.bias === 'BUY' ? 'COMPRA' : 'VENDA'}</span>
                                </div>
                            </div>
                            <div class="leds-container">
                                <span class="led-dot ${port.leds?.MN1 || 'yellow'}" title="MN1"></span>
                                <span class="led-dot ${port.leds?.W1 || 'yellow'}" title="W1"></span>
                                <span class="led-dot ${port.leds?.D1 || 'yellow'}" title="D1"></span>
                                <span class="led-dot ${port.leds?.H4 || 'yellow'}" title="H4"></span>
                                <span class="led-dot ${port.leds?.H1 || 'yellow'}" title="H1"></span>
                            </div>
                        </div>
                        <div style="font-size: 10.5px; color: var(--text-muted);">
                            📌 <em>${port.reason || 'Confluência Multi-TF'}</em>
                        </div>
                        <div style="display: flex; align-items: center; justify-content: space-between; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.06); font-family: var(--font-mono); font-size: 11px;">
                            <span>MFE: <strong style="color: var(--color-green);">+$${(port.mfe_usd||0).toFixed(2)}</strong></span>
                            <span>MAE: <strong style="color: var(--color-red);">-$${Math.abs(port.mae_usd||0).toFixed(2)}</strong></span>
                            <span style="font-weight: 800; font-size: 13px; color: ${isPos ? 'var(--color-green)' : 'var(--color-red)'};">
                                ${(isPos ? "+$" : "-$") + Math.abs(pnl).toFixed(2)}
                            </span>
                        </div>
                    </div>
                `;
            }).join("");
        }
    }

    // 2. Renderizar Tabela Live dos 28 Pares
    const tbody = document.getElementById("livePairsTableBody");
    if (tbody) {
        const allLivePairs = [];
        portfolios.forEach(port => {
            (port.pairs || []).forEach(p => {
                allLivePairs.push({ ...p, basket: port.currency, basketFlag: port.flag });
            });
        });

        if (allLivePairs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="10" class="loading-cell">Nenhum par em operação ativa nesta sessão.</td></tr>`;
        } else {
            tbody.innerHTML = allLivePairs.map(p => {
                const pnl = p.pnl_usd || 0;
                const pips = p.pips || 0;
                const isPos = pnl >= 0;
                const pnlClass = isPos ? "positive" : "negative";
                const currPrice = p.current_price || p.exit_price || p.entry_price;

                return `
                    <tr>
                        <td style="font-family: var(--font-mono); font-weight: 800; color: #FFF;">${p.pair}</td>
                        <td>
                            <span style="font-family: var(--font-mono); font-weight: 700; color: var(--color-cyan);">
                                ${p.basketFlag} ${p.basket}
                            </span>
                        </td>
                        <td>
                            <span class="region-badge ${p.action === 'BUY' ? 'green' : 'red'}" style="font-size: 9px; padding: 1px 6px;">
                                ${p.action}
                            </span>
                        </td>
                        <td class="text-right" style="font-family: var(--font-mono);">${p.entry_price.toFixed(5)}</td>
                        <td class="text-right" style="font-family: var(--font-mono); font-weight: 700; color: #FFF;">${Number(currPrice).toFixed(5)}</td>
                        <td class="score-cell positive text-right">+${(p.mfe_usd || 0).toFixed(2)}</td>
                        <td class="score-cell negative text-right">-${Math.abs(p.mae_usd || 0).toFixed(2)}</td>
                        <td class="score-cell ${pnlClass} text-right">${(pips >= 0 ? "+" : "") + pips.toFixed(1)}p</td>
                        <td class="score-cell ${pnlClass} text-right" style="font-weight: 800; font-size: 13px;">
                            ${(isPos ? "+$" : "-$") + Math.abs(pnl).toFixed(2)}
                        </td>
                        <td class="text-center">
                            <span class="signal-pill ${isPos ? 'buy' : 'sell'}" style="font-size: 9px; padding: 1px 6px;">
                                ${isPos ? 'LUCRO' : 'DRAWDOWN'}
                            </span>
                        </td>
                    </tr>
                `;
            }).join("");
        }
    }
}

// ==========================================================================
// ABA 2: AUDITORIA DE SESSÕES (MESTRE-DETALHE)
// ==========================================================================

function renderAuditTab(data) {
    if (!data) return;
    const sessions = data.sessions || [];

    const countEl = document.getElementById("auditSessionsCount");
    if (countEl) countEl.textContent = `${sessions.length} Sessões`;

    const sidebarList = document.getElementById("auditSessionsList");
    if (!sidebarList) return;

    if (sessions.length === 0) {
        sidebarList.innerHTML = `<div style="padding: 16px; color: var(--text-muted); text-align: center;">Nenhuma sessão encontrada.</div>`;
        return;
    }

    sidebarList.innerHTML = sessions.map((sess, idx) => {
        const isInProgress = sess.status === "EM ANDAMENTO" || sess.is_in_progress === true;
        const isNeut = sess.portfolios_count === 0 && !isInProgress;
        const isWin = sess.total_pnl_usd >= 0;
        const isSelected = state.auditSelectedSession && state.auditSelectedSession.date === sess.date;

        let statusBadge = isNeut ? '🛡️ NEUTRO' : (isInProgress ? '🟡 AO VIVO' : (isWin ? '✅ GANHO' : '❌ PERDA'));
        let statusStyle = isInProgress ? 'color: #FFD600; background: rgba(255,214,0,0.15);' : (isNeut ? 'color: #94A3B8;' : (isWin ? 'color: #00FF88; background: rgba(0,255,136,0.12);' : 'color: #FF334B; background: rgba(255,51,75,0.12);'));

        const pnlStr = (sess.total_pnl_usd >= 0 ? "+$" : "-$") + Math.abs(sess.total_pnl_usd || 0).toFixed(2);
        const pnlColor = isNeut ? '#94A3B8' : (sess.total_pnl_usd >= 0 ? 'var(--color-green)' : 'var(--color-red)');

        const flags = (sess.portfolios || []).map(p => p.flag).join(" ");

        return `
            <div class="session-card-item ${isSelected ? 'active' : ''}" data-sidx="${idx}" onclick="selectAuditSession(${idx})">
                <div class="session-card-top">
                    <span class="session-card-date">📅 ${sess.date}</span>
                    <span style="font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px; ${statusStyle}">
                        ${statusBadge}
                    </span>
                </div>
                <div class="session-card-bottom">
                    <span style="color: var(--text-muted); font-size: 11px;">${flags || 'Sem Cestas'}</span>
                    <span style="font-weight: 800; font-size: 12px; color: ${pnlColor};">${pnlStr}</span>
                </div>
            </div>
        `;
    }).join("");

    // Selecionar a primeira sessão se nenhuma estiver selecionada
    if (!state.auditSelectedSession && sessions.length > 0) {
        selectAuditSession(0);
    } else if (state.auditSelectedSession) {
        const found = sessions.find(s => s.date === state.auditSelectedSession.date);
        if (found) renderAuditDetailPanel(found);
    }
}

window.selectAuditSession = function(index) {
    if (!state.trackRecordData || !state.trackRecordData.sessions) return;
    const sess = state.trackRecordData.sessions[index];
    if (!sess) return;

    state.auditSelectedSession = sess;
    document.querySelectorAll(".session-card-item").forEach((card, idx) => {
        card.classList.toggle("active", idx === index);
    });

    renderAuditDetailPanel(sess);
};

function renderAuditDetailPanel(sess) {
    const panel = document.getElementById("auditDetailPanel");
    if (!panel || !sess) return;

    const isInProgress = sess.status === "EM ANDAMENTO" || sess.is_in_progress === true;
    const isNeut = sess.portfolios_count === 0 && !isInProgress;
    const isWin = sess.total_pnl_usd >= 0;
    const pnl = sess.total_pnl_usd || 0;
    const pips = sess.total_pips || 0;
    const pnlColor = isNeut ? '#94A3B8' : (pnl >= 0 ? 'var(--color-green)' : 'var(--color-red)');

    const portfolios = sess.portfolios || [];

    panel.innerHTML = `
        <!-- HEADER DA SESSÃO SELECIONADA -->
        <div style="display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); flex-wrap: wrap; gap: 10px;">
            <div>
                <h4 style="font-family: var(--font-display); font-size: 16px; font-weight: 800; color: #FFF;">
                    📅 Sessão de ${sess.date}
                </h4>
                <span style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);">
                    Horário de Execução: 21h00 ➔ 08h00 Brasília | Duração: 11 horas
                </span>
            </div>
            <div style="display: flex; align-items: center; gap: 16px; font-family: var(--font-mono);">
                <div>
                    <span style="font-size: 10px; color: var(--text-muted); display: block;">MFE (Pico):</span>
                    <span style="font-weight: 800; color: var(--color-green);">+$${(sess.mfe_usd||0).toFixed(2)}</span>
                </div>
                <div>
                    <span style="font-size: 10px; color: var(--text-muted); display: block;">MAE (Drawdown):</span>
                    <span style="font-weight: 800; color: var(--color-red);">-$${Math.abs(sess.mae_usd||0).toFixed(2)}</span>
                </div>
                <div>
                    <span style="font-size: 10px; color: var(--text-muted); display: block;">Resultado Pips:</span>
                    <span style="font-weight: 800; color: #FFF;">${(pips >= 0 ? "+" : "") + pips.toFixed(1)}p</span>
                </div>
                <div style="background: rgba(255,255,255,0.05); padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-color);">
                    <span style="font-size: 10px; color: var(--text-muted); display: block;">Lucro Líquido:</span>
                    <span style="font-size: 16px; font-weight: 900; color: ${pnlColor};">
                        ${(pnl >= 0 ? "+$" : "-$") + Math.abs(pnl).toFixed(2)}
                    </span>
                </div>
            </div>
        </div>

        <!-- TESE INSTITUCIONAL DE DISPARO -->
        ${isNeut ? `
            <div style="padding: 14px; background: #101624; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; font-size: 12px; color: var(--text-secondary);">
                🛡️ <strong>Sessão Neutra com Proteção Total de Capital:</strong> Nenhuma moeda atingiu confluência em 4+ timeframes acompanhados de ciclo institucional válido às 21h00. O robô permaneceu fora do mercado com 0 trades.
            </div>
        ` : `
            <div style="display: flex; flex-direction: column; gap: 8px;">
                <span style="font-weight: 700; font-family: var(--font-display); color: var(--color-cyan); font-size: 12px;">
                    ⚡ Diagnóstico & Teses de Disparo Institucional (21h00):
                </span>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px;">
                    ${portfolios.map(p => `
                        <div style="background: #111728; border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 10px 12px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                <span style="font-weight: 800; font-family: var(--font-mono); color: #FFF; font-size: 12px;">${p.flag} ${p.bias_label}</span>
                                <div class="leds-container">
                                    <span class="led-dot ${p.leds?.MN1 || 'yellow'}" title="MN1"></span>
                                    <span class="led-dot ${p.leds?.W1 || 'yellow'}" title="W1"></span>
                                    <span class="led-dot ${p.leds?.D1 || 'yellow'}" title="D1"></span>
                                    <span class="led-dot ${p.leds?.H4 || 'yellow'}" title="H4"></span>
                                    <span class="led-dot ${p.leds?.H1 || 'yellow'}" title="H1"></span>
                                </div>
                            </div>
                            <span style="font-size: 11px; color: var(--text-muted); display: block;"><em>${p.reason}</em></span>
                            <div style="margin-top: 6px; font-family: var(--font-mono); font-size: 11px; display: flex; justify-content: space-between;">
                                <span>Pico (MFE): <strong style="color: var(--color-green);">+$${(p.mfe_usd||0).toFixed(2)}</strong></span>
                                <span>Saldo: <strong style="color: ${p.pnl_usd >= 0 ? 'var(--color-green)' : 'var(--color-red)'};">${(p.pnl_usd >= 0 ? "+$" : "-$") + Math.abs(p.pnl_usd).toFixed(2)}</strong></span>
                            </div>
                        </div>
                    `).join("")}
                </div>
            </div>
        `}

        <!-- DUAL AUDIT CHARTS FOR THIS SESSION -->
        ${!isNeut ? `
            <div class="track-dual-charts-grid" style="grid-template-columns: 1fr 1fr; gap: 12px;">
                <div class="equity-chart-wrapper">
                    <div class="equity-chart-header">
                        <span style="font-weight: 700; font-family: var(--font-display); color: #FFF; font-size: 11.5px;">📈 Curva de Capital Intraday das 11 Horas</span>
                        <span style="font-size: 9.5px; color: var(--text-muted);">Evolução das 21h às 08h</span>
                    </div>
                    <div style="height: 160px; width: 100%; position: relative;">
                        <canvas id="auditIntradayCanvas"></canvas>
                    </div>
                </div>
                <div class="equity-chart-wrapper">
                    <div class="equity-chart-header">
                        <span style="font-weight: 700; font-family: var(--font-display); color: var(--color-cyan); font-size: 11.5px;">⚡ Trajetória do CSS H1 e H4</span>
                        <div class="chart-legend-hints" style="font-size: 9px;">
                            <span class="legend-hint green-hint">+0.20</span>
                            <span class="legend-hint red-hint">-0.20</span>
                        </div>
                    </div>
                    <div style="height: 160px; width: 100%; position: relative;">
                        <canvas id="auditCssCanvas"></canvas>
                    </div>
                </div>
            </div>
        ` : ''}

        <!-- TABELA DE AUDITORIA DETALHADA DOS 7 PARES POR CESTA -->
        ${!isNeut ? `
            <div style="display: flex; flex-direction: column; gap: 8px;">
                <span style="font-weight: 700; font-family: var(--font-display); color: #FFF; font-size: 12.5px;">
                    📋 Auditoria Completa dos Pares Operados nesta Sessão:
                </span>
                <div class="table-responsive" style="border: 1px solid var(--border-color); border-radius: 8px;">
                    <table class="track-table">
                        <thead>
                            <tr>
                                <th>PAR</th>
                                <th>CESTA</th>
                                <th>AÇÃO</th>
                                <th class="text-right">ENTRADA (21h00)</th>
                                <th class="text-right">SAÍDA (08h00)</th>
                                <th class="text-right">MFE (PICO)</th>
                                <th class="text-right">MAE (DD MÁX)</th>
                                <th class="text-right">PIPS</th>
                                <th class="text-right">RESULTADO ($)</th>
                                <th class="text-center">STATUS</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${portfolios.flatMap(port => (port.pairs || []).map(p => {
                                const isPos = p.pnl_usd >= 0;
                                const pnlClass = isPos ? "positive" : "negative";
                                return `
                                    <tr>
                                        <td style="font-family: var(--font-mono); font-weight: 800; color: #FFF;">${p.pair}</td>
                                        <td><span style="font-family: var(--font-mono); font-weight: 700; color: var(--color-cyan);">${port.flag} ${port.currency}</span></td>
                                        <td><span class="region-badge ${p.action === 'BUY' ? 'green' : 'red'}" style="font-size: 9px; padding: 1px 6px;">${p.action}</span></td>
                                        <td class="text-right" style="font-family: var(--font-mono);">${p.entry_price.toFixed(5)}</td>
                                        <td class="text-right" style="font-family: var(--font-mono); color: #FFF;">${p.exit_price.toFixed(5)}</td>
                                        <td class="score-cell positive text-right">+${(p.mfe_usd||0).toFixed(2)}</td>
                                        <td class="score-cell negative text-right">-${Math.abs(p.mae_usd||0).toFixed(2)}</td>
                                        <td class="score-cell ${pnlClass} text-right">${(p.pips >= 0 ? "+" : "") + p.pips.toFixed(1)}p</td>
                                        <td class="score-cell ${pnlClass} text-right" style="font-weight: 800;">
                                            ${(isPos ? "+$" : "-$") + Math.abs(p.pnl_usd).toFixed(2)}
                                        </td>
                                        <td class="text-center">
                                            <span class="signal-pill ${isPos ? 'buy' : 'sell'}" style="font-size: 9px; padding: 1px 6px;">
                                                ${isPos ? 'WIN' : 'LOSS'}
                                            </span>
                                        </td>
                                    </tr>
                                `;
                            })).join("")}
                        </tbody>
                    </table>
                </div>
            </div>
        ` : ''}
    `;

    // Desenhar gráficos da sessão no detalhe
    if (!isNeut) {
        setTimeout(() => {
            renderAuditIntradayCanvas(sess);
            renderAuditCssCanvas(sess);
        }, 60);
    }
}

function renderAuditIntradayCanvas(sess) {
    const canvas = document.getElementById("auditIntradayCanvas");
    if (!canvas || !sess) return;

    const pnlCurve = sess.intraday_pnl_curve || [0.0];
    const hours = sess.intraday_hours || ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"];
    const numPoints = pnlCurve.length;

    const ctx = canvas.getContext("2d");
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 20, bottom: 20, left: 45, right: 25 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    let minVal = Math.min(...pnlCurve, sess.mae_usd || 0, -1.0);
    let maxVal = Math.max(...pnlCurve, sess.mfe_usd || 0, 2.0);
    const range = (maxVal - minVal) || 1.0;
    minVal -= range * 0.1;
    maxVal += range * 0.1;

    const getX = (i) => padding.left + (i / (numPoints - 1)) * chartW;
    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));

    const y0 = getY(0.0);
    ctx.save();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(padding.left, y0);
    ctx.lineTo(width - padding.right, y0);
    ctx.stroke();
    ctx.restore();

    const lastVal = pnlCurve[pnlCurve.length - 1];
    const lineColor = lastVal >= 0 ? "#00E676" : "#FF334B";
    const grad = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
    grad.addColorStop(0, lastVal >= 0 ? "rgba(0, 230, 118, 0.25)" : "rgba(255, 51, 75, 0.25)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0.0)");

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(getX(0), y0);
    for (let i = 0; i < numPoints; i++) ctx.lineTo(getX(i), getY(pnlCurve[i]));
    ctx.lineTo(getX(numPoints - 1), y0);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2.0;
    for (let i = 0; i < numPoints; i++) {
        const x = getX(i);
        const y = getY(pnlCurve[i]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();

    // Rótulos do Eixo
    ctx.fillStyle = "#94A3B8";
    ctx.font = "8.5px JetBrains Mono";
    ctx.textAlign = "center";
    for (let i = 0; i < numPoints; i += 3) {
        ctx.fillText(hours[i] || "", getX(i), height - 4);
    }
    ctx.textAlign = "right";
    ctx.fillText(`$${maxVal.toFixed(1)}`, padding.left - 4, padding.top + 4);
    ctx.fillText(`$${minVal.toFixed(1)}`, padding.left - 4, height - padding.bottom);
}

function renderAuditCssCanvas(sess) {
    const canvas = document.getElementById("auditCssCanvas");
    if (!canvas || !sess) return;

    const portfolios = sess.portfolios || [];
    if (portfolios.length === 0) return;

    const port = portfolios[0];
    const h1Curve = port.css_h1_curve || [];
    const h4Curve = port.css_h4_curve || [];
    const hours = sess.intraday_hours || ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"];

    if (h1Curve.length === 0) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 20, bottom: 20, left: 40, right: 20 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    let allVals = [...h1Curve, ...h4Curve, 0.25, -0.25];
    let minVal = Math.min(...allVals) - 0.05;
    let maxVal = Math.max(...allVals) + 0.05;

    const getX = (i) => padding.left + (i / (h1Curve.length - 1)) * chartW;
    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));

    // +0.20
    const yGreen = getY(0.20);
    ctx.save();
    ctx.strokeStyle = "rgba(0, 230, 118, 0.5)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(padding.left, yGreen);
    ctx.lineTo(width - padding.right, yGreen);
    ctx.stroke();
    ctx.restore();

    // -0.20
    const yRed = getY(-0.20);
    ctx.save();
    ctx.strokeStyle = "rgba(255, 51, 75, 0.5)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(padding.left, yRed);
    ctx.lineTo(width - padding.right, yRed);
    ctx.stroke();
    ctx.restore();

    // H4 Dourado
    if (h4Curve.length > 0) {
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = "#FFD600";
        ctx.lineWidth = 1.6;
        for (let i = 0; i < h4Curve.length; i++) {
            const x = getX(i);
            const y = getY(h4Curve[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.restore();
    }

    // H1 Ciano
    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = "#00E5FF";
    ctx.lineWidth = 2.0;
    for (let i = 0; i < h1Curve.length; i++) {
        const x = getX(i);
        const y = getY(h1Curve[i]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = "#94A3B8";
    ctx.font = "8.5px JetBrains Mono";
    ctx.textAlign = "center";
    for (let i = 0; i < h1Curve.length; i += 3) {
        ctx.fillText(hours[i] || "", getX(i), height - 4);
    }
}

// ==========================================================================
// ABA 3: CURVA DE CAPITAL & ANALYTICS MACRO
// ==========================================================================

function renderAnalyticsTab(data) {
    if (!data) return;
    const summary = data.summary || {};
    const sessions = data.sessions || [];

    const pnlEl = document.getElementById("trackTotalPnL");
    const pipsEl = document.getElementById("trackTotalPips");
    const wrEl = document.getElementById("trackWinRate");
    const wlEl = document.getElementById("trackWinLossCount");
    const pfEl = document.getElementById("trackProfitFactor");
    const activeSessEl = document.getElementById("trackActiveSessions");
    const portEl = document.getElementById("trackTotalPortfolios");
    const neutEl = document.getElementById("trackNeutralSessions");
    const avgMFEEl = document.getElementById("trackAvgMFE");
    const avgMAEEl = document.getElementById("trackAvgMAE");

    const totalPnL = summary.total_pnl_usd || 0;
    if (pnlEl) {
        pnlEl.textContent = (totalPnL >= 0 ? "+$" : "-$") + Math.abs(totalPnL).toFixed(2);
        pnlEl.className = "metric-value " + (totalPnL >= 0 ? "highlight-green" : "highlight-red");
    }
    if (pipsEl) pipsEl.textContent = `${(summary.total_pips >= 0 ? "+" : "") + (summary.total_pips || 0).toFixed(1)} pips`;
    if (wrEl) wrEl.textContent = `${(summary.win_rate || 0).toFixed(1)}%`;
    if (wlEl) wlEl.textContent = `${summary.win_sessions || 0} Wins / ${summary.loss_sessions || 0} Losses`;
    if (pfEl) pfEl.textContent = (summary.profit_factor || 0).toFixed(2);
    if (activeSessEl) activeSessEl.textContent = `${summary.active_sessions || 0} Sessões Ativas`;
    if (portEl) portEl.textContent = `${summary.total_portfolios || 0} Cestas Operadas`;
    if (neutEl) neutEl.textContent = `${summary.neutral_sessions || 0} Sessões Neutras`;

    const activeS = sessions.filter(s => s.portfolios_count > 0);
    if (activeS.length > 0) {
        const avgMFE = activeS.reduce((acc, s) => acc + (s.mfe_usd || 0), 0) / activeS.length;
        const avgMAE = activeS.reduce((acc, s) => acc + (s.mae_usd || 0), 0) / activeS.length;
        if (avgMFEEl) avgMFEEl.textContent = `+$${avgMFE.toFixed(2)}`;
        if (avgMAEEl) avgMAEEl.textContent = `-$${Math.abs(avgMAE).toFixed(2)}`;
    }

    setTimeout(() => {
        renderGlobalEquityCurve(data.equity_curve || []);
        renderCurrencyBreakdownTable(sessions);
    }, 60);
}

function renderGlobalEquityCurve(curveData) {
    const canvas = document.getElementById("equityCanvasGlobal");
    if (!canvas || curveData.length < 2) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 25, bottom: 30, left: 60, right: 30 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const equities = curveData.map(d => d.equity);
    let minVal = Math.min(...equities, 0.0);
    let maxVal = Math.max(...equities, 10.0);
    const range = (maxVal - minVal) || 1.0;
    minVal -= range * 0.08;
    maxVal += range * 0.08;

    const getX = (i) => padding.left + (i / (curveData.length - 1)) * chartW;
    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));

    const y0 = getY(0.0);
    ctx.save();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, y0);
    ctx.lineTo(width - padding.right, y0);
    ctx.stroke();
    ctx.restore();

    const lastEquity = equities[equities.length - 1];
    const lineColor = lastEquity >= 0 ? "#00E676" : "#FF334B";
    const grad = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
    grad.addColorStop(0, lastEquity >= 0 ? "rgba(0, 230, 118, 0.28)" : "rgba(255, 51, 75, 0.28)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0.0)");

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(getX(0), y0);
    for (let i = 0; i < curveData.length; i++) ctx.lineTo(getX(i), getY(curveData[i].equity));
    ctx.lineTo(getX(curveData.length - 1), y0);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2.4;
    ctx.shadowColor = lineColor;
    ctx.shadowBlur = 6;
    for (let i = 0; i < curveData.length; i++) {
        const x = getX(i);
        const y = getY(curveData[i].equity);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = "#94A3B8";
    ctx.font = "10px JetBrains Mono";
    ctx.textAlign = "right";
    ctx.fillText(`$${maxVal.toFixed(1)}`, padding.left - 8, padding.top + 5);
    ctx.fillText(`$${minVal.toFixed(1)}`, padding.left - 8, height - padding.bottom);
    ctx.fillText(`$0`, padding.left - 8, y0 + 3);
}

function renderCurrencyBreakdownTable(sessions) {
    const tbody = document.getElementById("currencyAnalyticsTableBody");
    if (!tbody) return;

    const stats = {};
    CURRENCIES.forEach(c => {
        stats[c] = { count: 0, pnl: 0.0, pips: 0.0, mfes: [], maes: [] };
    });

    sessions.forEach(sess => {
        (sess.portfolios || []).forEach(port => {
            const c = port.currency;
            if (stats[c]) {
                stats[c].count += 1;
                stats[c].pnl += (port.pnl_usd || 0);
                stats[c].pips += (port.pips || 0);
                stats[c].mfes.push(port.mfe_usd || 0);
                stats[c].maes.push(port.mae_usd || 0);
            }
        });
    });

    tbody.innerHTML = CURRENCIES.map(c => {
        const st = stats[c];
        const avgMfe = st.mfes.length > 0 ? (st.mfes.reduce((a,b)=>a+b, 0) / st.mfes.length) : 0;
        const avgMae = st.maes.length > 0 ? (st.maes.reduce((a,b)=>a+b, 0) / st.maes.length) : 0;
        const isPos = st.pnl >= 0;
        const pnlClass = st.count === 0 ? "neutral" : (isPos ? "positive" : "negative");

        return `
            <tr>
                <td style="font-family: var(--font-mono); font-weight: 800; color: #FFF;">
                    ${CCY_FLAGS[c] || ''} ${c}
                </td>
                <td>${st.count} Cestas</td>
                <td class="score-cell ${pnlClass} text-right" style="font-weight: 800; font-size: 13px;">
                    ${st.count === 0 ? '--' : ((isPos ? "+$" : "-$") + Math.abs(st.pnl).toFixed(2))}
                </td>
                <td class="score-cell ${pnlClass} text-right">
                    ${st.count === 0 ? '--' : ((st.pips >= 0 ? "+" : "") + st.pips.toFixed(1) + "p")}
                </td>
                <td class="score-cell positive text-right">
                    ${st.count === 0 ? '--' : `+$${avgMfe.toFixed(2)}`}
                </td>
                <td class="score-cell negative text-right">
                    ${st.count === 0 ? '--' : `-$${Math.abs(avgMae).toFixed(2)}`}
                </td>
                <td class="text-center">
                    <span class="signal-pill ${st.count === 0 ? 'neutral' : (isPos ? 'buy' : 'sell')}" style="font-size: 9px; padding: 1px 6px;">
                        ${st.count === 0 ? 'SEM TRADES' : (isPos ? 'LUCRO' : 'PREJUÍZO')}
                    </span>
                </td>
            </tr>
        `;
    }).join("");
}

