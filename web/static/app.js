const state = {
  submissions: [],   // summaries from /api/submissions
  activeId: null,
  view: "welcome",   // "welcome" | "detail" | "leaderboard"
};

const el = (sel) => document.querySelector(sel);
const caseList = el("#caseList");
const mainPanel = el("#mainPanel");
const modalBackdrop = el("#modalBackdrop");
const submissionForm = el("#submissionForm");
const weightsModalBackdrop = el("#weightsModalBackdrop");
const weightsForm = el("#weightsForm");

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
  const res = await fetch(`/api/submissions/${id}/evaluate`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json();
    alert("Evaluation failed: " + (err.detail || res.statusText));
  }
  await refreshSubmissions();
  await selectSubmission(id);
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
       <div class="pending-note">Analyzes code, README, demo evidence, and pitch against the rubric.</div>`
    : d.status === "scoring"
      ? `<button class="evaluate-btn" disabled>Scoring…</button>`
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

    ${actionArea}
    ${rubricSection}
    ${summarySection}
    ${flagsSection}
  `;

  const evalBtn = el("#evaluateBtn");
  if (evalBtn) evalBtn.addEventListener("click", () => runEvaluation(d.id));
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

// ---------- Init ----------

refreshSubmissions();
