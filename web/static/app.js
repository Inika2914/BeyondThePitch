const state = {
  submissions: [],   // summaries from /api/submissions
  activeId: null,
  view: "welcome",   // "welcome" | "detail" | "leaderboard" | "jury-leaderboard"
  user: null,        // { id, username, role, display_name }
  juries: [],        // admin: jury accounts, loaded into the judging setup modal
  judgingMode: "all",
};

const el = (sel) => document.querySelector(sel);
const caseList = el("#caseList");
const mainPanel = el("#mainPanel");
const modalBackdrop = el("#modalBackdrop");
const submissionForm = el("#submissionForm");
const weightsModalBackdrop = el("#weightsModalBackdrop");
const weightsForm = el("#weightsForm");
const loginScreen = el("#loginScreen");
const loginForm = el("#loginForm");
const appRoot = el("#appRoot");
const judgingModalBackdrop = el("#judgingModalBackdrop");

// ---------- API helper ----------
// Every call is same-origin, so the btp_session cookie rides along
// automatically. A 401 anywhere (session expired / logged out in
// another tab) drops back to the login screen instead of leaving the
// UI in a broken half-logged-in state.

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401) {
    showLogin();
    throw new Error("Session expired — please log in again.");
  }
  return res;
}

// ---------- Auth ----------

function showLogin() {
  state.user = null;
  document.body.classList.remove("role-admin", "role-jury");
  appRoot.style.display = "none";
  loginScreen.style.display = "flex";
  el("#loginError").textContent = "";
  loginForm.reset();
}

function showApp(user) {
  state.user = user;
  document.body.classList.remove("role-admin", "role-jury");
  document.body.classList.add(`role-${user.role}`);
  el("#userName").textContent = user.display_name || user.username;
  el("#userRole").textContent = user.role;
  loginScreen.style.display = "none";
  appRoot.style.display = "grid";
}

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(loginForm);
  const btn = el("#loginSubmitBtn");
  btn.disabled = true;
  btn.textContent = "Logging in…";
  el("#loginError").textContent = "";
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: fd.get("username"), password: fd.get("password") }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      el("#loginError").textContent = err.detail || "Login failed.";
      return;
    }
    const user = await res.json();
    showApp(user);
    await initApp();
  } finally {
    btn.disabled = false;
    btn.textContent = "Log in";
  }
});

el("#logoutBtn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  showLogin();
});

// ---------- Modal wiring ----------

el("#newSubmissionBtn").addEventListener("click", () => modalBackdrop.classList.add("open"));
el("#modalClose").addEventListener("click", () => modalBackdrop.classList.remove("open"));
el("#modalCancel").addEventListener("click", () => modalBackdrop.classList.remove("open"));
modalBackdrop.addEventListener("click", (e) => {
  if (e.target === modalBackdrop) modalBackdrop.classList.remove("open");
});

submissionForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(submissionForm);
  const submitBtn = submissionForm.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = "Ingesting…";

  try {
    const res = await fetch("/api/submissions", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json();
      alert("Could not ingest submission: " + (err.detail || res.statusText));
      return;
    }
    const created = await res.json();
    modalBackdrop.classList.remove("open");
    submissionForm.reset();
    await refreshSubmissions();
    selectSubmission(created.id);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Ingest submission";
  }
});

el("#leaderboardBtn").addEventListener("click", () => {
  state.view = "leaderboard";
  state.activeId = null;
  renderRail();
  renderLeaderboard();
});

el("#juryLeaderboardBtn").addEventListener("click", () => {
  state.view = "jury-leaderboard";
  state.activeId = null;
  renderRail();
  renderJuryLeaderboard();
});

// ---------- Data ----------

async function refreshSubmissions() {
  const res = await fetch("/api/submissions");
  state.submissions = await res.json();
  renderRail();
}

async function fetchDetail(id) {
  const res = await fetch(`/api/submissions/${id}`);
  return res.json();
}

