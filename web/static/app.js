/**
 * CSS PRO INSTITUTIONAL WEB PLATFORM — ENGINE JAVASCRIPT
 * Gerencia renderização gráfica em Canvas de alta performance, Badges laterais dinâmicos,
 * modo Split-View, sincronização em tempo real e modais analíticos.
 */

// Estado Global da Aplicação
const state = {
    currencies: ["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"],
    activeCurrencies: new Set(["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"]),
    chartType: "flow", // "flow" (CSS Flow) | "matrix" (Matriz de Pares)
    matrixActiveCcy: "USD",
    chartMatrixCcys: {
        chart1: "USD",
        chart2: "EUR",
        chart3: "GBP"
    },
    viewMode: "single", // 'single', 'split2', 'split3'
    chartTFs: {
        chart1: "H1",
        chart2: "H4",
        chart3: "D1"
    },
    data: null,
    engineMode: localStorage.getItem("css_engine_mode") || "standard",
    pairsFilter: "ALICATE",
    pairsSearch: "",
    selectedDeepDive: "AUD",
    matrixActiveCcy: "USD",
    matrixActiveTF: "H1",
    crossoversActiveTF: "H1",
    crossoversActiveTab: "cross-live",
    crossoversCurrencyFilter: "ALL",
    isCrossoversModalOpen: false,
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
    EUR: "#2ECC71",
    GBP: "#3872FF",
    CHF: "#00E5FF",
    JPY: "#9932CC",
    AUD: "#FF8C00",
    CAD: "#8B0000",
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

    // 2.1 Alternador do Gráfico Principal: CSS Flow vs Matriz de Pares
    const btnSwitchFlow = document.getElementById("btnSwitchFlow");
    const btnSwitchMatrix = document.getElementById("btnSwitchMatrix");

    function updateChartTypeUI(type) {
        state.chartType = type;
        if (btnSwitchFlow && btnSwitchMatrix) {
            btnSwitchFlow.classList.toggle("active", type === "flow");
            btnSwitchMatrix.classList.toggle("active", type === "matrix");
        }

        const isMatrix = type === "matrix";
        const currencyToggles = document.getElementById("currencyToggles");
        if (currencyToggles) {
            // Em modo matriz, esconde os toggles gerais de moeda do header para deixar a tela 100% limpa e sem duplicidade!
            if (isMatrix) currencyToggles.classList.add("hidden");
            else currencyToggles.classList.remove("hidden");
        }

        for (let i = 1; i <= 3; i++) {
            const chartKey = `chart${i}`;
            const ccy = state.chartMatrixCcys[chartKey] || "USD";
            const titleEl = document.getElementById(`chart${i}Title`);
            const iconEl = document.getElementById(`chart${i}Icon`);
            const matrixWrapper = document.getElementById(`matrixSelectWrapper${i}`);
            const selectEl = document.getElementById(`matrixCcySelect${i}`);

            if (titleEl) titleEl.textContent = isMatrix ? `Matriz de Pares (${ccy})` : "CSS Flow";
            if (iconEl) iconEl.textContent = isMatrix ? "🕸️" : "📈";
            if (matrixWrapper) {
                if (isMatrix) matrixWrapper.classList.remove("hidden");
                else matrixWrapper.classList.add("hidden");
            }
            if (selectEl) {
                selectEl.value = ccy;
            }
        }

        renderAllCharts();
    }

    if (btnSwitchFlow) {
        btnSwitchFlow.addEventListener("click", () => updateChartTypeUI("flow"));
    }
    if (btnSwitchMatrix) {
        btnSwitchMatrix.addEventListener("click", () => updateChartTypeUI("matrix"));
    }

    // 2.2 Seletor Dropdown de Moeda da Matriz (independente em cada gráfico)
    document.querySelectorAll(".matrix-ccy-select").forEach(sel => {
        sel.addEventListener("change", (e) => {
            const chartKey = sel.dataset.chart;
            const ccy = sel.value;
            state.chartMatrixCcys[chartKey] = ccy;
            if (chartKey === "chart1") state.matrixActiveCcy = ccy;

            const titleEl = document.getElementById(`${chartKey}Title`);
            if (titleEl && state.chartType === "matrix") {
                titleEl.textContent = `Matriz de Pares (${ccy})`;
            }

            renderChart(chartKey);
        });
    });

    // 3. Timeframe Tabs independentes em cada gráfico
    setupTFTabs("chart1TFTabs", "chart1");
    setupTFTabs("chart2TFTabs", "chart2");
    setupTFTabs("chart3TFTabs", "chart3");

    // 4. Modo Gauss Toggle (NWE Kernel Regression)
    setupGaussModeToggle();

    // 5. Botão de Refresh Manual
    const btnRefresh = document.getElementById("btnRefresh");
    if (btnRefresh) {
        btnRefresh.addEventListener("click", async () => {
            const icon = btnRefresh.querySelector(".refresh-icon");
            icon.classList.add("rotating");
            await forceRecalculate();
            setTimeout(() => icon.classList.remove("rotating"), 800);
        });
    }

    // 6. Modais
    setupModals();
    setupMatrixModal();
    setupTrackRecordModal();
    setupCrossoversModal();

    // 7. Redimensionamento de Janela
    window.addEventListener("resize", () => {
        renderAllCharts();
        renderMatrixChart();
        if (state.trackRecordData) {
            if (state.activeTrackTab === 'analytics') renderGlobalEquityCurve(state.trackRecordData.equity_curve || []);
            else if (state.activeTrackTab === 'audit' && state.auditSelectedSession) renderAuditDetailPanel(state.auditSelectedSession);
        }
    });
}

