function dbtcovApp() {
  return {
    view: 'projects',
    modal: null,
    modalError: '',
    projects: [],
    selectedProject: null,
    runs: [],
    selectedRun: null,
    selectedModel: null,
    modelPanelOpen: false,
    findings: [],
    findingsTotal: 0,
    findingRuleIds: [],
    findingFilter: { rule_id: '', severity: '' },
    coverageDims: [],
    meta: { rules: {}, dimensions: {}, config_fields: {}, column_tooltips: {} },
    newProject: { name: '', path: '' },
    renderMode: 'auto',
    scanning: false,
    pollTimer: null,
    configYaml: '',
    configPath: '',
    configTab: 'form',
    configError: '',
    form: { render: {}, complexity: {}, coverage: {}, gate: {}, rules: {}, scoring: {} },
    scoringDefaults: {
      no_test_penalty: 25, doc_penalty_max: 15,
      tier1_per_finding: 10, tier1_cap: 40,
      tier2_per_finding: 3, tier2_cap: 20,
      unexec_per_test: 5, unexec_cap: 15,
      parse_fail_penalty: 10, parse_uncertain_penalty: 5,
      skip_cap: 5,
    },
    ruleSearch: '',
    perModelRows: [],
    perModelDimensions: [],
    perModelSearch: '',
    perModelDimFilter: '',
    perModelSortBy: 'score',
    perModelLimit: 100,
    trendChartObj: null,
    coverageChartObj: null,

    get filteredRuleIds() {
      const q = (this.ruleSearch || '').toLowerCase();
      const ids = Object.keys(this.meta.rules || {});
      if (!q) return ids;
      return ids.filter(id => {
        const r = this.meta.rules[id] || {};
        return id.toLowerCase().includes(q)
          || (r.name || '').toLowerCase().includes(q)
          || (r.category || '').toLowerCase().includes(q);
      });
    },

    get perModelFiltered() {
      const q = (this.perModelSearch || '').toLowerCase();
      let rows = this.perModelRows;
      if (q) rows = rows.filter(m => m.name.toLowerCase().includes(q) || (m.file_path || '').toLowerCase().includes(q));
      if (this.perModelDimFilter) {
        const d = this.perModelDimFilter;
        rows = rows.filter(m => {
          const ct = m.dims[d];
          return ct && ct[1] > 0 && ct[0] < ct[1]; // not fully covered
        });
      }
      const sortBy = this.perModelSortBy;
      const sorted = [...rows];
      if (sortBy === 'score') sorted.sort((a, b) => (a.score ?? 999) - (b.score ?? 999));
      else if (sortBy === '-score') sorted.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
      else if (sortBy === 'name') sorted.sort((a, b) => a.name.localeCompare(b.name));
      else if (sortBy === 'findings') sorted.sort((a, b) => (b.finding_count || 0) - (a.finding_count || 0));
      return sorted;
    },

    get modelFindings() {
      if (!this.selectedModel) return [];
      const nid = this.selectedModel.node_id;
      return this.findings.filter(f => f.node_id === nid);
    },

    async init() {
      this.meta = await fetch('/api/meta').then(r => r.json());
      await this.loadProjects();
      // simple hash routing
      window.addEventListener('hashchange', () => this.handleHash());
      this.handleHash();
    },

    handleHash() {
      const m = location.hash.match(/^#\/project\/([^\/]+)(\/run\/(.+))?$/);
      if (!m) { this.view = 'projects'; return; }
      const pid = m[1], rid = m[3];
      this.openProject(pid, rid);
    },

    // ---- projects ----
    async loadProjects() {
      this.projects = await fetch('/api/projects').then(r => r.json());
    },
    openRegister() { this.modal = 'register'; this.modalError = ''; this.newProject = { name: '', path: '' }; },
    async createProject() {
      this.modalError = '';
      try {
        const r = await fetch('/api/projects', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(this.newProject),
        });
        if (!r.ok) { this.modalError = (await r.json()).detail; return; }
        this.modal = null;
        await this.loadProjects();
      } catch (e) { this.modalError = e.message; }
    },
    async deleteProject(id) {
      if (!confirm('Delete this project and all run history?')) return;
      await fetch('/api/projects/' + id, { method: 'DELETE' });
      await this.loadProjects();
    },

    // ---- project detail ----
    async openProject(id, runId) {
      await this.loadProject(id);
      this.view = 'project';
      if (runId) await this.openRun(runId);
    },
    async loadProject(id) {
      this.selectedProject = await fetch('/api/projects/' + id).then(r => r.json());
      this.runs = this.selectedProject.runs || [];
      this.scanning = this.runs.some(r => r.status === 'running');
      if (this.scanning) this.startPolling();
      this.$nextTick(() => this.drawCharts());
    },
    async runScan() {
      this.scanning = true;
      const r = await fetch(`/api/projects/${this.selectedProject.id}/scan`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ render_mode: this.renderMode }),
      });
      if (!r.ok) { alert('Scan failed: ' + await r.text()); this.scanning = false; return; }
      this.startPolling();
    },
    startPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      this.pollTimer = setInterval(async () => {
        await this.loadProject(this.selectedProject.id);
        if (!this.runs.some(r => r.status === 'running')) {
          clearInterval(this.pollTimer); this.pollTimer = null; this.scanning = false;
        }
      }, 3000);
    },
    async drawCharts() {
      const trend = await fetch(`/api/projects/${this.selectedProject.id}/trend`).then(r => r.json());
      const labels = trend.map(t => new Date(t.started_at).toLocaleDateString());
      // score chart
      if (this.trendChartObj) this.trendChartObj.destroy();
      this.trendChartObj = new Chart(this.$refs.trendChart, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Mean Score', data: trend.map(t=>t.score_mean), borderColor:'#059669', tension:0.3, yAxisID:'y' },
            { label: 'Findings', data: trend.map(t=>t.findings_total), borderColor:'#dc2626', tension:0.3, yAxisID:'y1' },
            { label: 'Critical', data: trend.map(t=>t.findings_critical), borderColor:'#7c2d12', tension:0.3, yAxisID:'y1' },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            y: { beginAtZero: true, max: 100, position:'left', title:{display:true, text:'Score'} },
            y1: { beginAtZero: true, position:'right', grid:{drawOnChartArea:false}, title:{display:true, text:'Findings'} },
          },
        },
      });
      if (this.coverageChartObj) this.coverageChartObj.destroy();
      this.coverageChartObj = new Chart(this.$refs.coverageChart, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Model Data Test', data: trend.map(t=>t.coverage_test), borderColor:'#0ea5e9', tension:0.3 },
            { label: 'Documentation', data: trend.map(t=>t.coverage_doc), borderColor:'#8b5cf6', tension:0.3 },
            { label: 'Model Unit Test', data: trend.map(t=>t.coverage_test_unit), borderColor:'#f59e0b', tension:0.3 },
            { label: 'Columnar Data Test', data: trend.map(t=>t.coverage_column_test), borderColor:'#10b981', tension:0.3 },
            { label: 'Columnar Meaningful', data: trend.map(t=>t.coverage_column_test_meaningful), borderColor:'#6366f1', tension:0.3 },
            { label: 'Unit CC-Weighted', data: trend.map(t=>t.coverage_test_unit_weighted_cc), borderColor:'#f43f5e', tension:0.3 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: { y: { beginAtZero: true, max: 1, ticks:{callback: v=>(v*100)+'%'} } },
        },
      });
    },

    // ---- run detail ----
    async openRun(rid) {
      this.selectedRun = await fetch(`/api/projects/${this.selectedProject.id}/runs/${rid}`).then(r => r.json());
      // For failed runs, skip artifact fetches — just show the error banner
      if (this.selectedRun.status === 'failed') {
        this.coverageDims = [];
        this.perModelRows = [];
        this.perModelDimensions = [];
        this.findings = [];
        this.findingsTotal = 0;
        this.findingRuleIds = [];
        this.view = 'run';
        return;
      }
      const cov = await fetch(`/api/projects/${this.selectedProject.id}/runs/${rid}/coverage`).then(r => r.json());
      this.coverageDims = (cov.coverage || []).filter(c => Number.isFinite(c.ratio));
      // per-model coverage
      const pmc = await fetch(`/api/projects/${this.selectedProject.id}/runs/${rid}/per-model-coverage`).then(r => r.json());
      this.perModelRows = pmc.models || [];
      this.perModelDimensions = pmc.dimensions || [];
      this.perModelSearch = '';
      this.perModelDimFilter = '';
      this.perModelLimit = 100;
      this.findingFilter = { rule_id: '', severity: '' };
      // collect rule ids from full findings list
      const all = await fetch(`/api/projects/${this.selectedProject.id}/runs/${rid}/findings?limit=10000`).then(r => r.json());
      this.findingRuleIds = [...new Set(all.findings.map(f => f.rule_id))].sort();
      await this.loadFindings();
      this.view = 'run';
    },
    async loadFindings() {
      const q = new URLSearchParams();
      if (this.findingFilter.rule_id) q.set('rule_id', this.findingFilter.rule_id);
      if (this.findingFilter.severity) q.set('severity', this.findingFilter.severity);
      q.set('limit', '500');
      const r = await fetch(`/api/projects/${this.selectedProject.id}/runs/${this.selectedRun.id}/findings?${q}`).then(r => r.json());
      this.findings = r.findings;
      this.findingsTotal = r.total;
    },

    // ---- config ----
    async openConfig() {
      const r = await fetch(`/api/projects/${this.selectedProject.id}/config`).then(r => r.json());
      this.configPath = r.path;
      this.configYaml = r.yaml || (await fetch('/api/config-template').then(r => r.json())).yaml;
      this.configError = '';
      this.configTab = 'form';
      this.syncFormFromYaml();
      this.modal = 'config';
    },
    async loadTemplate() {
      const r = await fetch('/api/config-template').then(r => r.json());
      this.configYaml = r.yaml;
      this.configError = '';
      this.syncFormFromYaml();
    },
    syncFormFromYaml() {
      this.configError = '';
      let parsed = {};
      try {
        parsed = jsyaml.load(this.configYaml) || {};
      } catch (e) {
        this.configError = 'YAML parse error: ' + e.message;
        return;
      }
      this.form = {
        render: parsed.render || { mode: 'AUTO', fallback: 'PARTIAL' },
        dialect: parsed.dialect || 'snowflake',
        confidence_threshold: parsed.confidence_threshold ?? 0.7,
        complexity: parsed.complexity || { threshold_warn: 15, threshold_block: 30, include_jinja: true },
        coverage: parsed.coverage || {},
        gate: parsed.gate || { fail_on_tier: 'TIER_1_ENFORCED', fail_on_coverage_regression: true },
        rules: parsed.rules || {},
        scoring: parsed.scoring || {},
        _raw: parsed,  // preserve other keys (architecture, adapters, overrides, …)
      };
    },
    syncYamlFromForm() {
      this.configError = '';
      const out = { ...(this.form._raw || {}) };
      out.render = this.form.render;
      out.dialect = this.form.dialect;
      out.confidence_threshold = this.form.confidence_threshold;
      out.complexity = { ...(out.complexity || {}), ...this.form.complexity };
      out.coverage = { ...(out.coverage || {}), ...this.form.coverage };
      out.gate = { ...(out.gate || {}), ...this.form.gate };
      out.rules = this.form.rules;
      // Only write scoring keys that differ from defaults to keep yaml clean
      const scoringOut = {};
      for (const [k, v] of Object.entries(this.form.scoring || {})) {
        if (v !== undefined && v !== null) scoringOut[k] = v;
      }
      if (Object.keys(scoringOut).length > 0) out.scoring = { ...(out.scoring || {}), ...scoringOut };
      // remove our private key if it leaked
      delete out._raw;
      try {
        this.configYaml = jsyaml.dump(out, { lineWidth: 120, noRefs: true, sortKeys: false });
      } catch (e) {
        this.configError = 'Could not serialize: ' + e.message;
      }
    },
    async saveConfig() {
      this.configError = '';
      // if we're on the form tab, sync to yaml first
      if (this.configTab === 'form') this.syncYamlFromForm();
      if (this.configError) return;
      const r = await fetch(`/api/projects/${this.selectedProject.id}/config`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ yaml: this.configYaml }),
      });
      if (!r.ok) { this.configError = 'Save failed: ' + (await r.json()).detail; return; }
      this.modal = null;
    },

    // ---- helpers ----
    selectModel(row) {
      this.selectedModel = row;
      this.modelPanelOpen = true;
    },
    scoreBreakdownLabel(reason) {
      const labels = {
        no_test: 'No test declared',
        test_column: 'Column test coverage gap',
        column_test: 'Column test coverage gap',
        meaningful_column: 'Meaningful column test gap',
        unit_cc: 'Unit CC-weighted coverage gap',
        doc: 'Documentation gap',
        tier1: 'Tier-1 findings',
        tier2: 'Tier-2 findings',
        unexec: 'Tests not executed',
        parse: 'Parse failure / uncertain',
        skips: 'Skipped rule checks',
      };
      return labels[reason] || reason;
    },
    fmtDate(s) { return s ? new Date(s).toLocaleString() : ''; },
    scoreColor(s) {
      if (s == null) return 'text-slate-400';
      if (s >= 80) return 'text-emerald-600';
      if (s >= 60) return 'text-amber-600';
      return 'text-red-600';
    },
    ratioColor(r) {
      if (r >= 0.8) return 'bg-emerald-500';
      if (r >= 0.5) return 'bg-amber-500';
      return 'bg-red-500';
    },
    sevColor(s) {
      switch (s) {
        case 'BLOCKER':
        case 'CRITICAL': return 'bg-red-100 text-red-700';
        case 'MAJOR': return 'bg-amber-100 text-amber-700';
        case 'MINOR': return 'bg-blue-100 text-blue-700';
        default: return 'bg-slate-100 text-slate-700';
      }
    },
    catColor(c) {
      const m = {
        QUALITY: 'border-emerald-400',
        PERFORMANCE: 'border-amber-400',
        REFACTOR: 'border-blue-400',
        ARCHITECTURE: 'border-purple-400',
        TESTING: 'border-pink-400',
        SECURITY: 'border-red-400',
        GOVERNANCE: 'border-slate-400',
      };
      return m[c] || 'border-slate-300';
    },
    dimCell(m, dim) {
      const ct = m.dims[dim];
      if (!ct || ct[1] === 0) return '<span class="text-slate-300">—</span>';
      const [c, t] = ct;
      if (t === 1) {
        // boolean dimension (per-model)
        return c >= 1
          ? '<span class="text-emerald-600 font-bold">✓</span>'
          : '<span class="text-red-500 font-bold">✗</span>';
      }
      // ratio dimension (e.g. doc coverage by columns)
      const ratio = c / t;
      const colorCls = ratio >= 0.8 ? 'text-emerald-600' : ratio >= 0.5 ? 'text-amber-600' : 'text-red-600';
      return `<span class="${colorCls} font-mono text-xs">${(ratio*100).toFixed(0)}%</span>`;
    },
    dimCellTitle(m, dim) {
      const ct = m.dims[dim];
      if (!ct) return 'No data';
      const name = this.meta.dimensions[dim]?.name || dim;
      return `${name}: ${ct[0]} / ${ct[1]}`;
    },
  };
}