async function runEvaluation(id) {
  // Streamed version: shows each rubric dimension being scored live,
  // instead of one static "Scoring…" state until the whole pass returns.
  state.progress = { active: true, lines: [] };
  renderProgress();

  let res;
  try {
    res = await fetch(`/api/submissions/${id}/evaluate/stream`, { method: "POST" });
  } catch (e) {
    state.progress = null;
    alert("Evaluation failed to start: " + e.message);
    return;
  }

  if (!res.ok || !res.body) {
    state.progress = null;
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    alert("Evaluation failed: " + msg);
    await refreshSubmissions();
    await selectSubmission(id);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      handleProgressEvent(id, JSON.parse(line.slice(6)));
    }
  }

  await refreshSubmissions();
}

function handleProgressEvent(id, evt) {
  if (!state.progress) return;

  if (evt.stage === "dimension" && evt.status === "start") {
    state.progress.lines.push({ key: evt.key, label: `Scoring ${evt.label}…`, state: "active" });
  } else if (evt.stage === "dimension" && evt.status === "done") {
    const line = state.progress.lines.find((l) => l.key === evt.key);
    if (line) {
      line.state = "done";
      line.label = `${evt.label} — ${evt.score.score}/10`;
    }
  } else if (evt.stage === "dimension" && evt.status === "error") {
    const line = state.progress.lines.find((l) => l.key === evt.key);
    if (line) { line.state = "error"; line.label = `${evt.label} — failed`; }
  } else if (evt.stage === "summary" && evt.status === "start") {
    state.progress.lines.push({ key: "summary", label: "Synthesizing strengths, improvements & flags…", state: "active" });
  } else if (evt.stage === "summary" && evt.status === "done") {
    const line = state.progress.lines.find((l) => l.key === "summary");
    if (line) { line.state = "done"; line.label = "Summary synthesized"; }
  } else if (evt.stage === "final") {
    state.progress = null;
    selectSubmission(id);
    return;
  } else if (evt.stage === "fatal") {
    const line = { key: "fatal", label: `Evaluation failed: ${evt.error}`, state: "error" };
    state.progress.lines.push(line);
    state.progress.active = false;
  }

  renderProgress();
}

function renderProgress() {
  const box = el("#evalProgress");
  if (!box) return;
  if (!state.progress) { box.innerHTML = ""; return; }
  box.innerHTML = `
    <div class="progress-feed">
      ${state.progress.lines.map((l) => `
        <div class="progress-line ${l.state}">
          <span class="progress-dot"></span>
          <span class="progress-text">${escapeHtml(l.label)}</span>
        </div>
      `).join("")}
    </div>`;
}

// ---------- Rail rendering ----------

function renderRail() {
  if (state.submissions.length === 0) {
    caseList.innerHTML = `<div class="empty-hint">No submissions yet.<br>Add one to begin.</div>`;
    return;
  }
  caseList.innerHTML = state.submissions.map((s) => `
    <div class="case-card ${s.id === state.activeId ? "active" : ""}" data-id="${s.id}">
      <div class="team">${escapeHtml(s.team_name)}</div>
      <div class="title">${escapeHtml(s.project_title)}</div>
      <div class="status-row">
        <span class="status-pill ${s.status}">${statusLabel(s.status)}</span>
        ${s.weighted_total !== null ? `<span class="case-score">${s.weighted_total}</span>` : ""}
      </div>
    </div>
  `).join("");

  caseList.querySelectorAll(".case-card").forEach((card) => {
    card.addEventListener("click", () => selectSubmission(card.dataset.id));
  });
}

function statusLabel(status) {
  return { ingested: "Ready to score", scoring: "Scoring…", scored: "Scored", error: "Error" }[status] || status;
}

// ---------- Detail rendering ----------

async function selectSubmission(id) {
  state.activeId = id;
  state.view = "detail";
  renderRail();
  const detail = await fetchDetail(id);
  renderDetail(detail);
}

