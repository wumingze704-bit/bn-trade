// trade 看板前端逻辑（Alpine.js）。
// 规格：docs/10-可视化看板.md §4、§5.0、§5.1、§5.5、§6
/* global Alpine, Plotly */

function emptyKpis() {
  return {
    equity: { value: null, initial: null, change: null, change_pct: null, spark: [] },
    today_pnl: { value: null, pct: null, daily_loss_stop: null, stop_usdt: null, remaining_usdt: null },
    positions: { count: 0, max_positions: null, margin_used: 0, margin_ratio: 0 },
    today_trades: { count: 0, wins: 0, losses: 0, profit_factor: null, avg_bp: null },
  };
}

function dashboard() {
  return {
    page: "overview",
    researchTab: "reports",
    liveAvailable: false,
    status: {},
    overview: { positions: [], orders: [], events: [] },
    kpis: emptyKpis(),
    scan: { cycle: null, states: [] },
    equityRange: "1D",
    equityPoints: [],
    reports: [],
    selectedReport: null,
    models: [],
    universeMonths: [],
    universeMonth: null,
    universeSnapshot: null,
    universeChanges: [],
    universeRule: null,
    universeReport: null,
    quality: null,
    datasets: [],
    wsConnected: false,
    clockBeijing: "",
    clockUtc: "",
    nextCloseLabel: "--",
    nowTick: Date.now(),
    litCount: 0,
    scanTimer: null,
    scannedCycle: null,
    nav: [
      { key: "overview", label: "总览" },
      { key: "positions", label: "持仓与挂单" },
      { key: "trades", label: "交易记录" },
      { key: "performance", label: "绩效" },
      { key: "research", label: "回测与研究" },
      { key: "control", label: "控制" },
    ],

    get pageTitle() {
      const entry = this.nav.find((item) => item.key === this.page);
      return entry ? entry.label : "总览";
    },

    get isLivePlaceholder() {
      return ["positions", "trades", "performance", "control"].includes(this.page);
    },

    get modeLabel() {
      const mode = this.status && this.status.mode;
      return mode ? String(mode).toUpperCase() : "研究";
    },

    get modeClass() {
      const mode = this.status && this.status.mode;
      return mode === "live" ? "live" : mode === "shadow" ? "shadow" : mode === "paper" ? "paper" : "";
    },

    get heartbeatSeconds() {
      const beat = this.status && this.status.heartbeat_ms;
      if (!beat) return Infinity;
      return Math.max(0, Math.floor((this.nowTick - beat) / 1000));
    },

    get heartbeatLabel() {
      if (!this.status || !this.status.heartbeat_ms) return "心跳 —";
      return `心跳 ${this.heartbeatSeconds} 秒`;
    },

    get modelLabel() {
      const modelId = this.status && this.status.model_id;
      return modelId ? `模型 ${modelId}` : "模型 —";
    },

    get equitySubLabel() {
      const equity = this.kpis.equity;
      if (equity.change === null || equity.change === undefined) return "自启动 —";
      const sign = equity.change >= 0 ? "+" : "";
      return `${sign}${this.fmtMoney(equity.change)} · ${sign}${this.fmtPct(equity.change_pct)} 自启动`;
    },

    get todayPnlSubLabel() {
      const pnl = this.kpis.today_pnl;
      const pct = this.fmtPct(pnl.pct);
      if (pnl.daily_loss_stop === null || pnl.daily_loss_stop === undefined) {
        return `${pct} · 日亏损停止线 —`;
      }
      const stop = Math.round(Number(pnl.daily_loss_stop) * 100);
      return `${pct} · 日亏损停止线 −${stop}% · 距停止线 ${this.fmtMoney(pnl.remaining_usdt)}`;
    },

    get marginSubLabel() {
      const positions = this.kpis.positions;
      const ratio = Math.round(Number(positions.margin_ratio || 0) * 100);
      return `保证金占用 ${ratio}%`;
    },

    get todayTradesSubLabel() {
      const trades = this.kpis.today_trades;
      const pf = trades.profit_factor === null ? "—" : Number(trades.profit_factor).toFixed(2);
      return `PF ${pf} · 单笔期望 ${Number(trades.avg_bp || 0).toFixed(1)} bp`;
    },

    get ringStyle() {
      const ratio = Math.min(1, Math.max(0, Number(this.kpis.positions.margin_ratio || 0)));
      return `stroke-dashoffset: ${101 - 101 * ratio}`;
    },

    get sparkPoints() {
      const points = (this.kpis.equity.spark || []).map((item) => Number(item.equity));
      if (points.length < 2) return "0,28 240,28";
      const min = Math.min(...points);
      const max = Math.max(...points);
      const span = max - min || 1;
      return points
        .map((value, index) => {
          const x = (index / (points.length - 1)) * 240;
          const y = 28 - ((value - min) / span) * 24;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
    },

    get equityAlert() {
      if (!this.liveAvailable || this.status.halted) return "";
      const pnl = this.kpis.today_pnl;
      if (pnl.stop_usdt === null || pnl.stop_usdt === undefined) return "";
      if (pnl.remaining_usdt !== null && pnl.remaining_usdt < pnl.stop_usdt * 0.2) {
        return `今日已实现亏损接近日亏损停止线（剩余 ${this.fmtMoney(pnl.remaining_usdt)} USDT）`;
      }
      return "";
    },

    get scanCells() {
      return (this.scan.states || []).slice().sort((a, b) => a.seq - b.seq);
    },

    get scanRangeLabel() {
      const cycle = this.scan.cycle;
      if (!cycle) return "等待扫描数据";
      return `${this.fmtTime(cycle.open_time)} · ${cycle.n_symbols} 个币`;
    },

    get scanStatsLabel() {
      const cycle = this.scan.cycle;
      if (!cycle) return "本轮：暂无统计";
      const duration = Math.max(0, Number(cycle.finished_ms) - Number(cycle.started_ms));
      return (
        `${this.fmtTime(cycle.open_time)} 这一根：已处理 ${this.litCount} / ${cycle.n_symbols}` +
        ` · 预过滤 ${cycle.n_prefilter} · 信号 ${cycle.n_signal} · 开仓 ${cycle.n_opened}` +
        ` · 用时 ${Math.round(duration)} ms`
      );
    },

    get scanProgressStyle() {
      const cycle = this.scan.cycle;
      const total = cycle ? Math.max(1, Number(cycle.n_symbols)) : 1;
      const duration = cycle
        ? Math.max(200, Number(cycle.finished_ms) - Number(cycle.started_ms))
        : 300;
      const ratio = Math.min(1, this.litCount / total);
      return `transform: scaleX(${ratio}); transition-duration: ${duration}ms`;
    },

    get ordersSummary() {
      const orders = this.overview.orders || [];
      const invalid = orders.filter((order) => !order.valid).length;
      return invalid ? `${orders.length} · ${invalid} 张异常` : `${orders.length} · 全部有效`;
    },

    get universeReviewed() {
      return Boolean(this.universeRule && this.universeRule.reviewed);
    },

    get universeSummary() {
      const snapshot = this.universeSnapshot;
      if (!snapshot) return "暂无数据";
      const roles = Object.entries(snapshot.role_counts || {})
        .map(([role, count]) => `${this.roleLabel(role)} ${count}`)
        .join(" · ");
      return `${snapshot.month} · ${snapshot.coins.length} 个币` + (roles ? ` · ${roles}` : "");
    },

    get qualitySummary() {
      const summary = this.quality && this.quality.summary;
      if (!summary) return "";
      return `${summary.n_rows} 行 · ${summary.n_symbols} 个币 · 合格 ${summary.n_qualified}`;

    async init() {
      await Promise.all([this.loadStatus(), this.loadReports(), this.loadModels()]);
      await this.loadOverview();
      await this.loadScan(true);
      await this.loadEquity(this.equityRange);
      this.connectWs();
      this.startClock();
      // 实时数据轮询（10 §7：WebSocket 推送 status；总览数据每秒轮询状态库）
      setInterval(() => {
        if (!this.liveAvailable) return;
        this.loadOverview();
        this.loadScan(false);
      }, 5000);
    },

    setPage(page) {
      this.page = page;
      if (page === "research" && this.researchTab === "data") this.loadResearchData();
    },

    setResearchTab(tab) {
      this.researchTab = tab;
      if (tab === "data") this.loadResearchData();
    },

    async loadStatus() {
      const res = await fetch("/api/status");
      this.status = await res.json();
      this.liveAvailable = Boolean(this.status.live_available);
    },

    async loadOverview() {
      const res = await fetch("/api/overview");
      const data = await res.json();
      this.liveAvailable = Boolean(data.live_available);
      this.overview = {
        positions: data.positions || [],
        orders: data.orders || [],
        events: data.events || [],
      };
      this.kpis = data.kpis || emptyKpis();
    },

    async loadScan(initial) {
      const current = this.scan.cycle ? this.scan.cycle.open_time : null;
      const res = await fetch("/api/scan");
      const data = await res.json();
      this.scan = { cycle: data.cycle, states: data.states || [] };
      const next = data.cycle ? data.cycle.open_time : null;
      const isNew = next !== null && next !== current && current !== null;
      if (initial || !isNew) {
        this.litCount = (data.states || []).length;
      } else {
        this.animateScan(data.cycle);
      }
      this.scannedCycle = next;
    },

    animateScan(cycle) {
      const total = Math.max(1, Number(cycle.n_symbols));
      const duration = Math.max(200, Number(cycle.finished_ms) - Number(cycle.started_ms));
      if (this.scanTimer) clearTimeout(this.scanTimer);
      this.litCount = 0;
      const stepMs = Math.max(12, Math.min(120, duration / total));
      const tick = () => {
        if (this.litCount >= total) return;
        this.litCount += 1;
        this.scanTimer = setTimeout(tick, stepMs);
      };
      tick();
    },

    async loadEquity(rangeKey) {
      const res = await fetch(`/api/equity?range=${encodeURIComponent(rangeKey)}`);
      const data = await res.json();
      this.equityPoints = data.points || [];
      this.$nextTick(() => this.renderOverviewEquity());
    },

    setEquityRange(rangeKey) {
      this.equityRange = rangeKey;
      this.loadEquity(rangeKey);
    },

    renderOverviewEquity() {
      const element = this.$refs.overviewEquity;
      if (!element) return;
      if (!this.equityPoints.length) {
        Plotly.purge(element);
        return;
      }
      const x = this.equityPoints.map((point) => point.ts_ms);
      const y = this.equityPoints.map((point) => point.equity);
      Plotly.react(element, [{
        x,
        y,
        type: "scatter",
        mode: "lines",
        line: { color: "#30D158", width: 2.4 },
        fill: "tozeroy",
        fillcolor: "rgba(48, 209, 88, 0.14)",
        hovertemplate: "%{x|%m-%d %H:%M}<br>%{y:.4f} USDT<extra></extra>",
      }], this.plotlyLayout(false), { responsive: true, displaylogo: false });
    },

    async loadReports() {
      const res = await fetch("/api/reports");
      const data = await res.json();
      this.reports = data.reports || [];
      if (this.reports.length) this.selectReport(this.reports[0].run_id);
    },

    async loadModels() {
      const res = await fetch("/api/models");
      const data = await res.json();
      this.models = data.models || [];
    },

    async selectReport(runId) {
      const res = await fetch(`/api/reports/${encodeURIComponent(runId)}`);
      if (!res.ok) return;
      this.selectedReport = await res.json();
      this.$nextTick(() => this.renderReportEquity());
    },

    renderReportEquity() {
      const element = this.$refs.equityChart;
      if (!element || !this.selectedReport || !this.selectedReport.equity.length) return;
      const traces = this.selectedReport.equity.map((series) => ({
        x: series.points.map((point) => point.ts_ms),
        y: series.points.map((point) => point.equity),
        type: "scatter",
        mode: "lines",
        name: `${series.run}/${series.scenario}`,
      }));
      Plotly.react(element, traces, this.plotlyLayout(true), {
        responsive: true,

    async loadResearchData() {
      await Promise.all([this.loadUniverse(), this.loadQuality(), this.loadDatasets()]);
    },

    async loadUniverse(month) {
      const query = month ? `?month=${encodeURIComponent(month)}` : "";
      const res = await fetch(`/api/universe${query}`);
      const data = await res.json();
      this.universeMonths = data.months || [];
      this.universeMonth = data.selected || null;
      this.universeSnapshot = data.snapshot || null;
      this.universeChanges = data.changes || [];
      this.universeRule = data.rule || null;
      this.universeReport = data.report_md || null;
    },

    async loadQuality() {
      const res = await fetch("/api/quality");
      const data = await res.json();
      this.quality = data.quality || null;
    },

    async loadDatasets() {
      const res = await fetch("/api/datasets");
      const data = await res.json();
      this.datasets = data.datasets || [];
    },

    connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => {
        this.wsConnected = true;
      };
      ws.onmessage = (event) => {
        this.status = JSON.parse(event.data);
        this.liveAvailable = Boolean(this.status.live_available);
      };
      ws.onclose = () => {
        this.wsConnected = false;
        setTimeout(() => this.connectWs(), 3000);
      };
    },

    startClock() {
      const tick = () => {
        const now = new Date();
        this.nowTick = now.getTime();
        this.clockBeijing =
          new Intl.DateTimeFormat("zh-CN", {
            timeZone: "Asia/Shanghai",
            hour12: false,
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }).format(now) + " 北京";
        this.clockUtc =
          new Intl.DateTimeFormat("zh-CN", {
            timeZone: "UTC",
            hour12: false,
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }).format(now) + " UTC";
        this.nextCloseLabel = `${60 - now.getUTCSeconds()} 秒`;
      };
      tick();
      setInterval(tick, 1000);
    },

    cellClass(cell) {
      const classes = [];
      const index = this.scanCells.indexOf(cell);
      if (index < this.litCount) classes.push("lit");
      const status = String(cell.status || "");
      if (status === "watching") classes.push("cell-watching");
      else if (status === "exit_only" || status === "blocked") classes.push("cell-blocked");
      if (cell.prefilter === 1) classes.push("cell-prefilter");
      if (cell.decision === "open") classes.push("cell-opened");
      else if (cell.decision === "skip") classes.push("cell-skip");
      else if (cell.prefilter === 1 && cell.p !== null && cell.p !== undefined) {
        classes.push("cell-signal");
      }
      if (this.scanCells.indexOf(cell) === this.litCount) classes.push("cell-current");
      return classes.join(" ");
    },

    cellStyle(cell, index) {
      const cycle = this.scan.cycle;
      const total = cycle ? Math.max(1, Number(cycle.n_symbols)) : 1;
      const duration = cycle
        ? Math.max(200, Number(cycle.finished_ms) - Number(cycle.started_ms))
        : 300;
      return `--delay: ${Math.round((index / total) * duration)}ms`;
    },

    cellRingStyle(cell) {
      const p = cell && cell.p !== null && cell.p !== undefined ? Number(cell.p) : 0;
      return `--p: ${Math.max(0, Math.min(1, p)) * 100}%`;
    },

    eventLevelClass(level) {
      const value = String(level || "").toLowerCase();
      if (value === "error" || value === "critical") return "red";
      if (value === "warn" || value === "warning") return "orange";
      if (value === "info") return "blue";
      return "green";
    },

    eventMeta(event) {

    stopKindLabel(kind) {
      return { initial: "初始止损", breakeven: "保本", trail: "追踪" }[kind] || "止损";
    },

    orderTypeLabel(type) {
      const value = String(type || "").toUpperCase();
      const labels = {
        STOP_MARKET: "止损",
        TAKE_PROFIT_MARKET: "止盈",
        LIMIT: "限价",
        MARKET: "市价",
      };
      return labels[value] || value || "订单";
    },

    orderSideLabel(order) {
      const type = String(order.type || "").toUpperCase();
      if (type === "STOP_MARKET") return `条件市价 · 触发 ${this.fmtPrice(order.price)}`;
      if (type === "TAKE_PROFIT_MARKET") return `限价 ${this.fmtPrice(order.price)} · 只减仓`;
      const side = String(order.side || "").toUpperCase();
      return `${side === "SELL" ? "卖出" : "买入"} ${this.fmtPrice(order.price)}`;
    },

    roleLabel(role) {
      return { train: "训练组", holdout: "留出组", new_entry: "新进入" }[role] || role;
    },

    rangeLabel(rangeKey) {
      return { "1D": "1D", "1W": "1W", "1M": "1M", all: "全部" }[rangeKey] || rangeKey;
    },

    shortSymbol(symbol) {
      return String(symbol || "").replace(/USDT$/, "");
    },

    shortHash(value) {
      if (!value) return "—";
      return String(value).slice(0, 10);
    },

    sign(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
      return Number(value) >= 0 ? "pos" : "neg";
    },

    fmt(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return String(value);
    },

    fmtMoney(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return Number(value).toFixed(4);
    },

    fmtPrice(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return Number(value).toPrecision(6);
    },

    fmtR(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return `${Number(value).toFixed(2)}R`;
    },

    fmtPct(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return `${(Number(value) * 100).toFixed(2)}%`;
    },

    fmtTime(tsMs) {
      if (!tsMs) return "—";
      return new Date(Number(tsMs)).toLocaleString("zh-CN", {
        hour12: false,
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    },

    formatMtime(mtimeMs) {
      if (!mtimeMs) return "";
      return new Date(mtimeMs).toLocaleString("zh-CN", { hour12: false });
    },
  };
}

      try {
        const detail = event.detail_json ? JSON.parse(event.detail_json) : {};
        const parts = [];
        if (detail.p !== undefined) parts.push(`p ${Number(detail.p).toFixed(2)}`);
        if (detail.reason) parts.push(String(detail.reason));
        if (detail.notional !== undefined) parts.push(`${Number(detail.notional).toFixed(2)} USDT`);
        return parts.join(" · ");
      } catch (error) {
        return "";
      }
    },

        displaylogo: false,
      });
    },

    plotlyLayout(showLegend) {
      return {
        margin: { l: 48, r: 16, t: 16, b: 40 },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#8E8E93" },
        showlegend: showLegend,
        xaxis: { type: "date", gridcolor: "rgba(255,255,255,0.06)" },
        yaxis: { gridcolor: "rgba(255,255,255,0.06)" },
      };
    },

    },

    get qualityRows() {
      return (this.quality && this.quality.rows) || [];
    },

    get reportRows() {
      const summary = (this.selectedReport && this.selectedReport.summary) || {};
      const rows = [];
      Object.keys(summary).forEach((scenario) => {
        const runs = summary[scenario] || {};
        Object.keys(runs).forEach((run) => {
          rows.push({ scenario, run, metrics: runs[run] || {} });
        });
      });
      return rows;
    },


    get sizingLabel() {
      const sizing = this.status && this.status.sizing;
      if (!sizing) return "仓位 —";
      const pct = Math.round(Number(sizing.position_frac) * 100);
      return `${pct}% × ${sizing.leverage}x · 最多 ${sizing.max_positions} 仓`;
    },

    get weightLabel() {
      const usage = this.status && this.status.weight_usage;
      return usage === null || usage === undefined ? "—" : `${Math.round(usage * 100)}%`;
    },