function setupGaussModeToggle() {
    const btn = document.getElementById("btnToggleGaussMode");
    const tag = document.getElementById("gaussModeStateTag");
    if (!btn) return;

    const updateBtnUI = () => {
        const isGauss = state.engineMode === "gauss";
        if (isGauss) {
            btn.classList.add("gauss-active");
            if (tag) tag.textContent = "ON";
            btn.setAttribute("title", "Modo Gauss ATIVADO (Nadaraya-Watson Kernel Regression + Tanh). Clique para voltar ao Modo Padrão.");
        } else {
            btn.classList.remove("gauss-active");
            if (tag) tag.textContent = "OFF";
            btn.setAttribute("title", "Modo Padrão ATIVADO (TMA / LWMA). Clique para ativar o Modo Gauss (Nadaraya-Watson).");
        }
    };

    updateBtnUI();

    btn.addEventListener("click", async () => {
        state.engineMode = state.engineMode === "gauss" ? "standard" : "gauss";
        localStorage.setItem("css_engine_mode", state.engineMode);
        updateBtnUI();
        await fetchData();
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
// BUSCA DE DADOS (API REST & STATIC SNAPSHOT)
// ==========================================================================
async function fetchData() {
    try {
        const modeParam = state.engineMode === "gauss" ? "gauss" : "standard";
        let res = null;
        const isLocalServer = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' || window.location.port === '8050';

        if (isLocalServer) {
            // No localhost: chama a API dinâmica com query ?mode=...
            try {
                res = await fetch(`/api/css/all?mode=${modeParam}&t=${Date.now()}`);
            } catch (e) {
                // Servidor local offline
            }
        }

        // Se estiver no Firebase Hosting (ou se o servidor local estiver offline), busca o arquivo JSON do banco selecionado
        if (!res || !res.ok) {
            const staticPath = state.engineMode === "gauss" ? "/api/css/all_gauss.json" : "/api/css/all_standard.json";
            try {
                res = await fetch(`${staticPath}?t=${Date.now()}`);
            } catch (e) {}
            
            if (!res || !res.ok) {
                // Fallback secundário
                res = await fetch(`/api/css/all.json?t=${Date.now()}`);
            }
        }

        if (!res || !res.ok) throw new Error("Erro ao carregar dados do CSS PRO");
        const json = await res.json();
        state.data = json;
        
        updateHeaderStatus(json);
        renderTable(json.currencies);
        renderAllCharts();
        updateStrongSignalsCount(json.pairs);
        if (typeof renderMatrixChart === "function") renderMatrixChart();

        // Atualizar Badge de Cruzamentos de Score (limite máximo de 8 horas)
        if (json.crossovers) {
            const h1Cross = (json.crossovers.timeframes?.H1?.crossovers || []).filter(c => c.bars_ago <= 8);
            const count = h1Cross.length;
            const badge = document.getElementById("crossoversFreshCount");
            if (badge) badge.textContent = count;
            const tabBadge = document.getElementById("crossoversTabBadge");
            if (tabBadge) tabBadge.textContent = `${count} Recentes (≤8h)`;
            if (state.isCrossoversModalOpen) {
                renderCrossoversModal();
            }
        }
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

    const now = new Date();
    const dayOfWeek = now.getDay(); // 0 = Domingo, 6 = Sábado
    const isWeekend = (dayOfWeek === 6) || (dayOfWeek === 0 && now.getHours() < 18);

    if (isWeekend) {
        if (statusDot) statusDot.className = "status-dot weekend";
        if (statusTitle) statusTitle.textContent = "FECHAMENTO SEXTA";
    } else if (data.mt5_connected) {
        if (statusDot) statusDot.className = "status-dot online";
        if (statusTitle) statusTitle.textContent = "MT5 LIVE";
    } else {
        if (statusDot) statusDot.className = "status-dot offline";
        if (statusTitle) statusTitle.textContent = "CACHE OFFLINE";
    }

    if (data.timestamp && statusTime) {
        statusTime.textContent = data.timestamp.split(" ")[1] || data.timestamp;
    }
}

function updateStrongSignalsCount(pairs) {
    if (!pairs) return;
    const strongCount = pairs.filter(p => p.is_alicate || p.conviction.includes("MÁXIMA") || p.conviction.includes("ALICATE") || p.recommendation.includes("STRONG")).length;
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
    if (!state.data) return;

    const tf = state.chartTFs[chartKey];
    const isMatrix = state.chartType === "matrix";

    if (isMatrix) {
        renderMatrixOnMainChart(chartKey, tf);
        return;
    }

    const chartData = state.data.charts ? state.data.charts[tf] : null;
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

// RENDERIZAÇÃO DA MATRIZ DE PARES DIRETAMENTE NO GRÁFICO PRINCIPAL
function renderMatrixOnMainChart(chartKey, tf) {
    if (!state.data || !state.data.pair_charts) return;

    const allPairCharts = state.data.pair_charts[tf];
    if (!allPairCharts) return;

    const ccy = state.chartMatrixCcys[chartKey] || state.matrixActiveCcy || "USD";
    const canvasId = chartKey === "chart1" ? "cssCanvas1" : chartKey === "chart2" ? "cssCanvas2" : "cssCanvas3";
    const overlayId = chartKey === "chart1" ? "badgesOverlay1" : chartKey === "chart2" ? "badgesOverlay2" : "badgesOverlay3";

    const canvas = document.getElementById(canvasId);
    const overlay = document.getElementById(overlayId);
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

    // Identificar os pares relacionados à moeda selecionada
    const activePairs = [];
    Object.keys(allPairCharts).forEach(sym => {
        if (sym.includes(ccy)) {
            activePairs.push(sym);
        }
    });

    if (activePairs.length === 0) return;

    const times = state.data.charts ? (state.data.charts[tf]?.times || []) : [];
    const numPoints = times.length;
    if (numPoints < 2) return;

    let minVal = -0.05;
    let maxVal = 0.05;

    activePairs.forEach(sym => {
        const arr = allPairCharts[sym] || [];
        minVal = Math.min(minVal, ...arr);
        maxVal = Math.max(maxVal, ...arr);
    });

    minVal -= 0.05;
    maxVal += 0.05;

    const isMobile = window.innerWidth <= 768;
    const isSmall = window.innerWidth <= 480;
    const padding = { 
        top: 25, 
        bottom: 25, 
        left: isSmall ? 8 : 15, 
        right: isSmall ? 85 : (isMobile ? 110 : 180) 
    };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));
    const getX = (idx) => padding.left + (idx / (numPoints - 1)) * chartW;

    drawInstitutionalLevels(ctx, width, getX, getY, minVal, maxVal, padding);
    drawTimeAxis(ctx, width, height, times, getX, padding, tf);

    const lastPoints = [];

    activePairs.forEach(sym => {
        const arr = allPairCharts[sym] || [];
        const base = sym.substring(0, 3);
        const quote = sym.substring(3, 6);
        const otherCcy = base === ccy ? quote : base;
        const color = CCY_COLORS[otherCcy] || "#FFF";
        const flag = CCY_FLAGS[otherCcy] || "🏳️";

        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
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

        const lastVal = arr[arr.length - 1] || 0;
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

    const plotW = width - padding.left - padding.right;
    const numPoints = times.length;

    // 1. Formatação adaptativa baseada na largura real do gráfico (Split 2X/3X vs Único)
    const formatTimeLabel = (tStr) => {
        if (!tStr) return "";
        if (!tStr.includes(" ")) return tStr;

        const [datePart, timePart] = tStr.split(" ");
        const [year, month, day] = datePart.split("-");

        if (tf === "H1" || tf === "H4") {
            // Em telas/gráficos compactos (Split 2X, Split 3X ou telas menores), usar formato curto e limpo
            if (plotW < 520) {
                return `${timePart}`;
            } else if (plotW < 800) {
                return `${day}/${month} ${timePart.slice(0, 5)}`;
            } else {
                return `${timePart.slice(0, 5)} (${day}/${month})`;
            }
        } else if (tf === "D1" || tf === "W1") {
            return `${day}/${month}`;
        } else if (tf === "MN1") {
            return `${month}/${year ? year.slice(2) : ''}`;
        }
        return tStr;
    };

    // 2. Determinar a densidade ideal de labels medindo a largura de texto
    const sampleLabel = formatTimeLabel(times[Math.floor(numPoints / 2)] || "00:00 (00/00)");
    const sampleWidth = ctx.measureText(sampleLabel).width;
    const minGap = 28; // Espaço mínimo livre de 28px entre uma label e outra
    const maxPossibleLabels = Math.max(2, Math.floor(plotW / (sampleWidth + minGap)));
    const step = Math.max(1, Math.ceil((numPoints - 1) / maxPossibleLabels));

    let lastDrawnRight = -9999;

    for (let i = 0; i < numPoints; i += step) {
        const rawTime = times[i];
        if (!rawTime) continue;

        const label = formatTimeLabel(rawTime);
        const x = getX(i);
        const textWidth = ctx.measureText(label).width;
        const labelLeft = x - (textWidth / 2);
        const labelRight = x + (textWidth / 2);

        // Anti-Colisão Estrita: Não desenhar se sobrepõe a label anterior
        if (labelLeft < lastDrawnRight + minGap) {
            continue;
        }

        // Não desenhar cortado fora dos limites do Canvas
        if (labelLeft < 2 || labelRight > width - 2) {
            continue;
        }

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

        // Texto do Eixo Horizontal
        ctx.save();
        ctx.fillStyle = "rgba(148, 163, 184, 0.85)";
        ctx.fillText(label, x, height - 7);
        ctx.restore();

        lastDrawnRight = labelRight;
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

        // 6. CICLO DEVENDO
        const owingCell = document.createElement("td");
        owingCell.className = "owing-cell";
        owingCell.textContent = item.active_h1_triad ? item.active_h1_triad.owing_cycle : "Devendo Alinhamento";

        // 7. AÇÕES (RAIO-X)
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

    // Filtros de pares (Operações Alicate)
    document.querySelectorAll("#pairFilters .filter-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            document.querySelectorAll("#pairFilters .filter-pill").forEach(p => p.classList.remove("active"));
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

// SCREENER DOS 28 PARES (OPERAÇÕES ALICATE)
function renderPairsTable() {
    const tbody = document.getElementById("pairsTableBody");
    if (!tbody || !state.data || !state.data.pairs) return;

    let list = state.data.pairs;

    // Aplicar filtros
    if (state.pairsFilter === "ALICATE") {
        list = list.filter(p => p.is_alicate || p.conviction.includes("ALICATE") || p.recommendation.includes("ALICATE"));
    } else if (state.pairsFilter === "ALICATE_SYNC") {
        list = list.filter(p => p.alicate_status === "SYNC" || p.conviction.includes("TRIPLO") || p.conviction.includes("SINCRONIZADO") || p.recommendation.includes("ALICATE TRIPLO"));
    } else if (state.pairsFilter === "ALICATE_OP") {
        list = list.filter(p => p.alicate_status === "OP" || p.recommendation.includes("ALICATE H4/H1") || p.recommendation.includes("ALICATE OPERACIONAL") || p.conviction.includes("INTRADAY"));
    } else if (state.pairsFilter === "ALICATE_WAIT") {
        list = list.filter(p => p.alicate_status === "WAIT_OP" || p.recommendation.includes("AGUARDAR H1") || p.conviction.includes("TRANSIÇÃO") || p.conviction.includes("AGUARDAR"));
    } else if (state.pairsFilter === "BUY") {
        list = list.filter(p => p.recommendation.includes("BUY") || p.recommendation.includes("COMPRA"));
    } else if (state.pairsFilter === "SELL") {
        list = list.filter(p => p.recommendation.includes("SELL") || p.recommendation.includes("VENDA"));
    } else if (state.pairsFilter === "RECENT_4H") {
        list = list.filter(p => {
            const cross = state.data?.crossovers?.timeframes?.H1?.crossovers?.find(c => c.pair === p.pair);
            const bars = cross ? cross.bars_ago : (p.bars_ago !== undefined ? p.bars_ago : 0);
            return bars <= 4;
        });
    } else if (state.pairsFilter === "BOX") {
        list = list.filter(p => p.recommendation.includes("NEUTRO") || p.recommendation.includes("BOX") || p.conviction.includes("NEUTRA"));
    }

    if (state.pairsSearch) {
        list = list.filter(p => p.pair.includes(state.pairsSearch));
    }

    tbody.innerHTML = "";

    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="loading-cell" style="padding: 30px; text-align: center; color: var(--text-muted);">✂️ Nenhuma operação correspondente ao filtro selecionado.</td></tr>`;
        return;
    }

    list.forEach((item, index) => {
        const tr = document.createElement("tr");

        let recClass = "neutral";
        if (item.badge_type === "ALICATE_SYNC") recClass = "alicate-sync";
        else if (item.badge_type === "ALICATE_WAIT") recClass = "alicate-wait";
        else if (item.badge_type === "ALICATE_OP") recClass = "alicate-op";
        else if (item.badge_type === "STRONG_BUY") recClass = "strong-buy";
        else if (item.badge_type === "BUY") recClass = "buy";
        else if (item.badge_type === "STRONG_SELL") recClass = "strong-sell";
        else if (item.badge_type === "SELL") recClass = "sell";

        let signalTime = item.signal_time;
        let bars = item.bars_ago;

        if (state.data?.crossovers?.timeframes?.H1?.crossovers) {
            const cross = state.data.crossovers.timeframes.H1.crossovers.find(c => c.pair === item.pair);
            if (cross) {
                signalTime = cross.timestamp;
                bars = cross.bars_ago;
            }
        }
        if (!signalTime && state.data?.charts?.H1?.times) {
            const times = state.data.charts.H1.times;
            const barIdx = Math.max(0, times.length - 1 - (bars || 0));
            signalTime = times[barIdx];
        }

        const timeDisplay = signalTime ? signalTime.replace(/.*(\d{2}:\d{2}).*/, '$1') : '18:00';
        const recencyStr = (bars === 0 || bars === undefined) ? '🔥 Barra Atual' : `${bars}h atrás (${bars}b)`;

        tr.innerHTML = `
            <td style="white-space: nowrap;">
                <div style="display: flex; flex-direction: column; gap: 2px;">
                    <span style="font-family: var(--font-mono); font-size: 11.5px; font-weight: 800; color: #FFD700;">
                        🕒 ${timeDisplay}
                    </span>
                    <span style="font-size: 9px; font-weight: 700; color: ${(bars === 0 || bars <= 2) ? 'var(--color-green)' : 'var(--text-muted)'};">
                        ${recencyStr}
                    </span>
                </div>
            </td>
            <td style="color: var(--text-muted); font-family: var(--font-mono);">${index + 1}</td>
            <td>
                <div class="pair-badge-cell">
                    <span>${item.base_flag}${item.quote_flag}</span>
                    <span style="color: #FFFFFF; font-weight: 700;">${item.pair}</span>
                </div>
            </td>
            <td><span class="rec-badge ${recClass}">${item.recommendation}</span></td>
            <td style="font-weight: 700; color: ${item.conviction.includes('MÁXIMA') || item.conviction.includes('ALICATE') ? '#00FF88' : '#94A3B8'}">${item.conviction}</td>
            <td class="score-cell ${item.total_score > 0 ? 'positive' : item.total_score < 0 ? 'negative' : 'neutral'}">
                ${(item.total_score >= 0 ? "+" : "") + item.total_score.toFixed(2)}
            </td>
            <td class="score-cell">${(item.macro_diff >= 0 ? "+" : "") + item.macro_diff.toFixed(2)}</td>
            <td class="score-cell">${(item.op_diff >= 0 ? "+" : "") + item.op_diff.toFixed(2)}</td>
            <td style="font-size: 11.5px; color: var(--text-secondary);">${item.thesis}</td>
            <td>
                <button class="btn-deep-dive" style="padding: 3px 8px; font-size: 10.5px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px;" onclick="openDeepDive('${item.pair}')">
                    <span>🔍</span> <span>Raio-X 3-TF</span>
                </button>
            </td>
        `;

        tbody.appendChild(tr);
    });
}

// RENDERIZADOR DE MINI-GRÁFICOS DOS 5 TIMEFRAMES DO RAIO-X INSTITUCIONAL
function drawTriadMiniChart(canvasId, target, tf) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !state.data || !state.data.charts) return;

    const chartData = state.data.charts[tf];
    if (!chartData || !chartData.series) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    const w = (rect.width > 0 ? rect.width : (canvas.parentElement ? canvas.parentElement.clientWidth : 0)) || 320;
    const h = (rect.height > 0 ? rect.height : (canvas.parentElement ? canvas.parentElement.clientHeight : 0)) || 125;

    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    // Identificar moedas envolvidas (1 moeda ou Par com 2 moedas)
    let ccyList = [];
    if (state.currencies.includes(target)) {
        ccyList = [target];
    } else if (target.length === 6) {
        ccyList = [target.substring(0, 3), target.substring(3, 6)];
    }

    if (ccyList.length === 0) return;

    // Calcular min e max verticais
    let minVal = -0.25;
    let maxVal = 0.25;
    ccyList.forEach(ccy => {
        if (chartData.series[ccy]) {
            minVal = Math.min(minVal, ...chartData.series[ccy]);
            maxVal = Math.max(maxVal, ...chartData.series[ccy]);
        }
    });
    minVal -= 0.06;
    maxVal += 0.06;

    const padT = 16, padB = 16, padL = 8, padR = 45;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    const getY = (val) => padT + plotH * (1 - (val - minVal) / (maxVal - minVal));
    const getX = (idx, total) => padL + (idx / (total - 1)) * plotW;

    // 1. Linhas de Parada e Nível 0.00
    ctx.save();
    
    // Nível +0.20 (Verde)
    if (+0.20 >= minVal && +0.20 <= maxVal) {
        const y20 = getY(0.20);
        ctx.beginPath();
        ctx.strokeStyle = "rgba(0, 230, 118, 0.45)";
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.moveTo(padL, y20);
        ctx.lineTo(w - padR, y20);
        ctx.stroke();
        ctx.fillStyle = "rgba(0, 230, 118, 0.75)";
        ctx.font = "8.5px monospace";
        ctx.fillText("+0.20", w - padR + 4, y20 + 3);
    }

    // Nível 0.00 (Equilíbrio Cyan)
    if (0.00 >= minVal && 0.00 <= maxVal) {
        const y0 = getY(0.00);
        ctx.beginPath();
        ctx.strokeStyle = "rgba(0, 229, 255, 0.35)";
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);
        ctx.moveTo(padL, y0);
        ctx.lineTo(w - padR, y0);
        ctx.stroke();
        ctx.fillStyle = "rgba(0, 229, 255, 0.65)";
        ctx.font = "8.5px monospace";
        ctx.fillText("0.00", w - padR + 4, y0 + 3);
    }

    // Nível -0.20 (Vermelho)
    if (-0.20 >= minVal && -0.20 <= maxVal) {
        const yN20 = getY(-0.20);
        ctx.beginPath();
        ctx.strokeStyle = "rgba(255, 51, 75, 0.45)";
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.moveTo(padL, yN20);
        ctx.lineTo(w - padR, yN20);
        ctx.stroke();
        ctx.fillStyle = "rgba(255, 51, 75, 0.75)";
        ctx.font = "8.5px monospace";
        ctx.fillText("-0.20", w - padR + 4, yN20 + 3);
    }
    ctx.restore();

    // 2. Desenhar Curvas do Score
    ccyList.forEach(ccy => {
        const series = chartData.series[ccy];
        if (!series || series.length < 2) return;

        const color = CCY_COLORS[ccy] || "#FFF";
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.shadowColor = color;
        ctx.shadowBlur = 4;

        for (let i = 0; i < series.length; i++) {
            const x = getX(i, series.length);
            const y = getY(series[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Ponto Final + Badge de Pontuação Atual
        const lastIdx = series.length - 1;
        const lastX = getX(lastIdx, series.length);
        const lastY = getY(series[lastIdx]);
        const lastScore = series[lastIdx];

        ctx.beginPath();
        ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();

        ctx.font = "bold 9.5px monospace";
        ctx.fillStyle = color;
        ctx.fillText(`${(lastScore >= 0 ? "+" : "") + lastScore.toFixed(2)} ${ccy}`, lastX - 42, lastY - 6);
        ctx.restore();
    });
}

// RAIO-X 5-TIMEFRAMES DA MOEDA OU DO PAR FOREX
window.openDeepDive = function(target) {
    if (!state.data) return;
    state.selectedDeepDiveTarget = target;

    const isPair = target.length === 6 && !state.currencies.includes(target);
    const triadsGrid = document.getElementById("deepDiveTriadsGrid");
    const verdictCard = document.getElementById("deepDiveVerdictCard");

    if (isPair) {
        const base = target.substring(0, 3);
        const quote = target.substring(3, 6);
        const baseFlag = CCY_FLAGS[base] || "";
        const quoteFlag = CCY_FLAGS[quote] || "";
        const pairItem = state.data.pairs?.find(p => p.pair === target);

        document.getElementById("deepDiveFlag").textContent = `${baseFlag}${quoteFlag}`;
        document.getElementById("deepDiveTitle").textContent = `Par ${target}`;
        document.getElementById("deepDiveSubtitle").textContent = pairItem ? pairItem.thesis : `Raio-X de Confluência Intraday nos 3 TFs (D1, H4, H1) entre ${base} e ${quote}`;

        verdictCard.innerHTML = `
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">Confluência Institucional Intraday (3-TF):</div>
                <div style="font-size: 14px; font-weight: 800; color: #FFFFFF;">${pairItem ? pairItem.recommendation : target} — Convicção: ${pairItem ? pairItem.conviction : 'ALTA'}</div>
            </div>
            <div>
                <span class="signal-pill ${pairItem && pairItem.badge_type ? pairItem.badge_type.toLowerCase() : 'buy'}">
                    ${pairItem ? pairItem.recommendation : 'ANALISANDO'}
                </span>
            </div>
        `;

        triadsGrid.innerHTML = "";
        const tfs = ["D1", "H4", "H1"];

        tfs.forEach(tf => {
            const card = document.createElement("div");
            card.className = "triad-card";
            card.innerHTML = `
                <div class="triad-card-header">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="triad-tf-title">${tf}</span>
                        <span class="tf-tab" style="font-size: 10px; padding: 2px 8px;">${baseFlag} ${base} × ${quote} ${quoteFlag}</span>
                    </div>
                    <span class="triad-score positive" style="font-size: 11px;">
                        Confluência de Força Relativa (${tf})
                    </span>
                </div>
                
                <div class="triad-card-body">
                    <!-- 3/4 DO ESPAÇO: GRÁFICO EXPANSIVO (75%) -->
                    <div class="triad-canvas-container" style="flex: 3; min-width: 0; height: 160px; min-height: 160px; background: #080B11; border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; position: relative; overflow: hidden;">
                        <canvas id="deepDiveCanvas_${tf}" style="width: 100%; height: 160px; display: block;"></canvas>
                    </div>

                    <!-- 1/4 DO ESPAÇO: EXPLICAÇÃO INSTITUCIONAL (25%) -->
                    <div class="triad-info-pane">
                        <div class="triad-step">
                            <div class="triad-step-label">📊 Timeframe</div>
                            <div class="triad-step-val" style="color: #FFF;">${tf} Institutional Cycle</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">🟢 Moeda Base</div>
                            <div class="triad-step-val" style="color: ${CCY_COLORS[base] || '#FFF'};">${baseFlag} ${base} (Força Relativa)</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">🔴 Moeda Cotada</div>
                            <div class="triad-step-val" style="color: ${CCY_COLORS[quote] || '#FFF'};">${quoteFlag} ${quote} (Força Relativa)</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">💡 Confluência</div>
                            <div class="triad-step-val" style="color: #00E5FF; font-size: 10.5px;">Cruzamentos de score e divergência cíclica no ${tf}.</div>
                        </div>
                    </div>
                </div>
            `;
            triadsGrid.appendChild(card);
        });

        document.getElementById("deepDiveModal").classList.remove("hidden");

        const renderCharts = () => {
            tfs.forEach(tf => drawTriadMiniChart(`deepDiveCanvas_${tf}`, target, tf));
        };
        requestAnimationFrame(renderCharts);
        setTimeout(renderCharts, 60);
        setTimeout(renderCharts, 200);

        return;
    }

    // Caso seja Moeda Única (ex: AUD, USD, EUR, etc.)
    const ccy = target;
    const ccyData = state.data.currencies?.find(c => c.symbol === ccy);
    if (!ccyData) return;

    const tfs = ["MN1", "W1", "D1", "H4", "H1"];
    let tfPillsHtml = "";
    tfs.forEach(tf => {
        const triad = ccyData.triads ? ccyData.triads[tf] : null;
        if (!triad) return;
        const led = triad.led || "yellow";
        const angle = triad.angle || "";
        const angleType = triad.angle_type || "";
        const scoreStr = triad.score_str || `${triad.score >= 0 ? "+" : ""}${triad.score.toFixed(2)}`;

        let icon = "●";
        let label = "Neutro";
        let pillClass = "pill-yellow";

        if (angleType === "FOGUETE" || angle.includes("Foguete") || angle.includes("▲▲")) {
            icon = "🚀";
            label = "Foguete (▲▲)";
            pillClass = "pill-rocket-up";
        } else if (angleType === "MONTANHA_RUSSA" || angle.includes("Montanha-Russa") || angle.includes("▼▼")) {
            icon = "🎢";
            label = "Queda Forte (▼▼)";
            pillClass = "pill-rocket-down";
        } else if (led === "green") {
            icon = "🟢";
            label = "Força (UP)";
            pillClass = "pill-green";
        } else if (led === "red") {
            icon = "🔴";
            label = "Fraqueza (DN)";
            pillClass = "pill-red";
        } else {
            icon = "🟡";
            label = "Divergência";
            pillClass = "pill-yellow";
        }

        tfPillsHtml += `
            <div class="deep-dive-tf-pill ${pillClass}">
                <div class="tf-pill-top">
                    <span class="tf-pill-name">${tf}</span>
                    <span class="tf-pill-score">${scoreStr}</span>
                </div>
                <div class="tf-pill-bottom">
                    <span class="tf-pill-icon">${icon}</span>
                    <span class="tf-pill-text">${label}</span>
                </div>
            </div>
        `;
    });

    document.getElementById("deepDiveFlag").textContent = ccyData.flag;
    document.getElementById("deepDiveTitle").textContent = `${ccyData.symbol}`;
    document.getElementById("deepDiveSubtitle").textContent = "Diagnóstico Cíclico e Tríade Analítica nos 5 Timeframes (MN1, W1, D1, H4, H1) — CSS PRO";

    verdictCard.innerHTML = `
        <div style="width: 100%;">
            <div class="deep-dive-tf-pills-row" style="margin-top: 0;">
                ${tfPillsHtml}
            </div>
        </div>
    `;

    triadsGrid.innerHTML = "";
    tfs.forEach(tf => {
        const triad = ccyData.triads[tf];
        if (!triad) return;

        const card = document.createElement("div");
        card.className = "triad-card";
        card.innerHTML = `
            <div class="triad-card-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="triad-tf-title">${tf}</span>
                    <span class="tf-tab" style="font-size: 10px; padding: 2px 8px;">${ccyData.flag} ${ccy}</span>
                </div>
                <span class="triad-score ${triad.score > 0 ? 'positive' : 'negative'}">
                    ${triad.score_str} ${triad.dir} — ${triad.angle}
                </span>
            </div>

            <div class="triad-card-body">
                <!-- 3/4 DO ESPAÇO: GRÁFICO EXPANSIVO (75%) -->
                <div class="triad-canvas-container" style="flex: 3; min-width: 0; height: 160px; min-height: 160px; background: #080B11; border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; position: relative; overflow: hidden;">
                    <canvas id="deepDiveCanvas_${tf}" style="width: 100%; height: 160px; display: block;"></canvas>
                </div>

                <!-- 1/4 DO ESPAÇO: EXPLICAÇÃO INSTITUCIONAL (25%) -->
                <div class="triad-info-pane">
                    <div class="triad-step">
                        <div class="triad-step-label">1. Região no Box</div>
                        <div class="triad-step-val" style="color: #FFF;">${triad.region}</div>
                    </div>
                    <div class="triad-step">
                        <div class="triad-step-label">2. Ciclo Atual</div>
                        <div class="triad-step-val" style="color: #00E5FF;">${triad.current_cycle}</div>
                    </div>
                    <div class="triad-step">
                        <div class="triad-step-label">3. Ciclo Devendo</div>
                        <div class="triad-step-val" style="color: #FFD600;">${triad.owing_cycle}</div>
                    </div>
                    <div class="triad-step">
                        <div class="triad-step-label">4. Angulação / Veredito</div>
                        <div class="triad-step-val" style="color: #FFF;">${triad.angle}</div>
                    </div>
                </div>
            </div>
        `;
        triadsGrid.appendChild(card);
    });

    const modal = document.getElementById("deepDiveModal");
    modal.classList.add("active");

    setTimeout(() => {
        tfs.forEach(tf => {
            drawTriadMiniChart(`deepDiveCanvas_${tf}`, ccy, tf);
        });
    }, 150);
};

// IMPRESSÃO EM PDF DO RELATÓRIO DO RAIO-X
window.printDeepDiveReport = function() {
    const target = state.selectedDeepDiveTarget || "USD";
    const flag = CCY_FLAGS[target] || "";
    const isPair = target.length === 6 && !state.currencies.includes(target);
    const tfs = ["MN1", "W1", "D1", "H4", "H1"];

    let ccyData = null;
    let pairData = null;
    if (isPair) pairData = state.data.pairs?.find(p => p.pair === target);
    else ccyData = state.data.currencies?.find(c => c.symbol === target);

    const verdictHtml = document.getElementById("deepDiveVerdictCard")?.innerHTML || "";
    
    // Capturar as 5 imagens PNG dos canvas do Raio-X
    const chartImages = {};
    tfs.forEach(tf => {
        const c = document.getElementById(`deepDiveCanvas_${tf}`);
        if (c) {
            try {
                chartImages[tf] = c.toDataURL("image/png");
            } catch (e) {
                chartImages[tf] = "";
            }
        }
    });

    // Montar os 5 cards com imagem + tríade
    let triadsHtml = "";
    tfs.forEach(tf => {
        const triad = ccyData ? ccyData.triads[tf] : (pairData?.triads ? pairData.triads[tf] : null);
        const imgSrc = chartImages[tf] || "";
        const scoreClass = (triad && triad.score > 0) ? "positive" : "negative";
        const scoreText = triad ? `${triad.score_str} ${triad.dir} — ${triad.angle}` : "";

        triadsHtml += `
            <div class="print-triad-card">
                <div class="print-triad-header">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="triad-tf-title">${tf}</span>
                        <span class="tf-tab">${flag} ${target}</span>
                    </div>
                    <span class="triad-score ${scoreClass}">${scoreText}</span>
                </div>
                <div class="print-triad-body">
                    <div class="print-triad-chart">
                        ${imgSrc ? `<img src="${imgSrc}" style="width: 100%; height: 130px; object-fit: contain; display: block;" />` : ''}
                    </div>
                    <div class="print-triad-info">
                        <div class="triad-step">
                            <div class="triad-step-label">1. Região no Box</div>
                            <div class="triad-step-val" style="color: #FFF;">${triad ? triad.region : '-'}</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">2. Ciclo Atual</div>
                            <div class="triad-step-val" style="color: #00E5FF;">${triad ? triad.current_cycle : '-'}</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">3. Ciclo Devendo</div>
                            <div class="triad-step-val" style="color: #FFD600;">${triad ? triad.owing_cycle : '-'}</div>
                        </div>
                        <div class="triad-step">
                            <div class="triad-step-label">4. Angulação / Veredito</div>
                            <div class="triad-step-val" style="color: #FFF;">${triad ? triad.angle : '-'}</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });

    const printWin = window.open('', '_blank', 'width=1100,height=900');
    if (!printWin) {
        alert("Por favor, permita popups para imprimir o relatório.");
        return;
    }

    const printDoc = printWin.document;
    printDoc.open();
    printDoc.write(`
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <title>Raio-X Institucional - ${target}</title>
            <style>
                @page { size: A4 portrait; margin: 10mm 10mm 10mm 10mm; }
                * { box-sizing: border-box; -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; margin: 0; padding: 0; }
                body {
                    background: #080B11 !important;
                    color: #E2E8F0 !important;
                    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    padding: 12px;
                }
                .print-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    border-bottom: 2px solid rgba(0, 229, 255, 0.4);
                    padding-bottom: 8px;
                    margin-bottom: 12px;
                }
                .print-title { font-size: 18px; font-weight: 800; color: #FFFFFF; }
                .print-subtitle { font-size: 11px; color: #8899A6; margin-top: 2px; }
                .deep-dive-verdict-card {
                    background: #0E131E;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 8px 12px;
                    margin-bottom: 10px;
                    page-break-inside: avoid;
                    break-inside: avoid;
                }
                .deep-dive-tf-pills-row {
                    display: flex;
                    align-items: stretch;
                    gap: 8px;
                    margin-top: 0;
                    flex-wrap: wrap;
                }
                .deep-dive-tf-pill {
                    flex: 1;
                    min-width: 100px;
                    background: #05070A;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 6px;
                    padding: 5px 8px;
                    display: flex;
                    flex-direction: column;
                    gap: 3px;
                }
                .tf-pill-top { display: flex; justify-content: space-between; font-size: 10.5px; font-weight: 800; color: #FFF; }
                .tf-pill-bottom { display: flex; align-items: center; gap: 4px; font-size: 9.5px; font-weight: 700; }
                .pill-green .tf-pill-bottom, .pill-rocket-up .tf-pill-bottom { color: #00E676; }
                .pill-red .tf-pill-bottom, .pill-rocket-down .tf-pill-bottom { color: #FF1744; }
                .pill-yellow .tf-pill-bottom { color: #FFD700; }
                
                .print-triad-card {
                    background: #0E131E;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 8px 10px;
                    margin-bottom: 8px;
                    page-break-inside: avoid;
                    break-inside: avoid;
                }
            </style>
        </head>
        <body>
            <div class="print-header">
                <div>
                    <h1 class="print-title">${flag} Raio-X Institucional: ${target}</h1>
                    <p class="print-subtitle">Diagnóstico Cíclico e Tríade Analítica nos 5 Timeframes (MN1, W1, D1, H4, H1) — CSS PRO</p>
                </div>
            </div>
            <div class="deep-dive-verdict-card">${verdictHtml}</div>
            <div class="print-triads-container">${triadsHtml}</div>
            <script>window.onload = () => { setTimeout(() => window.print(), 500); }</script>
        </body>
        </html>
    `);
    printDoc.close();
};

// EXPORTAÇÃO DIRETA DO RAIO-X EM IMAGEM DE ALTA RESOLUÇÃO (PNG)
window.exportDeepDiveImage = function() {
    const target = state.selectedDeepDiveTarget || "USD";
    const flag = CCY_FLAGS[target] || "";
    const isPair = target.length === 6 && !state.currencies.includes(target);
    const tfs = isPair ? ["D1", "H4", "H1"] : ["MN1", "W1", "D1", "H4", "H1"];

    // Redesenhar os mini canvas
    tfs.forEach(tf => drawTriadMiniChart(`deepDiveCanvas_${tf}`, target, tf));

    const exportCanvas = document.createElement("canvas");
    const width = 1200;
    const height = isPair ? 1040 : 1580;
    exportCanvas.width = width;
    exportCanvas.height = height;
    const ctx = exportCanvas.getContext("2d");

    // 1. Fundo Dark Premium
    ctx.fillStyle = "#080B11";
    ctx.fillRect(0, 0, width, height);

    // 2. Cabeçalho
    ctx.fillStyle = "#0E131E";
    ctx.fillRect(20, 20, width - 40, 85);
    ctx.strokeStyle = "rgba(0, 229, 255, 0.4)";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(20, 20, width - 40, 85);

    ctx.fillStyle = "#FFFFFF";
    ctx.font = "bold 24px 'Inter', sans-serif";
    ctx.fillText(`${flag} Raio-X Institucional: ${target}`, 40, 58);

    ctx.fillStyle = "#8899A6";
    ctx.font = "13px 'Inter', sans-serif";
    const subText = isPair ? "Raio-X de Confluência Intraday nos 3 Timeframes (D1, H4, H1) — CSS PRO" : "Diagnóstico Cíclico e Tríade Analítica nos 5 Timeframes (MN1, W1, D1, H4, H1) — CSS PRO";
    ctx.fillText(subText, 40, 85);

    const timeStr = state.data?.timestamp || new Date().toLocaleString();
    ctx.textAlign = "right";
    ctx.fillText(timeStr, width - 40, 58);
    ctx.textAlign = "left";

    // 3. Card com Resumo dos 5 Timeframes
    let ccyData = null;
    let pairItem = null;
    if (isPair) pairItem = state.data.pairs?.find(p => p.pair === target);
    else ccyData = state.data.currencies?.find(c => c.symbol === target);

    const verdictH = 68;
    ctx.fillStyle = "#0E131E";
    ctx.fillRect(20, 118, width - 40, verdictH);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
    ctx.lineWidth = 1;
    ctx.strokeRect(20, 118, width - 40, verdictH);

    // Desenhar os 5 Pills Multi-TF no Card de Resumo
    if (ccyData && ccyData.triads) {
        const pillBoxW = (width - 90) / 5;
        const pillBoxH = 50;
        const pillBoxY = 127;

        tfs.forEach((tf, idx) => {
            const triad = ccyData.triads[tf];
            if (!triad) return;
            const bx = 30 + idx * (pillBoxW + 6);
            const led = triad.led || "yellow";
            const angle = triad.angle || "";
            const angleType = triad.angle_type || "";
            const scoreStr = triad.score_str || `${triad.score >= 0 ? "+" : ""}${triad.score.toFixed(2)}`;

            let pBg = "#071C12", pBorder = "#00E676", pText = "#00E676", pIcon = "● FORÇA (UP)";
            if (angleType === "FOGUETE" || angle.includes("Foguete") || angle.includes("▲▲")) {
                pBg = "#0B2618"; pBorder = "#00E676"; pText = "#00E676"; pIcon = "▲▲ FOGUETE";
            } else if (angleType === "MONTANHA_RUSSA" || angle.includes("Montanha-Russa") || angle.includes("▼▼")) {
                pBg = "#2B0B11"; pBorder = "#FF1744"; pText = "#FF1744"; pIcon = "▼▼ QUEDA FORTE";
            } else if (led === "green") {
                pBg = "#071C12"; pBorder = "#00E676"; pText = "#00E676"; pIcon = "● FORÇA (UP)";
            } else if (led === "red") {
                pBg = "#21080D"; pBorder = "#FF1744"; pText = "#FF1744"; pIcon = "● FRAQUEZA (DN)";
            } else {
                pBg = "#211D07"; pBorder = "#FFD700"; pText = "#FFD700"; pIcon = "● DIVERGÊNCIA";
            }

            ctx.fillStyle = pBg;
            ctx.strokeStyle = pBorder;
            ctx.lineWidth = 1;
            ctx.beginPath();
            if (ctx.roundRect) ctx.roundRect(bx, pillBoxY, pillBoxW, pillBoxH, 5);
            else ctx.rect(bx, pillBoxY, pillBoxW, pillBoxH);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = "#FFFFFF";
            ctx.font = "bold 13px 'Inter', sans-serif";
            ctx.fillText(`${tf}: ${scoreStr}`, bx + 12, pillBoxY + 20);

            ctx.fillStyle = pText;
            ctx.font = "bold 11px 'Inter', sans-serif";
            ctx.fillText(pIcon, bx + 12, pillBoxY + 38);
        });
    }

    // 4. Desenhar os 5 Timeframe Cards
    let startY = 198;
    const cardHeight = 265;
    const cardGap = 12;

    tfs.forEach((tf, i) => {
        const y = startY + i * (cardHeight + cardGap);
        const cardW = width - 40;

        // Fundo do Card TF
        ctx.fillStyle = "#0E131E";
        ctx.fillRect(20, y, cardW, cardHeight);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
        ctx.lineWidth = 1;
        ctx.strokeRect(20, y, cardW, cardHeight);

        // Header do Card TF
        const triad = ccyData ? ccyData.triads[tf] : null;
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "bold 15px 'Inter', sans-serif";
        ctx.fillText(`📊 ${tf} ${target}`, 35, y + 26);

        if (triad) {
            ctx.fillStyle = triad.score > 0 ? "#00E676" : "#FF1744";
            ctx.font = "bold 13px 'JetBrains Mono', monospace";
            ctx.textAlign = "right";
            ctx.fillText(`${triad.score_str} ${triad.dir} — ${triad.angle}`, width - 35, y + 26);
            ctx.textAlign = "left";
        }

        // Gráfico (renderizado a partir do canvas existente)
        const srcCanvas = document.getElementById(`deepDiveCanvas_${tf}`);
        if (srcCanvas) {
            const chartW = cardW * 0.70;
            const chartH = cardHeight - 45;
            ctx.drawImage(srcCanvas, 35, y + 36, chartW, chartH);
        }

        // Painel de Texto da Tríade à direita (30% da largura)
        const infoX = 35 + cardW * 0.70 + 15;
        const infoW = cardW * 0.26;
        ctx.fillStyle = "#05070A";
        ctx.fillRect(infoX, y + 36, infoW, cardHeight - 45);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
        ctx.strokeRect(infoX, y + 36, infoW, cardHeight - 45);

        if (triad) {
            let lineY = y + 56;
            const drawStep = (label, val, color) => {
                ctx.fillStyle = "#7F8C8D";
                ctx.font = "bold 10px 'Inter', sans-serif";
                ctx.fillText(label.toUpperCase(), infoX + 12, lineY);
                lineY += 16;
                ctx.fillStyle = color || "#FFFFFF";
                ctx.font = "bold 11.5px 'Inter', sans-serif";
                ctx.fillText(val, infoX + 12, lineY);
                lineY += 28;
            };

            drawStep("1. Região no Box", triad.region, "#FFFFFF");
            drawStep("2. Ciclo Atual", triad.current_cycle, "#00E5FF");
            drawStep("3. Ciclo Devendo", triad.owing_cycle, "#FFD600");
            drawStep("4. Angulação / Veredito", triad.angle, "#FFFFFF");
        } else if (isPair) {
            const base = target.substring(0, 3);
            const quote = target.substring(3, 6);
            const baseFlag = CCY_FLAGS[base] || "";
            const quoteFlag = CCY_FLAGS[quote] || "";
            let lineY = y + 56;
            const drawStep = (label, val, color) => {
                ctx.fillStyle = "#7F8C8D";
                ctx.font = "bold 10px 'Inter', sans-serif";
                ctx.fillText(label.toUpperCase(), infoX + 12, lineY);
                lineY += 16;
                ctx.fillStyle = color || "#FFFFFF";
                ctx.font = "bold 11.5px 'Inter', sans-serif";
                ctx.fillText(val, infoX + 12, lineY);
                lineY += 28;
            };

            drawStep("Timeframe", `${tf} Institutional Cycle`, "#FFFFFF");
            drawStep("Moeda Base", `${baseFlag} ${base} (Força Relativa)`, CCY_COLORS[base] || "#FFF");
            drawStep("Moeda Cotada", `${quoteFlag} ${quote} (Força Relativa)`, CCY_COLORS[quote] || "#FFF");
            drawStep("Confluência", `Cruzamento e fluxo no ${tf}`, "#00E5FF");
        }
    });

    // 5. Baixar imagem PNG
    const link = document.createElement("a");
    link.download = `Raio-X_${target}_${new Date().toISOString().slice(0, 10)}.png`;
    link.href = exportCanvas.toDataURL("image/png");
    link.click();
};

// ENVIAR FOTO DO RAIO-X INSTITUCIONAL PARA O TELEGRAM
window.sendDeepDiveToTelegram = async function() {
    const btn = document.getElementById("btnSendTelegram");
    const originalText = btn ? btn.innerHTML : "<span>✈️</span> <span>Telegram</span>";
    const target = state.selectedDeepDiveTarget || "USD";
    const flag = CCY_FLAGS[target] || "";
    const isPair = target.length === 6 && !state.currencies.includes(target);

    let ccyData = null;
    let pairItem = null;
    if (isPair) pairItem = state.data.pairs?.find(p => p.pair === target);
    else ccyData = state.data.currencies?.find(c => c.symbol === target);

    const bias = isPair ? (pairItem?.recommendation || "") : (ccyData?.trade_bias || "");
    const confluenceState = isPair ? (`Convicção: ${pairItem?.conviction || 'ALTA'}`) : (ccyData?.confluence_state || "");
    const timeStr = state.data?.timestamp || new Date().toLocaleString();

    try {
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<span>⏳</span> <span>Enviando...</span>`;
        }

        // 1. Gerar imagem offscreen canvas em alta resolução
        const tfs = ["MN1", "W1", "D1", "H4", "H1"];
        tfs.forEach(tf => drawTriadMiniChart(`deepDiveCanvas_${tf}`, target, tf));

        const exportCanvas = document.createElement("canvas");
        const width = 1200;
        const height = 1580;
        exportCanvas.width = width;
        exportCanvas.height = height;
        const ctx = exportCanvas.getContext("2d");

        // Fundo Dark Premium
        ctx.fillStyle = "#080B11";
        ctx.fillRect(0, 0, width, height);

        // Cabeçalho
        ctx.fillStyle = "#0E131E";
        ctx.fillRect(20, 20, width - 40, 85);
        ctx.strokeStyle = "rgba(0, 229, 255, 0.4)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(20, 20, width - 40, 85);

        ctx.fillStyle = "#FFFFFF";
        ctx.font = "bold 24px 'Inter', sans-serif";
        ctx.fillText(`${flag} Raio-X Institucional: ${target}`, 40, 58);

        ctx.fillStyle = "#8899A6";
        ctx.font = "13px 'Inter', sans-serif";
        ctx.fillText("Diagnóstico Cíclico e Tríade Analítica nos 5 Timeframes (MN1, W1, D1, H4, H1) — CSS PRO", 40, 85);

        ctx.textAlign = "right";
        ctx.fillText(timeStr, width - 40, 58);
        ctx.textAlign = "left";

        // Card com Resumo dos 5 Timeframes
        const verdictH = 68;
        ctx.fillStyle = "#0E131E";
        ctx.fillRect(20, 118, width - 40, verdictH);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
        ctx.lineWidth = 1;
        ctx.strokeRect(20, 118, width - 40, verdictH);

        // Desenhar os 5 Pills Multi-TF no Card de Resumo
        if (ccyData && ccyData.triads) {
            const pillBoxW = (width - 90) / 5;
            const pillBoxH = 50;
            const pillBoxY = 127;

            tfs.forEach((tf, idx) => {
                const triad = ccyData.triads[tf];
                if (!triad) return;
                const bx = 30 + idx * (pillBoxW + 6);
                const led = triad.led || "yellow";
                const angle = triad.angle || "";
                const angleType = triad.angle_type || "";
                const scoreStr = triad.score_str || `${triad.score >= 0 ? "+" : ""}${triad.score.toFixed(2)}`;

                let pBg = "#071C12", pBorder = "#00E676", pText = "#00E676", pIcon = "● FORÇA (UP)";
                if (angleType === "FOGUETE" || angle.includes("Foguete") || angle.includes("▲▲")) {
                    pBg = "#0B2618"; pBorder = "#00E676"; pText = "#00E676"; pIcon = "▲▲ FOGUETE";
                } else if (angleType === "MONTANHA_RUSSA" || angle.includes("Montanha-Russa") || angle.includes("▼▼")) {
                    pBg = "#2B0B11"; pBorder = "#FF1744"; pText = "#FF1744"; pIcon = "▼▼ QUEDA FORTE";
                } else if (led === "green") {
                    pBg = "#071C12"; pBorder = "#00E676"; pText = "#00E676"; pIcon = "● FORÇA (UP)";
                } else if (led === "red") {
                    pBg = "#21080D"; pBorder = "#FF1744"; pText = "#FF1744"; pIcon = "● FRAQUEZA (DN)";
                } else {
                    pBg = "#211D07"; pBorder = "#FFD700"; pText = "#FFD700"; pIcon = "● DIVERGÊNCIA";
                }

                ctx.fillStyle = pBg;
                ctx.strokeStyle = pBorder;
                ctx.lineWidth = 1;
                ctx.beginPath();
                if (ctx.roundRect) ctx.roundRect(bx, pillBoxY, pillBoxW, pillBoxH, 5);
                else ctx.rect(bx, pillBoxY, pillBoxW, pillBoxH);
                ctx.fill();
                ctx.stroke();

                ctx.fillStyle = "#FFFFFF";
                ctx.font = "bold 13px 'Inter', sans-serif";
                ctx.fillText(`${tf}: ${scoreStr}`, bx + 12, pillBoxY + 20);

                ctx.fillStyle = pText;
                ctx.font = "bold 11px 'Inter', sans-serif";
                ctx.fillText(pIcon, bx + 12, pillBoxY + 38);
            });
        }

        // 5 Timeframe Cards
        let startY = 198;
        const cardHeight = 265;
        const cardGap = 12;

        tfs.forEach((tf, i) => {
            const y = startY + i * (cardHeight + cardGap);
            const cardW = width - 40;

            ctx.fillStyle = "#0E131E";
            ctx.fillRect(20, y, cardW, cardHeight);
            ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
            ctx.lineWidth = 1;
            ctx.strokeRect(20, y, cardW, cardHeight);

            const triad = ccyData ? ccyData.triads[tf] : null;
            ctx.fillStyle = "#FFFFFF";
            ctx.font = "bold 15px 'Inter', sans-serif";
            ctx.fillText(`📊 ${tf} ${target}`, 35, y + 26);

            if (triad) {
                ctx.fillStyle = triad.score > 0 ? "#00E676" : "#FF1744";
                ctx.font = "bold 14px 'JetBrains Mono', monospace";
                ctx.textAlign = "right";
                ctx.fillText(`${triad.score_str} ${triad.dir} — ${triad.angle}`, width - 35, y + 26);
                ctx.textAlign = "left";
            }

            const srcCanvas = document.getElementById(`deepDiveCanvas_${tf}`);
            if (srcCanvas) {
                const chartW = cardW * 0.70;
                const chartH = cardHeight - 45;
                ctx.drawImage(srcCanvas, 35, y + 36, chartW, chartH);
            }

            const infoX = 35 + cardW * 0.70 + 15;
            const infoW = cardW * 0.26;
            ctx.fillStyle = "#05070A";
            ctx.fillRect(infoX, y + 36, infoW, cardHeight - 45);
            ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
            ctx.strokeRect(infoX, y + 36, infoW, cardHeight - 45);

            if (triad) {
                let lineY = y + 56;
                const drawStep = (label, val, color) => {
                    ctx.fillStyle = "#7F8C8D";
                    ctx.font = "bold 10px 'Inter', sans-serif";
                    ctx.fillText(label.toUpperCase(), infoX + 12, lineY);
                    lineY += 16;
                    ctx.fillStyle = color || "#FFFFFF";
                    ctx.font = "bold 11.5px 'Inter', sans-serif";
                    ctx.fillText(val, infoX + 12, lineY);
                    lineY += 28;
                };

                drawStep("1. Região no Box", triad.region, "#FFFFFF");
                drawStep("2. Ciclo Atual", triad.current_cycle, "#00E5FF");
                drawStep("3. Ciclo Devendo", triad.owing_cycle, "#FFD600");
                drawStep("4. Angulação / Veredito", triad.angle, "#FFFFFF");
            }
        });

        const imageBase64 = exportCanvas.toDataURL("image/png");

        // 2. Enviar via Backend API ou direto via Telegram Bot
        let sent = false;
        const isLocalServer = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' || window.location.port === '8050';

        if (isLocalServer) {
            try {
                const resp = await fetch("/api/telegram/send-raio-x", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        target: target,
                        image_base64: imageBase64,
                        bias: bias,
                        confluence_state: confluenceState,
                        timestamp: timeStr
                    })
                });
                if (resp.ok) {
                    const resJson = await resp.json();
                    if (resJson.success) sent = true;
                }
            } catch (e) {
                console.warn("Backend local offline, tentando envio direto Telegram:", e);
            }
        }

        if (!sent) {
            // Envio Direto via Telegram Bot API (Firebase Hosting / Client-Side Fallback)
            const botToken = "8661694016:AAHJ5RV7kJOnxXvYhcgllx-kYJSdHfbrBH8";
            const chatId = "665651806";
            
            // Converter dataUrl para Blob
            const blobBin = atob(imageBase64.split(',')[1]);
            const array = [];
            for (let i = 0; i < blobBin.length; i++) {
                array.push(blobBin.charCodeAt(i));
            }
            const fileBlob = new Blob([new Uint8Array(array)], { type: 'image/png' });

            const formData = new FormData();
            formData.append("chat_id", chatId);
            formData.append("photo", fileBlob, `Raio-X_${target}.png`);
            formData.append("parse_mode", "HTML");
            formData.append("caption", `📊 <b>Raio-X Institucional: ${target}</b>\n🎯 <b>Estado:</b> ${confluenceState}\n🧭 <b>Viés:</b> ${bias}\n🕒 <i>${timeStr} — CSS Institutional</i>`);

            const tgResp = await fetch(`https://api.telegram.org/bot${botToken}/sendPhoto`, {
                method: "POST",
                body: formData
            });
            const tgJson = await tgResp.json();
            if (tgJson.ok) sent = true;
        }

        if (sent) {
            if (btn) {
                btn.innerHTML = `<span>✅</span> <span>Enviado!</span>`;
                btn.style.borderColor = "#00E676";
                btn.style.color = "#00E676";
            }
            setTimeout(() => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                    btn.style.borderColor = "";
                    btn.style.color = "";
                }
            }, 2500);
        } else {
            throw new Error("Falha ao entregar mensagem no Telegram.");
        }
    } catch (err) {
        console.error("Erro ao enviar Raio-X para o Telegram:", err);
        alert("Erro ao enviar Raio-X para o Telegram: " + (err.message || err));
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
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
// TRACK RECORD & AUDITORIA MULTI-PORTFÓLIO (4 ABAS & MASTER-DETAIL)
// ==========================================================================

function setupTrackRecordModal() {
    const modal = document.getElementById("trackRecordModal");
    const btnOpen = document.getElementById("btnOpenTrackRecordModal");
    const btnClose = document.getElementById("btnCloseTrackRecordModal");
    const btnRecalc = document.getElementById("btnRecalculateTrackRecord");

    state.activeTrackTab = "live"; // 'live', 'audit', 'analytics', 'backtests'
    state.livePollingTimer = null;
    state.backtestStatusPollingTimer = null;

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
            stopBacktestStatusPolling();
        });
    }

    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.add("hidden");
                stopLivePolling();
                stopBacktestStatusPolling();
            }
        });
    }

    // 1. Alternância das Abas Principais
    const trackModal = document.getElementById("trackRecordModal");
    if (trackModal) {
        trackModal.querySelectorAll(".track-nav-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                trackModal.querySelectorAll(".track-nav-tab").forEach(t => t.classList.remove("active"));
                trackModal.querySelectorAll(".track-tab-pane").forEach(p => p.classList.remove("active"));

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
                } else if (targetTab === "backtests") {
                    const pane = document.getElementById("paneBacktests");
                    if (pane) pane.classList.add("active");
                    loadBacktestHistory();
                }
            });
        });
    }

    setupBacktestTriggerForm();

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