function renderDetail(d) {
  const sub = d.submission;

  let scoreHeader = "";
  let rubricSection = "";
  let summarySection = "";
  let flagsSection = "";

  if (d.status === "scored" && d.result) {
    const r = d.result;
    scoreHeader = `
      <div class="overall-score">
        <span class="num">${r.weighted_total}</span>
        <span class="label">/ 100 weighted</span>
      </div>`;

    rubricSection = `
      <div class="rubric-section">
        <div class="section-title">Rubric scores</div>
        ${r.dimension_scores.map((ds) => `
          <div class="dim-row">
            <div class="dim-top">
              <span class="dim-name">${escapeHtml(ds.label)}</span>
              <span class="dim-score">${ds.score}/10</span>
            </div>
            <div class="bar-track"><div class="bar-fill" style="width:${ds.score * 10}%"></div></div>
            <div class="dim-justification">${escapeHtml(ds.justification)}</div>
            <div class="evidence-list">
              ${ds.strengths.map((s) => `<div class="evidence-item strength">${escapeHtml(s)}</div>`).join("")}
              ${ds.concerns.map((c) => `<div class="evidence-item concern">${escapeHtml(c)}</div>`).join("")}
            </div>
          </div>
        `).join("")}
      </div>`;

    summarySection = `
      <div class="summary-grid">
        <div class="summary-col strengths">
          <h3>Top strengths</h3>
          <ul>${r.top_strengths.map((s) => `<li>${escapeHtml(s)}</li>`).join("") || "<li>None noted.</li>"}</ul>
        </div>
        <div class="summary-col improvements">
          <h3>Areas to improve</h3>
          <ul>${r.top_improvements.map((s) => `<li>${escapeHtml(s)}</li>`).join("") || "<li>None noted.</li>"}</ul>
        </div>
      </div>`;

    if (r.judge_flags && r.judge_flags.length > 0) {
      flagsSection = `
        <div class="flags-box">
          <h3>Judge attention flags</h3>
          <ul>${r.judge_flags.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
        </div>`;
    }
  } else if (d.status === "error") {
    flagsSection = `<div class="error-box">Scoring failed: ${escapeHtml(d.error || "unknown error")}</div>`;
  }

  const actionArea = d.status === "ingested" || d.status === "error"
    ? `<button class="evaluate-btn" id="evaluateBtn">Run evaluation</button>
       <div class="pending-note">Analyzes code, README, demo evidence, and pitch against the rubric.</div>
       <div id="evalProgress"></div>`
    : d.status === "scoring"
      ? `<button class="evaluate-btn" disabled>Scoring…</button>
         <div id="evalProgress"></div>`
      : "";

  mainPanel.innerHTML = `
    <div class="case-header">
      <div class="eyebrow">Submission</div>
      <h2>${escapeHtml(d.project_title)}</h2>
      <div class="meta">${escapeHtml(d.team_name)}</div>
      ${scoreHeader}
    </div>

    <div class="submission-meta-box">
      <div><span class="meta-label">Tech stack</span>${escapeHtml(sub.tech_stack) || "—"}</div>
      <div><span class="meta-label">Code files</span>${sub.code_file_count} file(s) ingested</div>
      <div><span class="meta-label">README</span>${sub.readme_present ? "Present" : "Not found"}</div>
      <div><span class="meta-label">Demo evidence</span>${demoLabel(sub.demo_kind, sub.demo_value)}</div>
    </div>

    <div id="assignBox"></div>
    <div id="juryScorecardBox"></div>

    ${actionArea}
    ${rubricSection}
    ${summarySection}
    ${flagsSection}
  `;

  const evalBtn = el("#evaluateBtn");
  if (evalBtn) evalBtn.addEventListener("click", () => {
    evalBtn.disabled = true;
    evalBtn.textContent = "Scoring…";
    runEvaluation(d.id);
  });

  // If a stream is already in flight for this submission (e.g. user
  // switched away and back), keep showing it rather than resetting.
  if (state.progress && state.activeId === d.id) renderProgress();

  if (state.user.role === "admin") {
    renderAssignBox(d.id);
  } else if (state.user.role === "jury") {
    renderJuryScorecard(d.id);
  }
}

