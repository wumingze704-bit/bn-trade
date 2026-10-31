// trade 看板前端逻辑（Alpine.js）。
// 规格：docs/10-可视化看板.md §4、§5.0、§5.5
/* global Alpine, Plotly */

function dashboard() {
  return {
    page: "research",
    researchTab: "reports",
    liveAvailable: false,
    status: {},
    reports: [],
    selectedReport: null,
    models: [],
    wsConnected: false,
    clockBeijing: "",
    clockUtc: "",
    nav: [
      { key: "overview", label: "总览" },
      { key: "positions", label: "持仓与挂单" },
      { key: "trades", label: "交易记录" },
      { key: "performance", label: "绩效" },
      { key: "research", label: "回测与研究" },
      { key: "control", label: "控制" },
    ],

    get livePages() {
      return this.nav.filter((entry) => entry.key !== "research");
    },

    get modeLabel() {
      return this.status && this.status.mode ? this.status.mode : "—";
    },

    async init() {
      this.loadStatus();
      this.loadReports();
      this.loadModels();
      this.connectWs();
      this.startClock();
    },

    setPage(page) {
      this.page = page;
    },

    async loadStatus() {
      const res = await fetch("/api/status");
      this.status = await res.json();
      this.liveAvailable = this.status.live_available;
    },

    async loadReports() {
      const res = await fetch("/api/reports");
      const data = await res.json();
      this.reports = data.reports || [];
      if (this.reports.length) {
        this.selectReport(this.reports[0].run_id);
      }
    },

    async loadModels() {
      const res = await fetch("/api/models");
      const data = await res.json();
      this.models = data.models || [];
    },

    async selectReport(runId) {
      const res = await fetch(`/api/reports/${encodeURIComponent(runId)}`);
      if (res.ok) {
        this.selectedReport = await res.json();
        this.$nextTick(() => this.renderEquity());
      }
    },

    renderEquity() {
      const el = this.$refs.equityChart;
      if (!el || !this.selectedReport || !this.selectedReport.equity.length) {
        return;
      }
      const traces = this.selectedReport.equity.map((series) => ({
        x: series.points.map((p) => p.ts_ms),
        y: series.points.map((p) => p.equity),
        type: "scatter",
        mode: "lines",
        name: `${series.run}/${series.scenario}`,
      }));
      Plotly.newPlot(el, traces, {
        margin: { l: 48, r: 16, t: 16, b: 40 },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: "#8E8E93" },
        xaxis: { type: "date", gridcolor: "rgba(255,255,255,0.06)" },
        yaxis: { gridcolor: "rgba(255,255,255,0.06)" },
      }, { responsive: true, displaylogo: false });
    },

    connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { this.wsConnected = true; };
      ws.onmessage = (event) => {
        this.status = JSON.parse(event.data);
        this.liveAvailable = this.status.live_available;
      };
      ws.onclose = () => {
        this.wsConnected = false;
        setTimeout(() => this.connectWs(), 3000);
      };
    },

    startClock() {
      const tick = () => {
        const now = new Date();
        this.clockBeijing = new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai", hour12: false,
          month: "2-digit", day: "2-digit", hour: "2-digit",
          minute: "2-digit", second: "2-digit",
        }).format(now) + " 北京";
        this.clockUtc = new Intl.DateTimeFormat("zh-CN", {
          timeZone: "UTC", hour12: false, hour: "2-digit",
          minute: "2-digit", second: "2-digit",
        }).format(now) + " UTC";
      };
      tick();
      setInterval(tick, 1000);
    },

    fmt(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return "—";
      return value;
    },

    fmtMoney(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return "—";
      return Number(value).toFixed(4);
    },

    fmtPct(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return "—";
      return (Number(value) * 100).toFixed(2) + "%";
    },

    formatMtime(mtimeMs) {
      if (!mtimeMs) return "";
      return new Date(mtimeMs).toLocaleString("zh-CN", { hour12: false });
    },
  };
}