// ============================================================
// ACOMPANHAMENTO DE BACKTEST (REGRESSION TRACKING) — 4ª aba do modal de
// track-record. Ver docs/plans/eventual-stargazing-bear.md (consulta
// herdr-ask mfc-13). Toda string vinda do journal (note, nomes de engine
// etc.) é setada via textContent, NUNCA interpolada em innerHTML — este é
// o primeiro texto livre digitado por humano renderizado nesta UI, e o
// resto do arquivo usa innerHTML sem escapar (só números/moeda até aqui).
// A chave de API vai em sessionStorage (some ao fechar a aba), nunca
// localStorage — é uma chave dedicada (CSS_BACKTEST_API_KEY), mas ainda
// assim não deve virar um segredo durável no navegador.
// ============================================================

const BACKTEST_API_KEY_STORAGE_KEY = "css_backtest_api_key";

function getBacktestApiKey() {
    try {
        return sessionStorage.getItem(BACKTEST_API_KEY_STORAGE_KEY) || "";
    } catch (err) {
        return "";
    }
}

function setBacktestApiKey(value) {
    try {
        sessionStorage.setItem(BACKTEST_API_KEY_STORAGE_KEY, value);
    } catch (err) {
        // sessionStorage indisponível (aba privada/bloqueio) — segue sem persistir
    }
}