// ---------- Admin: assignment & reveal (per submission) ----------

async function renderAssignBox(sid) {
  const box = el("#assignBox");
  if (!box) return;

  const mode = await refreshJudgingMode();
  if (state.juries.length === 0) await refreshJuryList().catch(() => {});
  const res = await api(`/api/admin/assign/${sid}`);
  const info = await res.json();

  let chipsHtml;
  if (mode === "split") {
    chipsHtml = state.juries.length === 0
      ? `<div class="assign-hint">No jury accounts yet — add one under "Judging setup".</div>`
      : `
        <div class="assign-hint">Assign this submission to one or more juries:</div>
        <div class="assign-chips">
          ${state.juries.map((j) => `
            <button type="button" class="assign-chip ${info.assigned_juries.includes(j.id) ? "on" : ""}" data-jury="${j.id}">
              ${escapeHtml(j.display_name)}
            </button>
          `).join("")}
        </div>`;
  } else {
    chipsHtml = `<div class="assign-hint">All-evaluate mode is on — every jury account scores every submission, so per-submission assignment isn't used.</div>`;
  }

  box.innerHTML = `
    <div class="assign-box">
      <div class="section-title">Jury assignment &amp; reveal</div>
      ${chipsHtml}
      <div class="reveal-row">
        <span class="reveal-status ${info.revealed ? "open" : "closed"}">
          ${info.revealed ? "● Revealed to juries" : "● Hidden from juries"}
        </span>
        <span class="jsc-note">${info.submitted_juries.length}/${info.expected_juries.length} submitted</span>
        <button type="button" class="btn-secondary" id="revealToggleBtn">
          ${info.force_revealed ? "Undo force-reveal" : "Force reveal"}
        </button>
      </div>
    </div>`;

  if (mode === "split") {
    box.querySelectorAll(".assign-chip").forEach((chip) => {
      chip.addEventListener("click", async () => {
        const juryId = chip.dataset.jury;
        const current = new Set(info.assigned_juries);
        current.has(juryId) ? current.delete(juryId) : current.add(juryId);
        await api(`/api/admin/assign/${sid}`, {
          method: "PUT",
          body: JSON.stringify({ jury_ids: [...current] }),
        });
        renderAssignBox(sid);
      });
    });
  }

  el("#revealToggleBtn").addEventListener("click", async () => {
    const endpoint = info.force_revealed ? "unreveal" : "reveal";
    await api(`/api/admin/${endpoint}/${sid}`, { method: "POST" });
    renderAssignBox(sid);
  });
}

// ---------- Jury: own scorecard for a submission ----------

