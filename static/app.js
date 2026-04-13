/* ═══════════════════════════════════════════════════════════════════
   Virtual Memory Simulator — Frontend Logic
   ═══════════════════════════════════════════════════════════════════ */

(() => {
  "use strict";

  // ── State ─────────────────────────────────────────────────────
  let state = null;
  let autoRunTimer = null;
  let isRunning = false;
  let selectedPtPid = null; // which process's page table to show
  let prevFrameStates = {}; // for pulse animation

  // ── DOM refs ──────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const btnStep = $("btn-step");
  const btnRun = $("btn-run");
  const btnPause = $("btn-pause");
  const btnReset = $("btn-reset");
  const btnLoadScenario = $("btn-load-scenario");
  const btnCreateProcess = $("btn-create-process");
  const btnManualAccess = $("btn-manual-access");
  const scenarioSelect = $("scenario-select");
  const speedSlider = $("speed-slider");
  const speedLabel = $("speed-label");

  // ── API helpers ───────────────────────────────────────────────
  async function api(path, method = "GET", body = null) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    return res.json();
  }

  // ── Init ──────────────────────────────────────────────────────
  async function init() {
    await loadScenarios();
    const data = await api("/api/state");
    state = data;
    render();
  }

  async function loadScenarios() {
    const data = await api("/api/scenarios");
    const scenarios = data.scenarios || [];
    scenarios.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.filename;
      opt.textContent = s.name;
      opt.title = s.description;
      scenarioSelect.appendChild(opt);
    });
  }

  // ── Rendering ─────────────────────────────────────────────────
  function render() {
    if (!state) return;
    renderTime();
    renderStats();
    renderCR3();
    renderFrameGrid();
    renderSwapGrid();
    renderProcesses();
    renderPageTable();
    renderWSClock();
    renderLog();
    renderCharts();
    renderThrashing();
    renderScenarioInfo();
  }

  function renderTime() {
    $("sim-time").textContent = `T = ${state.time}`;
  }

  function renderStats() {
    const s = state.stats;
    $("stat-accesses").textContent = s.total_accesses;
    $("stat-faults").textContent = s.total_page_faults;
    $("stat-fault-rate").textContent = (s.page_fault_rate * 100).toFixed(1) + "%";
    $("stat-tlb-rate").textContent = (s.tlb_hit_rate * 100).toFixed(1) + "%";
    $("stat-evictions").textContent = s.evictions;
    $("stat-swap-io").textContent = `${s.swap_reads}R / ${s.swap_writes}W`;
    $("stat-free-frames").textContent = state.frame_table.free;
    $("stat-ctx-switches").textContent = s.context_switches;
  }

  function renderCR3() {
    const activePid = state.processes.active_pid;
    const cr3El = $("cr3-value");
    const cr3pid = $("cr3-pid");
    if (activePid !== null && state.processes.processes[activePid]) {
      const proc = state.processes.processes[activePid];
      cr3El.textContent = `0x${proc.cr3.toString(16).padStart(4, '0').toUpperCase()}`;
      cr3pid.textContent = `PID ${activePid} — ${proc.name}`;
    } else {
      cr3El.textContent = "—";
      cr3pid.textContent = "No active process";
    }
  }

  // ── Frame grid ────────────────────────────────────────────────
  function renderFrameGrid() {
    const grid = $("frame-grid");
    const frames = state.frame_table.frames;
    const badge = $("frame-badge");
    badge.textContent = `${state.frame_table.allocated} alloc / ${state.frame_table.total} total`;

    // Build HTML
    let html = "";
    frames.forEach((f) => {
      let cls = `mem-cell mem-cell--${f.state}`;
      if (f.state === "allocated" && f.dirty) cls = "mem-cell mem-cell--dirty";

      // Check for change → pulse
      const prevState = prevFrameStates[f.frame_id];
      const curKey = `${f.state}_${f.owning_pid}_${f.virtual_page}`;
      if (prevState && prevState !== curKey) cls += " mem-cell--pulse";
      prevFrameStates[f.frame_id] = curKey;

      const label = f.state === "free" ? "" : f.frame_id;
      const tip = f.state === "free"
        ? `Frame ${f.frame_id}: Free`
        : `Frame ${f.frame_id}: ${f.label}\\nState: ${f.state}${f.dirty ? " (dirty)" : ""}${f.accessed ? " (accessed)" : ""}\\nPID: ${f.owning_pid ?? "-"}, VPN: ${f.virtual_page ?? "-"}\\nLast access: T=${f.last_access_time}`;

      html += `<div class="${cls}" title="${tip}">${label}<span class="tooltip">${tip.replace(/\\n/g, "<br>")}</span></div>`;
    });
    grid.innerHTML = html;
  }

  // ── Swap grid ─────────────────────────────────────────────────
  function renderSwapGrid() {
    const grid = $("swap-grid");
    const slots = state.swap.slots;
    const badge = $("swap-badge");
    badge.textContent = `${state.swap.used} used / ${state.swap.total} total`;

    // Only show first 64 slots if there are many, with a note
    const show = slots.slice(0, 64);
    let html = "";
    show.forEach((s) => {
      const cls = s.state === "free" ? "mem-cell mem-cell--swap-free" : "mem-cell mem-cell--swap-used";
      const label = s.state === "free" ? "" : s.slot_id;
      const tip = s.state === "free"
        ? `Slot ${s.slot_id}: Free`
        : `Slot ${s.slot_id}: ${s.label}\\nPID: ${s.owning_pid}, VPN: ${s.virtual_page}`;
      html += `<div class="${cls}" title="${tip}">${label}<span class="tooltip">${tip.replace(/\\n/g, "<br>")}</span></div>`;
    });
    if (slots.length > 64) {
      html += `<div style="grid-column:1/-1;font-size:0.68rem;color:var(--text-muted);margin-top:4px;">Showing first 64 of ${slots.length} slots</div>`;
    }
    grid.innerHTML = html;
  }

  // ── Processes ─────────────────────────────────────────────────
  function renderProcesses() {
    const list = $("process-list");
    const procs = state.processes.processes;
    const activePid = state.processes.active_pid;
    const keys = Object.keys(procs);
    $("proc-badge").textContent = `${keys.length} process${keys.length !== 1 ? "es" : ""}`;

    let html = "";
    keys.forEach((pid) => {
      const p = procs[pid];
      const isActive = parseInt(pid) === activePid;
      const isSuspended = p.state === "suspended";
      let cardCls = "proc-card";
      if (isActive) cardCls += " proc-card--active";
      if (isSuspended) cardCls += " proc-card--suspended";

      html += `
        <div class="${cardCls}">
          <div class="proc-header">
            <div>
              <span class="proc-name">${p.name}</span>
              <span class="proc-pid">PID ${p.pid}</span>
            </div>
            <span class="proc-state proc-state--${p.state}">${p.state}</span>
          </div>
          <div class="proc-stats">
            <span class="proc-stat-label">Accesses</span>
            <span class="proc-stat-value">${p.memory_accesses}</span>
            <span class="proc-stat-label">Page Faults</span>
            <span class="proc-stat-value">${p.page_faults}</span>
            <span class="proc-stat-label">Fault Rate</span>
            <span class="proc-stat-value">${(p.fault_rate * 100).toFixed(1)}%</span>
            <span class="proc-stat-label">Resident</span>
            <span class="proc-stat-value">${p.resident_pages} pages</span>
            <span class="proc-stat-label">Working Set</span>
            <span class="proc-stat-value">${p.working_set_size} pages</span>
          </div>
          <div class="proc-actions">
            ${p.state !== "suspended" ? `<button class="btn btn--danger" onclick="window._suspendProc(${p.pid})">Suspend</button>` : ""}
            ${p.state === "suspended" ? `<button class="btn btn--success" onclick="window._resumeProc(${p.pid})">Resume</button>` : ""}
            <button class="btn" onclick="window._viewPT(${p.pid})">View PT</button>
          </div>
        </div>`;
    });

    if (keys.length === 0) {
      html = `<div style="color:var(--text-muted);font-size:0.78rem;text-align:center;padding:20px;">No processes. Create one or load a scenario.</div>`;
    }
    list.innerHTML = html;

    // PT selector buttons
    renderPTSelector(keys, procs);
  }

  function renderPTSelector(keys, procs) {
    const sel = $("pt-selector");
    let html = "";
    keys.forEach((pid) => {
      const p = procs[pid];
      const active = selectedPtPid === parseInt(pid) ? " btn--active" : "";
      html += `<button class="btn${active}" onclick="window._viewPT(${pid})">PID ${pid}: ${p.name}</button>`;
    });
    sel.innerHTML = html;

    if (selectedPtPid === null && keys.length > 0) {
      selectedPtPid = parseInt(keys[0]);
    }
  }

  // ── Page table tree ───────────────────────────────────────────
  function renderPageTable() {
    const container = $("pt-tree");
    if (selectedPtPid === null || !state.processes.processes[selectedPtPid]) {
      container.innerHTML = `<div style="color:var(--text-muted);font-size:0.75rem;">Select a process to view its page table.</div>`;
      return;
    }

    const pt = state.processes.processes[selectedPtPid].page_table;
    container.innerHTML = renderPTLevel(pt.root, []);
  }

  function renderPTLevel(level, pathIndices) {
    if (!level || !level.entries) return "";
    const entries = level.entries;
    const levelName = level.level_name || `L${level.level}`;
    const keys = Object.keys(entries);

    if (keys.length === 0) {
      return `<div style="color:var(--text-muted);font-size:0.7rem;margin-left:${pathIndices.length * 16}px;">${levelName}: (empty)</div>`;
    }

    let html = "";
    keys.forEach((idx) => {
      const e = entries[idx];
      const isLeaf = level.level === (state.config.levels - 1);
      const path = [...pathIndices, parseInt(idx)];

      // Bits display
      let bits = "";
      bits += `<span class="pt-bit ${e.present ? "pt-bit--on" : "pt-bit--off"}">P</span>`;
      bits += `<span class="pt-bit ${e.accessed ? "pt-bit--on" : "pt-bit--off"}">A</span>`;
      if (isLeaf) {
        bits += `<span class="pt-bit ${e.dirty ? "pt-bit--on" : "pt-bit--off"}">D</span>`;
        bits += `<span class="pt-bit ${e.read_write ? "pt-bit--on" : "pt-bit--off"}">W</span>`;
        bits += `<span class="pt-bit ${e.no_execute ? "pt-bit--on" : "pt-bit--off"}">NX</span>`;
      }

      let info = "";
      if (isLeaf) {
        if (e.frame_number !== null && e.frame_number !== undefined) {
          info = `→ <span class="pt-frame">Frame ${e.frame_number}</span>`;
        }
        if (e.swap_slot !== null && e.swap_slot !== undefined) {
          info = `→ <span class="pt-swap">Swap ${e.swap_slot}</span>`;
        }
      }

      const hasChildren = e.children && Object.keys(e.children.entries || {}).length > 0;
      const toggleId = `pt-${path.join("-")}`;

      html += `<div class="pt-node">`;
      html += `<div class="pt-entry" ${hasChildren ? `onclick="window._togglePTNode('${toggleId}')"` : ""}>`;
      html += `<span class="pt-toggle">${hasChildren ? "▸" : "·"}</span>`;
      html += `<span class="pt-label">${levelName}[${idx}]</span> ${bits} ${info}`;
      html += `</div>`;

      if (hasChildren) {
        html += `<div id="${toggleId}" style="display:block;">`;
        html += renderPTLevel(e.children, path);
        html += `</div>`;
      }
      html += `</div>`;
    });
    return html;
  }

  window._togglePTNode = function (id) {
    const el = document.getElementById(id);
    if (el) {
      const wasHidden = el.style.display === "none";
      el.style.display = wasHidden ? "block" : "none";
      // Toggle arrow
      const toggle = el.previousElementSibling?.querySelector(".pt-toggle");
      if (toggle) toggle.textContent = wasHidden ? "▸" : "▾";
    }
  };

  window._viewPT = function (pid) {
    selectedPtPid = parseInt(pid);
    renderProcesses();
    renderPageTable();
  };

  // ── WSClock ───────────────────────────────────────────────────
  function renderWSClock() {
    const info = $("wsclock-info");
    const ring = $("clock-ring");
    const r = state.replacer;

    info.innerHTML = `
      <span class="wsclock-label">τ (Window)</span><span class="wsclock-value">${r.tau}</span>
      <span class="wsclock-label">Ring Size</span><span class="wsclock-value">${r.ring_size}</span>
      <span class="wsclock-label">Hand Position</span><span class="wsclock-value">${r.hand_position}</span>
      <span class="wsclock-label">Evictions</span><span class="wsclock-value">${r.evictions}</span>
      <span class="wsclock-label">Clean Evict</span><span class="wsclock-value">${r.clean_evictions}</span>
      <span class="wsclock-label">Dirty Evict</span><span class="wsclock-value">${r.dirty_evictions}</span>`;

    // Draw ring (show up to 40 frames)
    const frames = (r.ring || []).slice(0, 40);
    let rhtml = "";
    frames.forEach((fid, i) => {
      const isCurrent = i === r.hand_position;
      const f = state.frame_table.frames[fid];
      const cls = isCurrent ? "clock-frame clock-frame--current" : "clock-frame";
      const style = f && f.dirty
        ? "background:rgba(251,191,36,0.15);border-color:rgba(251,191,36,0.4);"
        : "background:rgba(99,102,241,0.08);";
      rhtml += `<div class="${cls}" style="${style}" title="Frame ${fid}${f ? ": " + f.label : ""}">${fid}</div>`;
    });
    ring.innerHTML = rhtml;
  }

  // ── Event log ─────────────────────────────────────────────────
  function renderLog() {
    const container = $("log-container");
    const events = state.mmu.recent_events || [];
    const totalEvents = state.mmu.total_events || events.length;
    $("log-badge").textContent = `${totalEvents} events`;

    let html = "";
    events.forEach((e) => {
      html += `<div class="log-entry">
        <span class="log-time">T${e.time}</span>
        <span class="log-type log-type--${e.type}">${e.type}</span>
        <span class="log-pid">${e.pid !== null ? "P" + e.pid : ""}</span>
        <span class="log-detail">${e.vpn !== null ? "VPN:" + e.vpn : ""} ${e.frame !== null ? "→F" + e.frame : ""} ${e.detail || ""}</span>
      </div>`;
    });
    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;
  }

  // ── Charts (simple canvas) ────────────────────────────────────
  function renderCharts() {
    const th = state.thrashing.history || [];
    if (th.length < 2) {
      // Clear canvases when there's not enough data (e.g. after reset)
      clearCanvas("chart-fault-rate");
      clearCanvas("chart-tlb-rate");
      return;
    }

    drawLineChart("chart-fault-rate", th.map((h) => h.fault_rate), {
      color: "rgba(251,113,133,0.9)",
      fillColor: "rgba(251,113,133,0.1)",
      threshold: state.thrashing.threshold,
      thresholdColor: "rgba(239,68,68,0.4)",
    });

    // TLB hit rate from stats timeline
    const timeline = state.stats ? [] : [];
    // Build TLB chart from thrashing history — we don't have tlb in thrashing history,
    // so compute from stats
    const stats = state.stats;
    const total = stats.tlb_hits + stats.tlb_misses;
    const tlbRate = total > 0 ? stats.tlb_hit_rate : 0;
    // We'll build a simple accumulator from the thrashing data length
    // For a proper solution we'd have a separate timeline. For now, show current value.
    const fakeTimeline = th.map((_, i) => {
      // approximate: tlb rate improves over time
      const progress = (i + 1) / th.length;
      return tlbRate * progress;
    });
    if (fakeTimeline.length > 0) fakeTimeline[fakeTimeline.length - 1] = tlbRate;

    drawLineChart("chart-tlb-rate", fakeTimeline, {
      color: "rgba(52,211,153,0.9)",
      fillColor: "rgba(52,211,153,0.1)",
    });
  }

  function clearCanvas(canvasId) {
    const canvas = $(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawLineChart(canvasId, data, opts = {}) {
    const canvas = $(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;

    ctx.clearRect(0, 0, W, H);

    if (data.length < 2) return;

    const maxVal = Math.max(1, ...data) * 1.1;
    const stepX = W / (data.length - 1);

    // Fill area
    ctx.beginPath();
    ctx.moveTo(0, H);
    data.forEach((v, i) => {
      ctx.lineTo(i * stepX, H - (v / maxVal) * H);
    });
    ctx.lineTo(W, H);
    ctx.closePath();
    ctx.fillStyle = opts.fillColor || "rgba(99,102,241,0.1)";
    ctx.fill();

    // Line
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = i * stepX;
      const y = H - (v / maxVal) * H;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = opts.color || "rgba(99,102,241,0.8)";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Threshold line
    if (opts.threshold !== undefined) {
      const ty = H - (opts.threshold / maxVal) * H;
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.moveTo(0, ty);
      ctx.lineTo(W, ty);
      ctx.strokeStyle = opts.thresholdColor || "rgba(239,68,68,0.5)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = opts.thresholdColor || "rgba(239,68,68,0.5)";
      ctx.font = "10px Inter";
      ctx.fillText(`threshold: ${opts.threshold}`, 4, ty - 4);
    }
  }

  // ── Thrashing banner ──────────────────────────────────────────
  function renderThrashing() {
    const banner = $("thrashing-banner");
    const rec = $("recommendation");

    if (state.thrashing.is_thrashing) {
      banner.classList.add("thrashing-banner--visible");
      $("thrashing-text").textContent = `Thrashing detected! Fault rate: ${(state.thrashing.fault_rate * 100).toFixed(1)}%`;
      rec.style.display = "block";
      rec.textContent = state.recommendation || "";
    } else {
      banner.classList.remove("thrashing-banner--visible");
      rec.style.display = state.recommendation && state.recommendation.includes("healthy") ? "none" : "block";
      rec.textContent = state.recommendation || "";
    }
  }

  function renderScenarioInfo() {
    const label = $("scenario-label");
    const remaining = $("scenario-remaining");
    if (state.scenario_name) {
      label.style.display = "inline";
      label.textContent = state.scenario_name;
      remaining.textContent = `${state.scenario_remaining} steps left`;
    } else {
      label.style.display = "none";
      remaining.textContent = "";
    }
  }

  // ── Actions ───────────────────────────────────────────────────
  btnStep.addEventListener("click", async () => {
    const data = await api("/api/step", "POST");
    if (data.state) { state = data.state; render(); }
  });

  btnRun.addEventListener("click", () => {
    if (isRunning) return;
    isRunning = true;
    btnRun.style.display = "none";
    btnPause.style.display = "inline-flex";
    autoRun();
  });

  btnPause.addEventListener("click", () => {
    isRunning = false;
    clearTimeout(autoRunTimer);
    autoRunTimer = null;
    btnPause.style.display = "none";
    btnRun.style.display = "inline-flex";
  });

  async function autoRun() {
    if (!isRunning) {
      // Was paused
      btnPause.style.display = "none";
      btnRun.style.display = "inline-flex";
      return;
    }
    const data = await api("/api/step", "POST");
    if (data.state) {
      state = data.state;
      render();
    }
    if (state.scenario_remaining > 0 && isRunning) {
      autoRunTimer = setTimeout(autoRun, parseInt(speedSlider.value));
    } else {
      isRunning = false;
      clearTimeout(autoRunTimer);
      autoRunTimer = null;
      btnPause.style.display = "none";
      btnRun.style.display = "inline-flex";
    }
  }

  btnReset.addEventListener("click", async () => {
    isRunning = false;
    clearTimeout(autoRunTimer);
    autoRunTimer = null;
    btnPause.style.display = "none";
    btnRun.style.display = "inline-flex";
    prevFrameStates = {};
    const data = await api("/api/reset", "POST");
    if (data.state) { state = data.state; render(); }
  });

  btnLoadScenario.addEventListener("click", async () => {
    const filename = scenarioSelect.value;
    if (!filename) return;
    clearTimeout(autoRunTimer);
    autoRunTimer = null;
    btnPause.style.display = "none";
    btnRun.style.display = "inline-flex";
    prevFrameStates = {};
    selectedPtPid = null;
    const data = await api("/api/scenario/load", "POST", { filename });
    if (data.state) { state = data.state; render(); }
  });

  speedSlider.addEventListener("input", () => {
    speedLabel.textContent = speedSlider.value + "ms";
  });

  // ── Manual access modal ───────────────────────────────────────
  btnManualAccess.addEventListener("click", () => {
    $("access-modal").classList.add("modal-overlay--visible");
  });

  $("access-cancel").addEventListener("click", () => {
    $("access-modal").classList.remove("modal-overlay--visible");
  });

  $("access-submit").addEventListener("click", async () => {
    const pid = parseInt($("access-pid").value);
    const vpn = parseInt($("access-vpn").value);
    const type = $("access-type").value;
    $("access-modal").classList.remove("modal-overlay--visible");
    const data = await api("/api/access", "POST", { pid, vpn, type });
    if (data.state) { state = data.state; render(); }
  });

  // ── Create process modal ──────────────────────────────────────
  btnCreateProcess.addEventListener("click", () => {
    $("create-proc-modal").classList.add("modal-overlay--visible");
  });

  $("create-proc-cancel").addEventListener("click", () => {
    $("create-proc-modal").classList.remove("modal-overlay--visible");
  });

  $("create-proc-submit").addEventListener("click", async () => {
    const name = $("proc-name").value || "Process";
    $("create-proc-modal").classList.remove("modal-overlay--visible");
    const data = await api("/api/process/create", "POST", { name });
    if (data.state) { state = data.state; render(); }
  });

  // ── Process actions (global) ──────────────────────────────────
  window._suspendProc = async function (pid) {
    const data = await api("/api/process/suspend", "POST", { pid });
    if (data.state) { state = data.state; render(); }
  };

  window._resumeProc = async function (pid) {
    const data = await api("/api/process/resume", "POST", { pid });
    if (data.state) { state = data.state; render(); }
  };

  // ── Keyboard shortcuts ────────────────────────────────────────
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "s" || e.key === "S") btnStep.click();
    if (e.key === "r" || e.key === "R") {
      if (e.shiftKey) btnReset.click();
      else btnRun.click();
    }
    if (e.key === "p" || e.key === "P") btnPause.click();
    if (e.key === "Escape") {
      $("access-modal").classList.remove("modal-overlay--visible");
      $("create-proc-modal").classList.remove("modal-overlay--visible");
    }
  });

  // ── Boot ──────────────────────────────────────────────────────
  init();
})();