function fmtBacktestMoney(value) {
    if (typeof value !== "number" || !isFinite(value)) return "n/a";
    return (value >= 0 ? "+$" : "-$") + Math.abs(value).toFixed(2);
}

function setupBacktestTriggerForm() {
    const keyInput = document.getElementById("backtestTriggerApiKey");
    if (keyInput) {
        keyInput.value = getBacktestApiKey();
        keyInput.addEventListener("input", () => setBacktestApiKey(keyInput.value));
    }

    const btn = document.getElementById("btnTriggerBacktest");
    if (!btn) return;
    btn.addEventListener("click", async () => {
        const descInput = document.getElementById("backtestTriggerDescription");
        const runsInput = document.getElementById("backtestTriggerRuns");
        const msgEl = document.getElementById("backtestTriggerStatusMsg");
        const description = (descInput && descInput.value || "").trim();
        const runs = parseInt((runsInput && runsInput.value) || "2", 10);
        const apiKey = getBacktestApiKey();

        if (description.length < 3) {
            if (msgEl) msgEl.textContent = "Descrição precisa ter pelo menos 3 caracteres.";
            return;
        }

        btn.disabled = true;
        if (msgEl) msgEl.textContent = "Disparando...";
        try {
            const res = await fetch("/api/backtest-history/trigger", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Css-Api-Key": apiKey },
                body: JSON.stringify({ description, runs }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                if (msgEl) msgEl.textContent = `Erro (${res.status}): ${body.detail || "falha ao disparar"}`;
                btn.disabled = false;
                return;
            }
            if (msgEl) msgEl.textContent = `Iniciado (run_id=${body.run_id}). Acompanhando...`;
            startBacktestStatusPolling();
        } catch (err) {
            if (msgEl) msgEl.textContent = "Erro de rede ao disparar.";
            btn.disabled = false;
        }
    });
}

function startBacktestStatusPolling() {
    stopBacktestStatusPolling();
    pollBacktestStatus();
    state.backtestStatusPollingTimer = setInterval(pollBacktestStatus, 3000);
}

function stopBacktestStatusPolling() {
    if (state.backtestStatusPollingTimer) {
        clearInterval(state.backtestStatusPollingTimer);
        state.backtestStatusPollingTimer = null;
    }
}

async function pollBacktestStatus() {
    const msgEl = document.getElementById("backtestTriggerStatusMsg");
    const btn = document.getElementById("btnTriggerBacktest");
    try {
        const res = await fetch("/api/backtest-history/trigger/status", {
            headers: { "X-Css-Api-Key": getBacktestApiKey() },
        });
        if (!res.ok) {
            stopBacktestStatusPolling();
            if (btn) btn.disabled = false;
            return;
        }
        const status = await res.json();
        if (status.status === "running" && msgEl) {
            msgEl.textContent = "Status: em andamento...";
        }
        if (status.status !== "running") {
            stopBacktestStatusPolling();
            if (btn) btn.disabled = false;
            if (msgEl) {
                if (status.status === "done") {
                    msgEl.textContent = `Concluído — journal_seq=${status.new_journal_seq != null ? status.new_journal_seq : "?"}`;
                } else if (status.status === "failed") {
                    msgEl.textContent = `Falhou: ${status.error || ("returncode=" + status.returncode)}`;
                } else if (status.status === "interrupted") {
                    // Achado P3-1 (herdr-review mfc-66, mfc-rev-2): antes caía
                    // no "else" mudo — o operador via a tela limpar sem saber
                    // que a execução foi interrompida (ex.: watchdog de janela
                    // crítica). log_tail continua disponível na mesma resposta.
                    msgEl.textContent = "Interrompida (ex.: watchdog de janela crítica) — veja o log.";
                } else if (status.status === "skipped") {
                    msgEl.textContent = `Pulada: ${status.reason || "condição mudou antes de rodar"}`;
                } else {
                    msgEl.textContent = "";
                }
            }
            loadBacktestHistory();
        }
    } catch (err) {
        console.error("Erro no polling do status do backtest:", err);
    }
}

async function loadBacktestHistory() {
    const tbody = document.getElementById("backtestsHistoryTableBody");
    if (!tbody) return;
    try {
        const res = await fetch("/api/backtest-history?limit=100");
        if (!res.ok) throw new Error("Erro ao buscar histórico de backtest");
        const data = await res.json();
        renderBacktestHistoryTable(data.entries || []);
    } catch (err) {
        console.error("Erro ao carregar histórico de backtest:", err);
        tbody.textContent = "";
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 11;
        td.className = "loading-cell";
        td.textContent = "Erro ao carregar histórico.";
        tr.appendChild(td);
        tbody.appendChild(tr);
    }
}

function renderBacktestHistoryTable(entries) {
    const tbody = document.getElementById("backtestsHistoryTableBody");
    if (!tbody) return;
    tbody.textContent = "";

    if (!entries.length) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 11;
        td.className = "loading-cell";
        td.textContent = "Nenhuma execução registrada ainda.";
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
    }

    entries.forEach(entry => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => selectBacktestEntry(entry.journal_seq));

        const addCell = (text) => {
            const td = document.createElement("td");
            td.textContent = text;
            tr.appendChild(td);
            return td;
        };

        addCell(entry.journal_seq != null ? String(entry.journal_seq) : "-");
        addCell(entry.recorded_at_utc ? new Date(entry.recorded_at_utc).toLocaleString("pt-BR") : "-");
        addCell(entry.market_open_at_run === false ? "🌙 fechado" : (entry.market_open_at_run === true ? "🟢 aberto" : "-"));

        const roleCell = addCell(entry.sample_role || "-");
        if (entry.sample_role === "oos_disjoint") {
            roleCell.style.color = "#FFD700";
            roleCell.style.fontWeight = "800";
        }

        const win = entry.window || {};
        addCell(win.days ? `${win.days}d / ${win.nights_evaluated != null ? win.nights_evaluated : "?"}n` : "-");

        const commitCell = addCell(entry.code_commit ? entry.code_commit.slice(0, 8) + (entry.worktree_dirty ? " ⚠️" : "") : "-");

        const descCell = addCell(entry.note || "-");
        if (entry.is_web_trigger) {
            const badge = document.createElement("span");
            badge.textContent = " 🌐";
            badge.title = "Disparado pela web";
            descCell.appendChild(badge);
        }

        const engineNames = Object.keys(entry.engines || {});
        const brutoText = engineNames.map(name => {
            const m = entry.engines[name] || {};
            return `${name}: ${fmtBacktestMoney(m.bruto)} (${m.baskets != null ? m.baskets : 0})`;
        }).join(" | ") || "-";
        addCell(brutoText);

        const paired = entry.paired_net_delta_per_night || {};
        addCell(paired.mean != null
            ? `${fmtBacktestMoney(paired.mean)} ± ${paired.stderr != null ? fmtBacktestMoney(paired.stderr) : "n/a"} (n=${paired.n != null ? paired.n : 0})`
            : "-");

        const liquidoText = engineNames.map(name => {
            const m = entry.engines[name] || {};
            return `${name}: ${fmtBacktestMoney(m.liquido)}`;
        }).join(" | ") || "-";
        addCell(liquidoText);

        const qualityCell = addCell(
            entry.quality_status === "clean" ? "✅" :
            entry.quality_status === "partial_model" ? "⚠️" :
            entry.quality_status === "degraded" ? "❌" : "-"
        );
        qualityCell.className = "text-center";

        tbody.appendChild(tr);
    });
}