async function renderJuryScorecard(sid) {
  const box = el("#juryScorecardBox");
  if (!box) return;

  const [weightsRes, scoreRes] = await Promise.all([
    api("/api/config/weights"),
    api(`/api/submissions/${sid}/jury-score`),
  ]);
  const dims = (await weightsRes.json()).dimensions;
  const data = await scoreRes.json();
  const own = data.own_score;
  const ownByKey = {};
  if (own) own.dimension_scores.forEach((ds) => { ownByKey[ds.key] = ds.score; });

  box.innerHTML = `
    <div class="jury-scorecard">
      <div class="section-title">Your score</div>
      <h3>Rate this submission</h3>
      ${dims.map((d) => {
        const val = ownByKey[d.key] ?? 5;
        return `
          <div class="jsc-dim" data-key="${d.key}">
            <div class="jsc-dim-top"><span class="n">${escapeHtml(d.label)}</span><span class="v">${val}/10</span></div>
            <input type="range" min="1" max="10" value="${val}" />
          </div>`;
      }).join("")}
      <label class="jsc-note" style="display:block; margin-top:4px;">
        Comment
        <textarea class="jsc-comment" rows="2" placeholder="Notes for your own record (visible to other juries once revealed)">${escapeHtml(own?.comment || "")}</textarea>
      </label>
      <div class="jsc-submit-row">
        <button type="button" class="btn-primary" id="jscSubmitBtn">${own ? "Update score" : "Submit score"}</button>
        ${own ? `<span class="jsc-note">Last submitted ${new Date(own.submitted_at).toLocaleString()}</span>` : ""}
      </div>
      <div id="jscOtherScores"></div>
    </div>`;

  box.querySelectorAll('input[type="range"]').forEach((slider) => {
    slider.addEventListener("input", () => {
      slider.closest(".jsc-dim").querySelector(".v").textContent = `${slider.value}/10`;
    });
  });

  el("#jscSubmitBtn").addEventListener("click", async () => {
    const btn = el("#jscSubmitBtn");
    btn.disabled = true;
    const dimension_scores = [...box.querySelectorAll(".jsc-dim")].map((row) => ({
      key: row.dataset.key,
      score: Number(row.querySelector('input[type="range"]').value),
    }));
    const comment = box.querySelector(".jsc-comment").value;
    let res;
    try {
      res = await api(`/api/submissions/${sid}/jury-score`, {
        method: "POST",
        body: JSON.stringify({ dimension_scores, comment, judge_flags: [] }),
      });
    } finally {
      btn.disabled = false;
    }
    if (!res.ok) {
      const err = await res.json();
      alert("Could not submit score: " + (err.detail || res.statusText));
      return;
    }
    renderJuryScorecard(sid);
  });

  renderOtherJuryScores(data);
}

function renderOtherJuryScores(data) {
  const box = el("#jscOtherScores");
  if (!box) return;
  const others = data.scores.filter((s) => s.jury_id !== state.user.id);

  if (!data.revealed) {
    box.innerHTML = `
      <div class="other-scores">
        <div class="blind-note">Other juries' scores are hidden until everyone assigned has submitted (or admin reveals them).</div>
      </div>`;
    return;
  }
  box.innerHTML = `
    <div class="other-scores">
      <div class="section-title">Other jury scores</div>
      ${others.length === 0
        ? `<div class="blind-note">No other jury scores yet.</div>`
        : others.map((s) => `
            <div class="other-score-row">
              <span class="n">${escapeHtml(s.jury_name)}</span>
              <span class="v">${s.weighted_total}/100</span>
            </div>
          `).join("")}
    </div>`;
}

function demoLabel(kind, value) {
  if (kind === "none" || !kind) return "None provided";
  const kindLabel = { live_url: "Live URL", video_url: "Video", screenshots: "Screenshots" }[kind] || kind;
  return value ? `${kindLabel} — ${escapeHtml(value)}` : kindLabel;
}

// ---------- Leaderboard ----------

async function renderLeaderboard() {
  const res = await fetch("/api/leaderboard");
  const rows = await res.json();

  if (rows.length === 0) {
    mainPanel.innerHTML = `
      <div class="case-header">
        <div class="eyebrow">Pool ranking</div>
        <h2>Leaderboard</h2>
      </div>
      <p style="color:var(--ink-soft)">Score at least one submission to see rankings here.</p>`;
    return;
  }

  mainPanel.innerHTML = `
    <div class="case-header">
      <div class="eyebrow">Pool ranking, calibrated against the batch</div>
      <h2>Leaderboard</h2>
    </div>
    <table class="leaderboard-table">
      <thead>
        <tr>
          <th>Rank</th><th>Team</th><th>Project</th>
          <th>Raw</th><th>Calibrated</th><th>Percentile</th><th>Flags</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((r) => `
          <tr data-id="${r.id}">
            <td class="rank-cell">#${r.rank}</td>
            <td>${escapeHtml(r.team_name)}</td>
            <td>${escapeHtml(r.project_title)}</td>
            <td>${r.weighted_total}</td>
            <td>${r.calibrated_total}</td>
            <td>${r.percentile}</td>
            <td>${r.judge_flags.length}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  mainPanel.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectSubmission(row.dataset.id));
  });
}

