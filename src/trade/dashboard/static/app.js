// trade 看板前端逻辑（Alpine.js）。
// 规格：docs/10-可视化看板.md §4、§5、§6
/* global Plotly */

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
    researchTab: "ledger",
    liveAvailable: false,
    pnlHidden: false,
    status: {},
    overview: { positions: [], orders: [], events: [] },
    kpis: emptyKpis(),
    scan: { cycle: null, states: [] },
    equityRange: "1D",
    equityPoints: [],
    engine: { engines: [], ledger: null },
    candidate: "",
    rebalances: [],
    consistency: [],
    controlState: null,
    halt: null,
    pin: "",
    riskInput: "",
    actionMessage: "",
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
    nowTick: Date.now(),
    litCount: 0,
    scanTimer: null,
    scannedCycle: null,
    nav: [
      { key: "overview", label: "总览" },
      { key: "positions", label: "持仓" },
      { key: "rebalances", label: "调仓记录" },
      { key: "consistency", label: "一致性" },
      { key: "research", label: "研究" },
      { key: "control", label: "控制" },
    ],

    get pageTitle() {
      const entry = this.nav.find((item) => item.key === this.page);
      return entry ? entry.label : "总览";
    },

    get candidateOptions() {
      const seen = new Set();
      const options = [];
      for (const item of this.engine.engines || []) {
        if (item.candidate && !seen.has(item.candidate)) {
          seen.add(item.candidate);
          options.push({ id: item.candidate, label: `${item.candidate} · ${item.mode}` });
        }
      }
      const ledger = this.engine.ledger;
      for (const item of (ledger && ledger.candidates) || []) {
        if (item.id && !seen.has(item.id)) {
          seen.add(item.id);
          options.push({ id: item.id, label: `${item.id} · ${item.final}/${item.ops}` });
        }
      }
      return options;
    },

    get candidateLabel() {
      if (!this.candidate) return "未选择候选";
      const engine = (this.engine.engines || []).find((item) => item.candidate === this.candidate);
      return engine ? `${this.candidate} · ${engine.mode}` : this.candidate;
    },

    get currentEngine() {
      return (this.engine.engines || []).find((item) => item.candidate === this.candidate) || null;
    },

    get modeLabel() {
      const engine = this.currentEngine;
      if (engine && engine.mode) return String(engine.mode).toUpperCase();
      return this.liveAvailable ? "运行" : "研究";
    },

    get modeClass() {
      const mode = this.currentEngine ? this.currentEngine.mode : null;
      return mode === "live" ? "live" : mode === "shadow" ? "shadow" : mode === "paper" ? "paper" : "";
    },

    get engineLabel() {
      const engine = this.currentEngine;
      if (!engine) return "引擎 —";
      return `${engine.version} · pid ${engine.pid ?? "—"}`;
    },

    get heartbeatSeconds() {
      const engine = this.currentEngine;
      const beat = engine ? engine.heartbeat_ms : this.status && this.status.heartbeat_ms;
      if (!beat) return Infinity;
      return Math.max(0, Math.floor((this.nowTick - beat) / 1000));
    },

    get heartbeatLabel() {
      const engine = this.currentEngine;
      const beat = engine ? engine.heartbeat_ms : this.status && this.status.heartbeat_ms;
      if (!beat) return "心跳 —";
      const seconds = this.heartbeatSeconds;
      return seconds < 120 ? `心跳 ${seconds} 秒` : `心跳 ${Math.floor(seconds / 60)} 分钟`;
    },

    get halted() {
      return Boolean(this.halt && this.halt.halted);
    },

    get equityAlert() {
      if (this.pnlHidden) return "虚拟收益在最终检验通过前不展示（盈亏遮蔽）。";
      const pnl = this.kpis.today_pnl;
      if (pnl.value === null || pnl.value === undefined || !pnl.stop_usdt) return "";
      if (pnl.value <= -Math.abs(Number(pnl.stop_usdt)) * 0.8) {
        return `接近日亏损停止线：今日 ${this.fmtMoney(pnl.value)} USDT`;
      }
      return "";
    },

    get equitySubLabel() {
      const equity = this.kpis.equity;
      if (equity.change === null || equity.change === undefined) return "自启动 —";
      const sign = equity.change >= 0 ? "+" : "";
      return `${sign}${this.fmtMoney(equity.change)} · ${sign}${this.fmtPct(equity.change_pct)} 自启动`;
    },

    get todayPnlSubLabel() {
      const pnl = this.kpis.today_pnl;
      if (pnl.value === null || pnl.value === undefined) return "盈亏遮蔽中";
      if (pnl.daily_loss_stop === null || pnl.daily_loss_stop === undefined) return "日亏损停止线 —";
      const stop = Math.round(Number(pnl.daily_loss_stop) * 100);
      return `日亏损停止线 −${stop}% · 距停止线 ${this.fmtMoney(pnl.remaining_usdt)}`;
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
          const y = 30 - ((value - min) / span) * 26;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
    },

    get scanCells() {
      return (this.scan.states || [])
        .slice()
        .sort((a, b) => a.seq - b.seq)
        .map((state) => ({
          symbol: state.alias || state.symbol,
          label: state.decision === "open" ? "目标" : state.skip_reason || "跳过",
          decision: state.decision,
        }));
    },

    get scanSummary() {
      const cycle = this.scan.cycle;
      if (!cycle) return "—";
      return `目标 ${cycle.n_signal || 0} · 成交 ${cycle.n_opened || 0}`;
    },

    get scanRangeLabel() {
      const cycle = this.scan.cycle;
      if (!cycle) return "尚无调仓记录";
      return `调仓整点 ${this.fmtTime(cycle.open_time)} · 处理 ${cycle.n_symbols || 0} 个币`;
    },

    get positionsRows() {
      return (this.overview.positions || []).map((item) => ({
        ...item,
        side_label: Number(item.qty) >= 0 ? "多" : "空",
        stop_ok: Number(item.stop_price) > 0,
      }));
    },

    get ledgerSummary() {
      const ledger = this.engine.ledger;
      if (!ledger) return "暂无总账文件。";
      return `试验 ${ledger.trials_used ?? "—"} / ${ledger.global_trial_budget ?? "—"} · ` +
        `合格池 ${(ledger.qualified_pool || []).length} 个候选`;
    },

    get ledgerRounds() {
      return (this.engine.ledger && this.engine.ledger.rounds) || [];
    },

    get windowRows() {
      return (this.engine.ledger && this.engine.ledger.final_windows) || [];
    },

    get universeRows() {
      const snapshot = this.universeSnapshot;
      if (!snapshot || !snapshot.coins) return [];
      return snapshot.coins.map((coin) => ({
        month: snapshot.month,
        symbol: coin.symbol,
        role: coin.role,
        rank: coin.rank,
        daily_quote_median: coin.daily_quote_median,
      }));
    },

    get universeSummary() {
      if (!this.universeSnapshot) return "暂无交易范围快照。";
      return `${this.universeSnapshot.month} · ${this.universeSnapshot.coins.length} 个币 · ` +
        `规则哈希 ${this.shortHash(this.universeSnapshot.rule_hash)}`;
    },

    get qualityRows() {
      const quality = this.quality;
      return quality && quality.rows ? quality.rows : [];
    },

    get qualitySummary() {
      const quality = this.quality;
      if (!quality || !quality.summary) return "暂无质量缓存。";
      const summary = quality.summary;
      return `${summary.n_rows} 行 · ${summary.n_symbols} 个币 · 合格 ${summary.n_qualified}`;
    },

    get controlSummary() {
      if (!this.candidate) return "请选择候选。";
      const state = this.controlState || {};
      const halt = this.halt ? `停机（${this.halt.reason || "—"}）` : "未停机";
      return `暂停开仓 ${state.pause_open ? "是" : "否"} · 平仓请求 ${state.flatten_requested ? "是" : "否"} · ` +
        `risk_per_trade ${state.risk_per_trade ?? "默认"} · ${halt}`;
    },

    cellStyle(index) {
      const delay = Math.min(600, index * 25);
      return `--delay: ${delay}ms; transform: scaleX(${index < this.litCount ? 1 : 0});`;
    },

    async init() {
      await Promise.all([this.loadStatus(), this.loadEngine(), this.loadReports(), this.loadModels()]);
      if (!this.candidate && this.candidateOptions.length) {
        this.candidate = this.candidateOptions[0].id;
      }
      await Promise.all([
        this.loadOverview(),
        this.loadScan(true),
        this.loadEquity(this.equityRange),
        this.loadRebalances(),
        this.loadConsistency(),
        this.loadControl(),
      ]);
      this.connectWs();
      this.startClock();
      setInterval(() => {
        this.nowTick = Date.now();
        if (!this.liveAvailable) return;
        this.loadEngine();
        this.loadOverview();
        this.loadScan(false);
      }, 5000);
    },

    setPage(page) {
      this.page = page;
      if (page === "rebalances") this.loadRebalances();
      if (page === "consistency") this.loadConsistency();
      if (page === "control") this.loadControl();
      if (page === "research" && this.researchTab === "data") this.loadResearchData();
    },

    setResearchTab(tab) {
      this.researchTab = tab;
      if (tab === "data") this.loadResearchData();
    },

    onCandidateChange() {
      this.litCount = 0;
      this.loadOverview();
      this.loadEquity(this.equityRange);
      this.loadRebalances();
      this.loadConsistency();
      this.loadControl();
    },

    async loadStatus() {
      const res = await fetch("/api/status");
      this.status = await res.json();
      this.liveAvailable = Boolean(this.status.live_available);
    },

    async loadEngine() {
      const res = await fetch("/api/engine");
      this.engine = await res.json();
      if (!this.candidate && this.candidateOptions.length) {
        this.candidate = this.candidateOptions[0].id;
      }
    },

    async loadOverview() {
      const query = this.candidate ? `?candidate=${encodeURIComponent(this.candidate)}` : "";
      const res = await fetch(`/api/overview${query}`);
      const data = await res.json();
      this.liveAvailable = Boolean(data.live_available);
      this.pnlHidden = Boolean(data.pnl_hidden);
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
      const query = this.candidate ? `&candidate=${encodeURIComponent(this.candidate)}` : "";
      const res = await fetch(`/api/equity?range=${encodeURIComponent(rangeKey)}${query}`);
      const data = await res.json();
      this.equityPoints = data.points || [];
      this.pnlHidden = Boolean(data.pnl_hidden) || this.pnlHidden;
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
      Plotly.react(
        element,
        [{
          x,
          y,
          type: "scatter",
          mode: "lines",
          line: { color: "#30D158", width: 2.4 },
          fill: "tozeroy",
          fillcolor: "rgba(48, 209, 88, 0.14)",
          hovertemplate: "%{x|%m-%d %H:%M}<br>%{y:.4f} USDT<extra></extra>",
        }],
        this.plotlyLayout(false),
        { responsive: true, displaylogo: false },
      );
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
      Plotly.react(
        element,
        traces,
        this.plotlyLayout(true),
        { responsive: true, displaylogo: false },
      );
    },

    plotlyLayout(showLegend) {
      return {
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#8e8e93", size: 11 },
        margin: { l: 44, r: 12, t: 12, b: 32 },
        xaxis: { gridcolor: "rgba(255,255,255,0.06)", zeroline: false },
        yaxis: { gridcolor: "rgba(255,255,255,0.06)", zeroline: false },
        showlegend: Boolean(showLegend),
        legend: { orientation: "h", y: 1.12 },
      };
    },

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


    async loadRebalances() {
      if (!this.candidate) {
        this.rebalances = [];
        return;
      }
      const res = await fetch(`/api/rebalances?candidate=${encodeURIComponent(this.candidate)}`);
      const data = await res.json();
      this.rebalances = data.rows || [];
      this.halt = data.halt || null;
    },

    async loadConsistency() {
      if (!this.candidate) {
        this.consistency = [];
        return;
      }
      const res = await fetch(`/api/consistency?candidate=${encodeURIComponent(this.candidate)}`);
      const data = await res.json();
      this.consistency = data.rows || [];
    },

    async loadControl() {
      if (!this.candidate) {
        this.controlState = null;
        this.halt = null;
        return;
      }
      const res = await fetch(`/api/control?candidate=${encodeURIComponent(this.candidate)}`);
      const data = await res.json();
      this.controlState = data.control || null;
      this.halt = data.halt || null;
    },

    async controlAction(action) {
      if (!this.candidate) {
        this.actionMessage = "请先选择候选。";
        return;
      }
      if (!this.pin) {
        this.actionMessage = "请输入 PIN。";
        return;
      }
      const payload = {
        candidate: this.candidate,
        action: action === "set_risk" ? "risk_per_trade" : action,
        pin: this.pin,
      };
      if (action === "set_risk") {
        const value = Number(this.riskInput);
        if (!Number.isFinite(value) || value < 0.005 || value > 0.01) {
          this.actionMessage = "risk_per_trade 必须在 [0.005, 0.01]。";
          return;
        }
        payload.value = value;
      }
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        this.actionMessage = `操作失败：${data.detail || res.status}`;
        return;
      }
      this.actionMessage = "操作已提交，引擎将在下一轮巡检生效。";
      this.controlState = data.control || this.controlState;
      this.halt = data.halt || this.halt;
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
      };
      tick();
      setInterval(tick, 1000);
    },

    fmt(value, digits) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      const precision = digits === undefined ? 4 : digits;
      return Number(value).toFixed(precision);
    },

    fmtMoney(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return Number(value).toFixed(2);
    },

    fmtPct(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return `${(Number(value) * 100).toFixed(2)}%`;
    },

    fmtBp(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return `${Number(value).toFixed(2)} bp`;
    },

    fmtTime(tsMs) {
      if (!tsMs) return "—";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(Number(tsMs)));
    },

    shortHash(value) {
      if (!value) return "—";
      return String(value).slice(0, 10);
    },
  };
}