async function selectBacktestEntry(journalSeq) {
    if (journalSeq == null) return;
    try {
        const res = await fetch(`/api/backtest-history/${journalSeq}`);
        if (!res.ok) return;
        const entry = await res.json();
        renderBacktestDetailPanel(entry);
    } catch (err) {
        console.error("Erro ao carregar detalhe do backtest:", err);
    }
}

function renderBacktestDetailPanel(entry) {
    const panel = document.getElementById("backtestDetailPanel");
    if (!panel) return;
    panel.textContent = "";
    panel.style.display = "flex";

    const title = document.createElement("div");
    title.style.fontWeight = "800";
    title.style.fontFamily = "var(--font-display)";
    title.style.color = "#FFF";
    title.textContent = `📋 Detalhe da execução #${entry.journal_seq != null ? entry.journal_seq : "-"}`;
    panel.appendChild(title);

    const note = document.createElement("div");
    note.style.fontSize = "12px";
    note.style.color = "var(--text-secondary)";
    note.textContent = entry.note || "(sem descrição)";
    panel.appendChild(note);

    const engines = entry.engines || {};
    Object.keys(engines).forEach(name => {
        const m = engines[name] || {};
        const row = document.createElement("div");
        row.style.fontFamily = "var(--font-mono)";
        row.style.fontSize = "11.5px";
        row.style.color = "var(--text-secondary)";
        row.textContent =
            `${name}: cestas=${m.baskets != null ? m.baskets : 0} bruto=${fmtBacktestMoney(m.bruto)} custo=${fmtBacktestMoney(m.custo)} ` +
            `spread=${fmtBacktestMoney(m.spread)} swap=${fmtBacktestMoney(m.swap)} liquido=${fmtBacktestMoney(m.liquido)} ` +
            `noite%=${m.noite_pct != null ? m.noite_pct : "-"} cesta%=${m.cesta_pct != null ? m.cesta_pct : "-"} qualidade=${m.quality_status || "-"}`;
        panel.appendChild(row);
    });

    const limitations = entry.limitations;
    if (Array.isArray(limitations) && limitations.length) {
        const limTitle = document.createElement("div");
        limTitle.style.fontSize = "11px";
        limTitle.style.fontWeight = "700";
        limTitle.style.color = "var(--text-muted)";
        limTitle.textContent = "Limitações:";
        panel.appendChild(limTitle);
        limitations.forEach(text => {
            const li = document.createElement("div");
            li.style.fontSize = "11px";
            li.style.color = "var(--text-muted)";
            li.textContent = `• ${text}`;
            panel.appendChild(li);
        });
    }

    const runsSummary = entry.runs_summary;
    if (runsSummary && runsSummary.aggregate) {
        const runsTitle = document.createElement("div");
        runsTitle.style.fontSize = "11px";
        runsTitle.style.fontWeight = "700";
        runsTitle.style.color = "var(--text-muted)";
        runsTitle.textContent = `Faixa observada em ${runsSummary.reported_pass != null ? runsSummary.reported_pass : "?"} passadas:`;
        panel.appendChild(runsTitle);
        const byEngine = (runsSummary.aggregate && runsSummary.aggregate.by_engine) || {};
        Object.keys(byEngine).forEach(name => {
            const agg = byEngine[name] || {};
            const liquido = agg.liquido || {};
            const row = document.createElement("div");
            row.style.fontFamily = "var(--font-mono)";
            row.style.fontSize = "11px";
            row.style.color = "var(--text-muted)";
            row.textContent = `${name}: líquido min=${fmtBacktestMoney(liquido.min)} max=${fmtBacktestMoney(liquido.max)} média=${fmtBacktestMoney(liquido.mean)}`;
            panel.appendChild(row);
        });
    }

    const provenance = entry.producer_provenance;
    if (provenance) {
        const provRow = document.createElement("div");
        provRow.style.fontSize = "10.5px";
        provRow.style.color = "var(--text-muted)";
        provRow.style.fontFamily = "var(--font-mono)";
        const terminalPath = provenance.terminal && provenance.terminal.observed_path;
        const ordersSent = provenance.execution && provenance.execution.orders_sent;
        provRow.textContent = `Terminal observado: ${terminalPath || "-"} | orders_sent=${ordersSent}`;
        panel.appendChild(provRow);
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

    const portfolios = session.portfolios || [];
    const totalPairsCount = session.total_open_pairs !== undefined ? session.total_open_pairs : (session.total_pairs_count || 0);
    const totalPnL = session.total_pnl_usd || 0;
    const totalPips = session.total_pips || 0;

    const pnlEl = document.getElementById("liveTotalPnL");
    const pipsEl = document.getElementById("liveTotalPips");
    const mfeEl = document.getElementById("liveTotalMFE");
    const maeEl = document.getElementById("liveTotalMAE");
    const countEl = document.getElementById("liveActiveBasketsCount");
    const pairsCountEl = document.getElementById("liveActivePairsCount");
    const badgeTab = document.getElementById("livePortfoliosBadge");
    const timerBadge = document.getElementById("liveElapsedTimer");
    const dateBadge = document.getElementById("liveDecisionDateBadge");

    const isLive = !!session.is_in_progress;

    // Status text, pill e timer
    const statusTextEl = document.querySelector(".status-live-text");
    const timeInfoEl = document.getElementById("liveSessionTimeInfo");
    const pulseRing = document.querySelector(".pulse-ring-live");
    const kpiTitleEl = document.querySelector(".live-kpi-card.highlight .kpi-title");

    if (dateBadge && session.date) {
        dateBadge.textContent = `Sessão: ${session.date} (Abertura 21:05 ➔ 08:00)`;
    }

    if (statusTextEl) {
        statusTextEl.textContent = isLive ? "🔴 PREGÃO DA MADRUGADA AO VIVO" : "🟢 SESSÃO ENCERRADA (AGUARDANDO 21:05 BRT)";
    }
    if (timeInfoEl) {
        timeInfoEl.textContent = session.session_info_str || (isLive ? "📅 Sessão Ao Vivo | Início: 21h05 ➔ Encerramento: 08h00 BRT" : "✅ Sessão Concluída às 08h00 BRT | Próxima Abertura às 21h05 BRT");
    }
    if (timerBadge) {
        timerBadge.textContent = isLive ? `⏱️ Em andamento (${session.time_remaining_str || '21h➔08h'})` : `⏳ Fechado às 08h00 | Próxima às 21h05 (${session.time_remaining_str || ''})`;
        timerBadge.style.background = isLive ? "rgba(255, 51, 75, 0.2)" : "rgba(0, 230, 118, 0.15)";
        timerBadge.style.color = isLive ? "#FF334B" : "#00E676";
        timerBadge.style.borderColor = isLive ? "rgba(255, 51, 75, 0.4)" : "rgba(0, 230, 118, 0.4)";
    }
    if (badgeTab) {
        const activeCount = portfolios.filter(p => p.decision?.direction !== "NEUTRAL" && p.decision?.direction).length;
        badgeTab.textContent = isLive ? `${activeCount} Cestas Ativas` : `Decisões Gravadas (${activeCount} Ativas)`;
        badgeTab.style.background = isLive ? "rgba(255, 51, 75, 0.2)" : "rgba(0, 229, 255, 0.15)";
        badgeTab.style.color = isLive ? "#FF334B" : "#00E5FF";
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
    if (countEl) countEl.textContent = `${session.active_portfolios_count || portfolios.filter(p=>p.execution?.is_trading).length} Operando`;
    if (pairsCountEl) pairsCountEl.textContent = `${totalPairsCount} ordens no MT5`;

    // 1. RENDERIZAR AS DECISÕES DAS 21:02 (OS 8 CARDS DE MOEDAS)
    const decisionsGrid = document.getElementById("liveDecisionsCardsGrid");
    if (decisionsGrid) {
        decisionsGrid.innerHTML = portfolios.map(port => {
            const dec = port.decision || { direction: port.bias || "NEUTRAL", reason: port.reason || "" };
            const dir = (dec.direction || "NEUTRAL").toUpperCase();
            const dirClass = dir === "BUY" ? "buy" : (dir === "SELL" ? "sell" : "neutral");
            const dirLabel = dir === "BUY" ? "🟢 COMPRA (FORÇA)" : (dir === "SELL" ? "🔴 VENDA (FRAQUEZA)" : "⚪ NEUTRO (SEM SINAL)");
            const cardColor = CCY_COLORS[port.currency] || "#00E5FF";

            return `
                <div class="decision-card ${dirClass}" style="--card-color: ${cardColor};">
                    <div class="decision-card-header">
                        <div class="decision-ccy-badge">
                            <span>${port.flag || CCY_FLAGS[port.currency] || '🏳️'}</span>
                            <span>${port.currency}</span>
                            <span style="font-size: 10px; font-weight: normal; color: var(--text-muted); font-family: var(--font-mono);">#${port.magic}</span>
                        </div>
                        <span class="decision-dir-badge ${dirClass}">${dirLabel}</span>
                    </div>
                    <div class="decision-scores">
                        <span>D1: <b>${dec.d1_score !== undefined ? (dec.d1_score >= 0 ? "+" : "") + Number(dec.d1_score).toFixed(2) : '-'}</b></span>
                        <span>H4: <b>${dec.h4_score !== undefined ? (dec.h4_score >= 0 ? "+" : "") + Number(dec.h4_score).toFixed(2) : '-'}</b></span>
                    </div>
                    <div class="decision-reason" title="${dec.reason}">
                        ${dec.reason || 'Sem confluência'}
                    </div>
                </div>
            `;
        }).join("");
    }

    // 2. Renderizar Cards de Cestas Ativas (Execução MT5)
    const basketsContainer = document.getElementById("liveBasketsCardsContainer");
    if (basketsContainer) {
        const tradingPorts = portfolios.filter(p => p.execution?.is_trading || p.pairs?.length > 0);
        if (tradingPorts.length === 0) {
            basketsContainer.innerHTML = `
                <div style="grid-column: 1 / -1; padding: 20px; background: #0C101A; border-radius: 8px; color: var(--text-muted); text-align: center; border: 1px dashed var(--border-color);">
                    ⏳ Nenhuma ordem aberta no MT5 no momento. As ordens serão disparadas pontualmente às <strong>21:05 BRT</strong> pelos robôs baseadas nas decisões acima.
                </div>
            `;
        } else {
            basketsContainer.innerHTML = tradingPorts.map(port => {
                const exec = port.execution || {};
                const pnl = exec.pnl_usd || port.pnl_usd || 0;
                const isPos = pnl >= 0;
                const dec = port.decision || {};
                return `
                    <div class="live-basket-card">
                        <div style="display: flex; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 20px;">${port.flag || CCY_FLAGS[port.currency] || '🏳️'}</span>
                                <div>
                                    <span style="font-weight: 800; font-family: var(--font-mono); color: #FFF; font-size: 13px;">Cesta ${port.currency}</span>
                                    <span class="region-badge ${dec.direction === 'BUY' ? 'green' : 'red'}" style="margin-left: 6px; font-size: 9px; padding: 1px 5px;">${dec.direction === 'BUY' ? 'COMPRA' : 'VENDA'}</span>
                                </div>
                            </div>
                            <span style="font-size: 10px; font-family: var(--font-mono); color: var(--text-muted);">Magic #${port.magic}</span>
                        </div>
                        <div style="font-size: 10.5px; color: var(--text-muted);">
                            📌 <em>${dec.reason || 'Execução Multi-TF'}</em>
                        </div>
                        <div style="display: flex; align-items: center; justify-content: space-between; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.06); font-family: var(--font-mono); font-size: 11px;">
                            <span>Ordens MT5: <strong style="color: var(--color-cyan);">${exec.open_pairs_count || (port.pairs||[]).length} pares</strong></span>
                            <span style="font-weight: 800; font-size: 13px; color: ${isPos ? 'var(--color-green)' : 'var(--color-red)'};">
                                ${(isPos ? "+$" : "-$") + Math.abs(pnl).toFixed(2)}
                            </span>
                        </div>
                    </div>
                `;
            }).join("");
        }
    }

    // 3. Renderizar Tabela Live das Ordens Reais no MT5
    const tbody = document.getElementById("livePairsTableBody");
    if (tbody) {
        const allLivePairs = [];
        portfolios.forEach(port => {
            const pairsList = port.execution?.pairs || port.pairs || [];
            pairsList.forEach(p => {
                allLivePairs.push({ ...p, basket: port.currency, basketFlag: port.flag || CCY_FLAGS[port.currency] });
            });
        });

        if (allLivePairs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="10" class="loading-cell" style="padding: 24px; color: var(--text-muted);">Nenhuma posição aberta no MT5 neste momento. Aguardando disparo às 21:05 BRT.</td></tr>`;
        } else {
            tbody.innerHTML = allLivePairs.map(p => {
                const pnl = p.pnl_usd || 0;
                const pips = p.pips || 0;
                const isPos = pnl >= 0;
                const pnlClass = isPos ? "positive" : "negative";
                const currPrice = p.current_price || p.exit_price || p.entry_price || 0;

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
                        <td class="text-right" style="font-family: var(--font-mono);">${Number(p.entry_price || 0).toFixed(5)}</td>
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
        renderMultiPortfolioEquityCurve(data.portfolio_equity_curves || {});
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

// Multi-Portfolio Equity State
if (!state.multiPortfolioVisible) {
    state.multiPortfolioVisible = {
        USD: true, EUR: true, GBP: true, CHF: true,
        JPY: true, AUD: true, CAD: true, NZD: true
    };
}

function renderMultiPortfolioEquityCurve(portfolioCurves) {
    const canvas = document.getElementById("equityCanvasMultiPortfolio");
    if (!canvas || !portfolioCurves) return;

    const ctx = canvas.getContext("2d");
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 25, bottom: 30, left: 60, right: 90 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // Coletar todos os pontos das moedas visíveis para escala dinâmica
    let allEquities = [0.0];
    CURRENCIES.forEach(c => {
        if (state.multiPortfolioVisible[c] && portfolioCurves[c]) {
            portfolioCurves[c].forEach(pt => allEquities.push(pt.equity));
        }
    });

    let minVal = Math.min(...allEquities, 0.0);
    let maxVal = Math.max(...allEquities, 5.0);
    const range = (maxVal - minVal) || 1.0;
    minVal -= range * 0.12;
    maxVal += range * 0.12;

    const samplePts = portfolioCurves["USD"] || [];
    const numPoints = Math.max(2, samplePts.length);

    const getX = (i) => padding.left + (i / (numPoints - 1)) * chartW;
    const getY = (val) => padding.top + chartH * (1 - (val - minVal) / (maxVal - minVal));

    // Linha Zero 0.00
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

    // Desenhar a curva individual de cada moeda visível com sua cor institucional
    CURRENCIES.forEach(c => {
        if (!state.multiPortfolioVisible[c]) return;
        const pts = portfolioCurves[c];
        if (!pts || pts.length < 2) return;

        const color = CCY_COLORS[c] || "#FFF";

        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.shadowColor = color;
        ctx.shadowBlur = 5;

        for (let i = 0; i < pts.length; i++) {
            const x = getX(i);
            const y = getY(pts[i].equity);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.restore();

        // Badge / Label final da moeda à direita
        const lastPt = pts[pts.length - 1];
        const lastX = getX(pts.length - 1);
        const lastY = getY(lastPt.equity);

        ctx.save();
        ctx.fillStyle = color;
        ctx.font = "bold 9.5px JetBrains Mono";
        ctx.textAlign = "left";
        const pnlStr = (lastPt.equity >= 0 ? "+$" : "-$") + Math.abs(lastPt.equity).toFixed(1);
        ctx.fillText(`${CCY_FLAGS[c] || ''} ${c} (${pnlStr})`, lastX + 6, lastY + 3);
        ctx.restore();
    });

    // Escala no Eixo Y
    ctx.fillStyle = "#94A3B8";
    ctx.font = "10px JetBrains Mono";
    ctx.textAlign = "right";
    ctx.fillText(`$${maxVal.toFixed(1)}`, padding.left - 8, padding.top + 5);
    ctx.fillText(`$${minVal.toFixed(1)}`, padding.left - 8, height - padding.bottom);
    ctx.fillText(`$0`, padding.left - 8, y0 + 3);

    // Datas no Eixo X
    ctx.textAlign = "center";
    for (let i = 0; i < numPoints; i += Math.max(1, Math.floor(numPoints / 5))) {
        const dStr = samplePts[i] ? samplePts[i].date : "";
        ctx.fillText(dStr, getX(i), height - 8);
    }
}

// Inicializar listeners dos botões de filtro de portfólio
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#multiPortfolioLegendToggles button").forEach(btn => {
        btn.addEventListener("click", () => {
            const ccy = btn.dataset.ccy;
            state.multiPortfolioVisible[ccy] = !state.multiPortfolioVisible[ccy];
            btn.classList.toggle("active", state.multiPortfolioVisible[ccy]);
            btn.style.opacity = state.multiPortfolioVisible[ccy] ? "1" : "0.35";
            if (state.trackRecordData) {
                renderMultiPortfolioEquityCurve(state.trackRecordData.portfolio_equity_curves);
            }
        });
    });
});

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

function renderWeekdayAnalytics(sessions) {
    const container = document.getElementById("weekdayAnalyticsContainer");
    if (!container) return;

    const days = [
        { label: "Segunda-feira", key: 0, pnl: 0.0, count: 0, wins: 0 },
        { label: "Terça-feira", key: 1, pnl: 0.0, count: 0, wins: 0 },
        { label: "Quarta-feira", key: 2, pnl: 0.0, count: 0, wins: 0 },
        { label: "Quinta-feira", key: 3, pnl: 0.0, count: 0, wins: 0 },
        { label: "Sexta-feira", key: 4, pnl: 0.0, count: 0, wins: 0 }
    ];

    sessions.forEach(sess => {
        if (!sess.date) return;
        const d = new Date(sess.date + "T12:00:00Z");
        const wd = d.getUTCDay(); // 0=Dom, 1=Seg...
        const idx = wd - 1;
        if (idx >= 0 && idx < 5) {
            days[idx].count += 1;
            days[idx].pnl += (sess.total_pnl_usd || 0);
            if ((sess.total_pnl_usd || 0) > 0) days[idx].wins += 1;
        }
    });

    if (sessions.length === 0) {
        container.innerHTML = `<div style="color: var(--text-muted); font-size: 11px; text-align: center; padding: 20px;">Aguardando histórico de sessões reais do MT5 para cálculo por dia da semana.</div>`;
        return;
    }

    container.innerHTML = days.map(d => {
        const wr = d.count > 0 ? Math.round((d.wins / d.count) * 100) : 0;
        const isPos = d.pnl >= 0;
        const color = d.count === 0 ? "var(--text-muted)" : (isPos ? "var(--color-green)" : "var(--color-red)");
        const pnlStr = d.count === 0 ? "$0.00" : ((isPos ? "+$" : "-$") + Math.abs(d.pnl).toFixed(2));

        return `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: rgba(255,255,255,0.02); border-radius: 6px; border: 1px solid rgba(255,255,255,0.05);">
                <div style="display: flex; flex-direction: column;">
                    <span style="font-size: 12px; font-weight: 700; color: #FFF;">${d.label}</span>
                    <span style="font-size: 10px; color: var(--text-muted);">${d.count} sessões (${wr}% Win Rate)</span>
                </div>
                <div style="text-align: right;">
                    <span style="font-family: var(--font-mono); font-size: 13px; font-weight: 800; color: ${color};">${pnlStr}</span>
                </div>
            </div>
        `;
    }).join("");
}

function renderRiskRewardAnalytics(sessions) {
    const container = document.getElementById("riskRewardAnalyticsContainer");
    if (!container) return;

    let totalMFE = 0.0;
    let totalMAE = 0.0;
    let maxPeak = 0.0;
    let count = 0;

    sessions.forEach(sess => {
        if (sess.portfolios_count > 0) {
            totalMFE += (sess.mfe_usd || 0);
            totalMAE += Math.abs(sess.mae_usd || 0);
            maxPeak = Math.max(maxPeak, sess.mfe_usd || 0);
            count += 1;
        }
    });

    const avgMFE = count > 0 ? (totalMFE / count) : 0.0;
    const avgMAE = count > 0 ? (totalMAE / count) : 0.0;
    const ratio = avgMAE > 0 ? (avgMFE / avgMAE) : (avgMFE > 0 ? 9.9 : 1.0);

    container.innerHTML = `
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px;">
            <div style="background: rgba(46, 204, 113, 0.08); border: 1px solid rgba(46, 204, 113, 0.25); border-radius: 8px; padding: 10px;">
                <span style="font-size: 10px; color: var(--color-green); text-transform: uppercase; font-weight: 700; display: block;">MFE Médio (Pico)</span>
                <span style="font-size: 17px; font-weight: 800; font-family: var(--font-mono); color: #FFF;">+$${avgMFE.toFixed(2)}</span>
            </div>
            <div style="background: rgba(255, 59, 48, 0.08); border: 1px solid rgba(255, 59, 48, 0.25); border-radius: 8px; padding: 10px;">
                <span style="font-size: 10px; color: var(--color-red); text-transform: uppercase; font-weight: 700; display: block;">MAE Médio (Drawdown)</span>
                <span style="font-size: 17px; font-weight: 800; font-family: var(--font-mono); color: #FFF;">-$${avgMAE.toFixed(2)}</span>
            </div>
        </div>
        <div style="background: rgba(0, 229, 255, 0.05); border: 1px solid rgba(0, 229, 255, 0.2); border-radius: 8px; padding: 10px; display: flex; justify-content: space-between; align-items: center;">
            <div>
                <span style="font-size: 11px; color: var(--color-cyan); font-weight: 700; display: block;">Índice de Eficiência R/R</span>
                <span style="font-size: 10px; color: var(--text-muted);">Proporção de captura de lucro vs risco suportado</span>
            </div>
            <span style="font-size: 18px; font-weight: 800; font-family: var(--font-mono); color: var(--color-cyan);">${ratio.toFixed(2)}x</span>
        </div>
    `;
}

// ==========================================================================
// CRUZAMENTOS DE SCORE NOS 28 PARES FOREX (BASE x QUOTE)
// ==========================================================================

function setupCrossoversModal() {
    const modal = document.getElementById("crossoversModal");
    const btnOpen = document.getElementById("btnOpenCrossoversModal");
    const btnClose = document.getElementById("btnCloseCrossoversModal");

    state.crossoversActiveTF = "H1";
    state.crossoversActiveTab = "cross-live";
    state.crossoversCurrencyFilter = "ALL";
    state.isCrossoversModalOpen = false;

    if (btnOpen && modal) {
        btnOpen.addEventListener("click", () => {
            modal.classList.remove("hidden");
            state.isCrossoversModalOpen = true;
            renderCrossoversModal();
        });
    }

    if (btnClose && modal) {
        btnClose.addEventListener("click", () => {
            modal.classList.add("hidden");
            state.isCrossoversModalOpen = false;
        });
    }

    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.add("hidden");
                state.isCrossoversModalOpen = false;
            }
        });
    }

    // 1. Timeframe Tabs
    const tfTabs = document.getElementById("crossoversTFTabs");
    if (tfTabs) {
        tfTabs.querySelectorAll(".tf-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                tfTabs.querySelectorAll(".tf-tab").forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                state.crossoversActiveTF = tab.dataset.tf;
                renderCrossoversModal();
            });
        });
    }

    // 2. Sub Navigation Tabs (Live, Spread, Filter)
    document.querySelectorAll("#crossoversModal .cross-nav-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll("#crossoversModal .cross-nav-tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".cross-tab-pane").forEach(p => p.style.display = "none");

            tab.classList.add("active");
            const targetTab = tab.dataset.tab;
            state.crossoversActiveTab = targetTab;

            if (targetTab === "cross-live") {
                const pane = document.getElementById("paneCrossLive");
                if (pane) pane.style.display = "block";
            } else if (targetTab === "cross-spread") {
                const pane = document.getElementById("paneCrossSpread");
                if (pane) pane.style.display = "block";
            } else if (targetTab === "cross-filter") {
                const pane = document.getElementById("paneCrossFilter");
                if (pane) pane.style.display = "block";
            }
            renderCrossoversModal();
        });
    });

    // 3. Currency Filter Pills
    const filterContainer = document.getElementById("crossCurrencyFilterPills");
    if (filterContainer) {
        filterContainer.querySelectorAll(".filter-pill").forEach(pill => {
            pill.addEventListener("click", () => {
                filterContainer.querySelectorAll(".filter-pill").forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                state.crossoversCurrencyFilter = pill.dataset.ccy;
                renderCrossoversFilterTab();
            });
        });
    }
}

function renderCrossoversModal() {
    if (!state.data || !state.data.crossovers) return;

    if (state.crossoversActiveTab === "cross-live") {
        renderCrossoversLiveTab();
    } else if (state.crossoversActiveTab === "cross-spread") {
        renderCrossoversSpreadTab();
    } else if (state.crossoversActiveTab === "cross-filter") {
        renderCrossoversFilterTab();
    }
}

function renderCrossoversLiveTab() {
    const container = document.getElementById("crossoversGrid");
    if (!container || !state.data?.crossovers) return;

    const tf = state.crossoversActiveTF || "H1";
    const tfData = state.data.crossovers.timeframes?.[tf];
    const rawCrossovers = tfData?.crossovers || [];
    // Filtro estrito: Apenas sinais com no máximo 8 horas de idade
    const crossovers = rawCrossovers.filter(c => c.bars_ago <= 8);

    if (crossovers.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; padding: 30px; text-align: center; color: var(--text-muted); background: #0E131E; border-radius: 8px;">
                Nenhum cruzamento recente (≤ 8h) detectado no timeframe ${tf}.
            </div>
        `;
        return;
    }

    container.innerHTML = crossovers.map(cross => {
        const isBuy = cross.direction === "BUY";
        const dirClass = isBuy ? "buy" : "sell";
        const freshClass = cross.is_fresh ? "fresh" : "";
        const sign = cross.current_spread >= 0 ? "+" : "";

        return `
            <div class="crossover-card ${dirClass} ${freshClass}">
                <div class="crossover-card-header">
                    <div class="crossover-pair-title">
                        <span>${cross.base_flag}${cross.quote_flag}</span>
                        <span>${cross.pair}</span>
                        <span class="tf-tab" style="font-size: 9px; padding: 1px 5px;">${cross.timeframe}</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        ${cross.is_fresh ? '<span class="pulse-dot live" style="background: #FFD700;" title="Cruzamento recente!"></span>' : ''}
                        <span class="crossover-badge ${dirClass}">
                            ${cross.direction_label}
                        </span>
                    </div>
                </div>

                <div class="crossover-metrics-row">
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Score ${cross.base}</span>
                        <span class="crossover-metric-value" style="color: ${CCY_COLORS[cross.base] || '#FFF'};">
                            ${(cross.current_base_score >= 0 ? "+" : "") + cross.current_base_score.toFixed(2)}
                        </span>
                    </div>
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Score ${cross.quote}</span>
                        <span class="crossover-metric-value" style="color: ${CCY_COLORS[cross.quote] || '#FFF'};">
                            ${(cross.current_quote_score >= 0 ? "+" : "") + cross.current_quote_score.toFixed(2)}
                        </span>
                    </div>
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Spread Atual</span>
                        <span class="crossover-metric-value ${cross.current_spread >= 0 ? 'positive' : 'negative'}">
                            ${sign + cross.current_spread.toFixed(2)}
                        </span>
                    </div>
                </div>

                <div style="font-size: 11px; color: var(--text-secondary);">
                    💡 <strong>Diagnóstico:</strong> ${cross.action_thesis} (${cross.region}).
                </div>

                <div class="crossover-footer">
                    <span>🕒 Cruzou em: <strong>${cross.timestamp}</strong></span>
                    <span>⏱️ <strong>${cross.bars_ago === 0 ? '🔥 Barra Atual' : cross.bars_ago + ' barras atrás'}</strong></span>
                </div>
            </div>
        `;
    }).join("");
}

function renderCrossoversSpreadTab() {
    const tbody = document.getElementById("crossoversSpreadTableBody");
    if (!tbody || !state.data?.crossovers) return;

    const tf = state.crossoversActiveTF || "H1";
    const tfData = state.data.crossovers.timeframes?.[tf];
    const rankings = tfData?.spread_ranking || [];

    if (rankings.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">Nenhum dado de spread disponível.</td></tr>`;
        return;
    }

    tbody.innerHTML = rankings.map((item, idx) => {
        const isBuy = item.bias === "BUY";
        const sign = item.spread >= 0 ? "+" : "";

        return `
            <tr>
                <td style="color: var(--text-muted); font-family: var(--font-mono);">${idx + 1}</td>
                <td>
                    <div class="pair-badge-cell">
                        <span>${item.base_flag}${item.quote_flag}</span>
                        <span style="color: #FFFFFF; font-weight: 700;">${item.pair}</span>
                    </div>
                </td>
                <td>
                    <span class="crossover-badge ${isBuy ? 'buy' : 'sell'}" style="font-size: 10px;">
                        ${isBuy ? '🟢 COMPRA (Base > Quote)' : '🔴 VENDA (Quote > Base)'}
                    </span>
                </td>
                <td class="score-cell text-right" style="color: ${CCY_COLORS[item.base] || '#FFF'}; font-weight: 700;">
                    ${(item.current_base_score >= 0 ? "+" : "") + item.current_base_score.toFixed(2)}
                </td>
                <td class="score-cell text-right" style="color: ${CCY_COLORS[item.quote] || '#FFF'}; font-weight: 700;">
                    ${(item.current_quote_score >= 0 ? "+" : "") + item.current_quote_score.toFixed(2)}
                </td>
                <td class="score-cell ${item.spread >= 0 ? 'positive' : 'negative'} text-right" style="font-weight: 800; font-size: 12.5px;">
                    ${sign + item.spread.toFixed(2)}
                </td>
                <td style="font-weight: 800; color: ${CCY_COLORS[item.leader] || '#FFF'};">
                    ${item.leader} (${CCY_FLAGS[item.leader] || ''})
                </td>
                <td style="font-size: 11px; color: var(--text-secondary);">
                    ${item.leader} lidera com diferencial de ${(Math.abs(item.spread)).toFixed(2)} pts.
                </td>
            </tr>
        `;
    }).join("");
}

function renderCrossoversFilterTab() {
    const container = document.getElementById("crossoversFilterGrid");
    if (!container || !state.data?.crossovers) return;

    const tf = state.crossoversActiveTF || "H1";
    const ccy = state.crossoversCurrencyFilter || "ALL";
    const tfData = state.data.crossovers.timeframes?.[tf];
    let crossovers = (tfData?.crossovers || []).filter(c => c.bars_ago <= 8);

    if (ccy !== "ALL") {
        crossovers = crossovers.filter(c => c.base === ccy || c.quote === ccy);
    }

    if (crossovers.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; padding: 30px; text-align: center; color: var(--text-muted); background: #0E131E; border-radius: 8px;">
                Nenhum cruzamento recente (≤ 8h) encontrado para ${ccy} no timeframe ${tf}.
            </div>
        `;
        return;
    }

    container.innerHTML = crossovers.map(cross => {
        const isBuy = cross.direction === "BUY";
        const dirClass = isBuy ? "buy" : "sell";
        const freshClass = cross.is_fresh ? "fresh" : "";
        const sign = cross.current_spread >= 0 ? "+" : "";

        return `
            <div class="crossover-card ${dirClass} ${freshClass}">
                <div class="crossover-card-header">
                    <div class="crossover-pair-title">
                        <span>${cross.base_flag}${cross.quote_flag}</span>
                        <span>${cross.pair}</span>
                        <span class="tf-tab" style="font-size: 9px; padding: 1px 5px;">${cross.timeframe}</span>
                    </div>
                    <span class="crossover-badge ${dirClass}">
                        ${cross.direction_label}
                    </span>
                </div>

                <div class="crossover-metrics-row">
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Score ${cross.base}</span>
                        <span class="crossover-metric-value" style="color: ${CCY_COLORS[cross.base] || '#FFF'};">
                            ${(cross.current_base_score >= 0 ? "+" : "") + cross.current_base_score.toFixed(2)}
                        </span>
                    </div>
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Score ${cross.quote}</span>
                        <span class="crossover-metric-value" style="color: ${CCY_COLORS[cross.quote] || '#FFF'};">
                            ${(cross.current_quote_score >= 0 ? "+" : "") + cross.current_quote_score.toFixed(2)}
                        </span>
                    </div>
                    <div class="crossover-metric-item">
                        <span class="crossover-metric-label">Spread</span>
                        <span class="crossover-metric-value ${cross.current_spread >= 0 ? 'positive' : 'negative'}">
                            ${sign + cross.current_spread.toFixed(2)}
                        </span>
                    </div>
                </div>

                <div style="font-size: 11px; color: var(--text-secondary);">
                    💡 <strong>Diagnóstico:</strong> ${cross.action_thesis} (${cross.region}).
                </div>

                <div class="crossover-footer">
                    <span>🕒 Cruzou em: <strong>${cross.timestamp}</strong></span>
                    <span>⏱️ <strong>${cross.bars_ago === 0 ? '🔥 Barra Atual' : cross.bars_ago + ' barras atrás'}</strong></span>
                </div>
            </div>
        `;
    }).join("");
}