async function renderJuryLeaderboard() {
  const res = await api("/api/leaderboard/jury");
  const rows = await res.json();

  if (rows.length === 0) {
    mainPanel.innerHTML = `
      <div class="case-header">
        <div class="eyebrow">Combined jury ranking</div>
        <h2>Jury leaderboard</h2>
      </div>
      <p style="color:var(--ink-soft)">No jury scores submitted yet.</p>`;
    return;
  }

  mainPanel.innerHTML = `
    <div class="case-header">
      <div class="eyebrow">Combined jury ranking, calibrated against the batch</div>
      <h2>Jury leaderboard</h2>
    </div>
    <table class="leaderboard-table">
      <thead>
        <tr>
          <th>Rank</th><th>Team</th><th>Project</th>
          <th>Avg. jury score</th><th>Calibrated</th><th>Percentile</th><th>Juries in</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((r) => `
          <tr data-id="${r.id}">
            <td class="rank-cell">#${r.rank}</td>
            <td>${escapeHtml(r.team_name)}</td>
            <td>${escapeHtml(r.project_title)}</td>
            <td>${r.weighted_total}</td>
            <td>${r.calibrated_total}</td>
            <td>${r.percentile}</td>
            <td>${r.juries_submitted}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  mainPanel.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectSubmission(row.dataset.id));
  });
}

// ---------- Utils ----------

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ---------- Rubric weights ----------

el("#weightsBtn").addEventListener("click", async () => {
  await loadWeightsIntoForm();
  weightsModalBackdrop.classList.add("open");
});
el("#weightsModalClose").addEventListener("click", () => weightsModalBackdrop.classList.remove("open"));
weightsModalBackdrop.addEventListener("click", (e) => {
  if (e.target === weightsModalBackdrop) weightsModalBackdrop.classList.remove("open");
});

async function loadWeightsIntoForm() {
  const res = await fetch("/api/config/weights");
  const data = await res.json();
  renderWeightsForm(data.dimensions, data.weights);
}

function renderWeightsForm(dimensions, weights) {
  weightsForm.innerHTML = dimensions.map((d) => {
    const w = weights[d.key];
    // Slider is 0-50% per dimension; weights don't need to sum to 1,
    // the engine normalizes by total_weight() at scoring time.
    const pct = Math.round(w * 100);
    return `
      <div class="weight-row" data-key="${d.key}">
        <div class="weight-row-top">
          <span class="wr-label">${escapeHtml(d.label)}</span>
          <span class="wr-value">${pct}%</span>
        </div>
        <input type="range" min="1" max="50" value="${pct}" />
      </div>`;
  }).join("");

  weightsForm.querySelectorAll(".weight-row").forEach((row) => {
    const slider = row.querySelector("input[type=range]");
    const valueLabel = row.querySelector(".wr-value");
    slider.addEventListener("input", () => {
      valueLabel.textContent = `${slider.value}%`;
    });
  });
}

el("#weightsSaveBtn").addEventListener("click", async () => {
  const newWeights = {};
  weightsForm.querySelectorAll(".weight-row").forEach((row) => {
    const key = row.dataset.key;
    const slider = row.querySelector("input[type=range]");
    newWeights[key] = Number(slider.value) / 100;
  });

  const res = await fetch("/api/config/weights", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weights: newWeights }),
  });

  if (!res.ok) {
    const err = await res.json();
    alert("Could not save weights: " + (err.detail || res.statusText));
    return;
  }

  weightsModalBackdrop.classList.remove("open");
});

el("#weightsResetBtn").addEventListener("click", async () => {
  const res = await fetch("/api/config/weights/reset", { method: "POST" });
  const data = await res.json();
  const dimRes = await fetch("/api/config/weights");
  const dimData = await dimRes.json();
  renderWeightsForm(dimData.dimensions, data.weights);
});

// ---------- Judging setup (admin) ----------

el("#judgingSetupBtn").addEventListener("click", async () => {
  await openJudgingSetup();
  judgingModalBackdrop.classList.add("open");
});
el("#judgingModalClose").addEventListener("click", () => judgingModalBackdrop.classList.remove("open"));
judgingModalBackdrop.addEventListener("click", (e) => {
  if (e.target === judgingModalBackdrop) judgingModalBackdrop.classList.remove("open");
});

async function refreshJudgingMode() {
  const res = await api("/api/admin/config");
  const data = await res.json();
  state.judgingMode = data.judging_mode;
  return state.judgingMode;
}

async function openJudgingSetup() {
  await refreshJudgingMode();
  renderModeToggle();
  await refreshJuryList();
  el("#juryError").textContent = "";
}

function renderModeToggle() {
  el("#modeToggle").querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === state.judgingMode);
  });
}

el("#modeToggle").addEventListener("click", async (e) => {
  const btn = e.target.closest(".mode-btn");
  if (!btn) return;
  const mode = btn.dataset.mode;
  if (mode === state.judgingMode) return;
  await api("/api/admin/config", { method: "PUT", body: JSON.stringify({ judging_mode: mode }) });
  state.judgingMode = mode;
  renderModeToggle();
  // Assignment relevance changed — refresh the open submission's detail
  // (assignment chips only show in split mode; see renderAssignBox).
  if (state.activeId) await selectSubmission(state.activeId);
});

el("#autoSplitBtn").addEventListener("click", async () => {
  el("#juryError").textContent = "";
  try {
    const res = await api("/api/admin/assign/auto-split", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      el("#juryError").textContent = err.detail || "Auto-split failed.";
      return;
    }
    if (state.activeId) await selectSubmission(state.activeId);
  } catch (e) {
    el("#juryError").textContent = e.message;
  }
});

async function refreshJuryList() {
  const res = await api("/api/admin/juries");
  state.juries = await res.json();
  renderJuryList();
}

function renderJuryList() {
  const box = el("#juryList");
  if (state.juries.length === 0) {
    box.innerHTML = `<div class="jsc-note">No jury accounts yet — add one below.</div>`;
    return;
  }
  box.innerHTML = state.juries.map((j) => `
    <div class="jury-row" data-id="${j.id}">
      <span><span class="jn">${escapeHtml(j.display_name)}</span><span class="ju">@${escapeHtml(j.username)}</span></span>
      <button type="button" class="jury-remove" title="Remove jury account">&times;</button>
    </div>
  `).join("");

  box.querySelectorAll(".jury-row").forEach((row) => {
    row.querySelector(".jury-remove").addEventListener("click", async () => {
      if (!confirm("Remove this jury account? Their submitted scores are kept, but they can no longer log in.")) return;
      await api(`/api/admin/juries/${row.dataset.id}`, { method: "DELETE" });
      await refreshJuryList();
    });
  });
}

el("#newJuryForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  el("#juryError").textContent = "";
  const res = await api("/api/admin/juries", {
    method: "POST",
    body: JSON.stringify({
      username: fd.get("username"),
      password: fd.get("password"),
      display_name: fd.get("display_name") || "",
    }),
  });
  if (!res.ok) {
    const err = await res.json();
    el("#juryError").textContent = err.detail || "Could not add jury account.";
    return;
  }
  form.reset();
  await refreshJuryList();
});

// ---------- Init ----------

async function initApp() {
  await refreshSubmissions();
  if (state.user.role === "admin") {
    await refreshJudgingMode();
  }
  state.view = "welcome";
  state.activeId = null;
}

async function bootstrap() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) { showLogin(); return; }
    const user = await res.json();
    showApp(user);
    await initApp();
  } catch {
    showLogin();
  }
}

bootstrap();
