const state = {
  scenarios: [],
  materials: [],
  standards: [],
  validation: [],
  cat: "all",
  q: "",
  running: false,
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ------------------------------------------------------------------ tabs */

$$("#tabs .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("#tabs .tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".view").forEach((v) => v.classList.remove("active"));
    $("#view-" + btn.dataset.view).classList.add("active");
    window.scrollTo({ top: 0, behavior: "instant" });
  });
});

/* ------------------------------------------------------------------ boot */

async function boot() {
  const [health, scenarios, materials, standards, validation] = await Promise.all([
    fetch("/api/health").then((r) => r.json()),
    fetch("/api/scenarios").then((r) => r.json()),
    fetch("/api/materials").then((r) => r.json()),
    fetch("/api/standards").then((r) => r.json()),
    fetch("/api/validation").then((r) => r.json()),
  ]);
  state.scenarios = scenarios.scenarios;
  state.materials = materials.materials;
  state.standards = standards.standards;
  state.validation = validation.cases;

  $("#health").textContent = `离线模式 · ${materials.meta.record_count} 条材料 · ${standards.standards.length} 条来源`;

  renderScenarioChips();
  applyPayload(state.scenarios[0].payload);
  renderMaterials();
  renderCitations();
  renderValidation();
  run();
}

/* ------------------------------------------------------------------ 场景 */

function renderScenarioChips() {
  $("#scenarioChips").innerHTML = state.scenarios
    .map(
      (s, i) =>
        `<button class="chip${i === 0 ? " active" : ""}" data-idx="${i}"><em>${s.badge}</em>${esc(s.name)}</button>`
    )
    .join("");
  $$("#scenarioChips .chip").forEach((b) => {
    b.addEventListener("click", () => {
      $$("#scenarioChips .chip").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      applyPayload(state.scenarios[+b.dataset.idx].payload);
      run();
    });
  });
}

function applyPayload(p) {
  $("#f-model").value = p.model;
  $("#f-installation").value = p.installation;
  $("#f-ambient").value = p.ambient;
  $("#f-flame").value = p.flame || "none";
  $("#f-rating").value = p.fire_rating || "";
  $("#f-bundle").value = p.bundle_count || "";
  $$("#f-checks input").forEach((c) => {
    c.checked = ["lszh", "fire_resistant", "bundle", "buried", "wet", "corrosive", "rodent", "uv", "oil", "flexible", "emc"].includes(c.dataset.key)
      ? !!p[c.dataset.key]
      : c.checked;
  });
}

function collect() {
  const checks = {};
  $$("#f-checks input").forEach((c) => (checks[c.dataset.key] = c.checked));
  const b = $("#f-bundle").value.trim();
  return {
    model: $("#f-model").value,
    installation: $("#f-installation").value,
    ambient: Number($("#f-ambient").value || 40),
    flame: $("#f-flame").value,
    fire_rating: $("#f-rating").value || null,
    bundle_count: b === "" ? null : Number(b),
    notes: "",
    ...checks,
  };
}

/* ------------------------------------------------------------------ 运行 */

async function run() {
  if (state.running) return;
  state.running = true;
  const btn = $("#runBtn");
  btn.disabled = true;
  btn.querySelector("span").textContent = "计算中…";
  try {
    const res = await fetch("/api/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collect()),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderResult(data);
  } catch (e) {
    $("#resultPanel").innerHTML = `<div class="empty"><h3>运行失败</h3><p>${esc(e.message)}</p></div>`;
  } finally {
    state.running = false;
    btn.disabled = false;
    btn.querySelector("span").textContent = "运行选型";
  }
}

/* ------------------------------------------------------------------ 结果 */

function layerCard(L, i) {
  const idx = String(i + 1).padStart(2, "0");

  if (!L.recommended) {
    const opts = L.candidates.length
      ? `<div class="opt-list">${L.candidates
          .slice(0, 4)
          .map((c) => `<span>${esc(c.name_cn)} ${c.score}</span>`)
          .join("")}</div>`
      : "";
    return `
      <article class="layer">
        <div class="layer-head">
          <span class="idx">${idx}</span>
          <div><h3>${esc(L.title)}</h3><div class="role">${esc(L.role)}</div></div>
          <span class="tag skip">不适用</span>
        </div>
        <div class="layer-body">
          <div class="skip-body">${esc(L.skip_reason || "")}${opts}</div>
        </div>
      </article>`;
  }

  const r = L.recommended;
  const rows = L.candidates
    .map((c, k) => {
      const pill =
        c.decision === "推荐"
          ? '<span class="pill p-rec">推荐</span>'
          : c.decision === "备选"
          ? '<span class="pill p-alt">备选</span>'
          : '<span class="pill p-opt">可选</span>';
      return `<tr>
        <td>${String(k + 1).padStart(2, "0")}</td>
        <td><span class="nm">${esc(c.name_cn)}</span><span class="gr">${esc(c.grade)}</span></td>
        <td class="sc">${c.score}</td>
        <td class="st">${pill}</td>
      </tr>`;
    })
    .join("");

  const exRows = L.excluded
    .slice(0, 6)
    .map(
      (c) => `<tr class="ex">
        <td>—</td>
        <td><span class="nm">${esc(c.name_cn)}</span><span class="gr">${esc(c.reasons[0] || "不满足硬约束")}</span></td>
        <td class="sc">—</td>
        <td class="st"><span class="pill p-ex">排除</span></td>
      </tr>`
    )
    .join("");

  const reasons = r.reasons
    .slice(0, 3)
    .map((t) => `<li class="${t.startsWith("−") ? "neg" : ""}">${esc(t.replace(/^[+·−]\s*/, ""))}</li>`)
    .join("");

  const cites = L.citations
    .map((c) => `<span class="cite ${c.level === "standard" ? "std" : "doc"}" title="${esc(c.title)}">${esc(c.code)}</span>`)
    .join("");

  return `
    <article class="layer">
      <div class="layer-head">
        <span class="idx">${idx}</span>
        <div><h3>${esc(L.title)}</h3><div class="role">${esc(L.role)}</div></div>
        <span class="tag">推荐</span>
      </div>
      <div class="layer-body">
        <div class="rec">
          <span class="rec-name">${esc(r.name_cn)}</span>
          <span class="rec-grade">${esc(r.grade)}</span>
          <span class="rec-score">${r.score}<span>分</span></span>
        </div>
        <div class="bar ${r.score < 75 ? "teal" : ""}"><i style="width:${r.score}%"></i></div>
        ${L.composite ? `<div class="composite">复合结构：${esc(L.composite)}</div>` : ""}
        <ul class="reasons">${reasons}</ul>
        <details>
          <summary>候选 ${L.candidates.length} 项 · 排除 ${L.excluded.length} 项</summary>
          <table class="cand-table">${rows}${exRows}</table>
        </details>
        <div class="cites">${cites}</div>
      </div>
    </article>`;
}

function renderResult(d) {
  const s = d.summary;
  const confSeg = Array.from({ length: 20 }, (_, i) => `<i class="${i < Math.round(s.confidence * 20) ? "on" : ""}"></i>`).join("");
  const n = d.narrative;
  const nar = n
    ? `<div class="narrative">
        <span class="mode">${esc(n.mode)}</span>
        <h4>Interpretation</h4>
        <p>${esc(n.headline)}</p>
        ${n.sections.map((x) => `<div class="sec"><b>${esc(x.title)}</b><div class="secbody">${esc(x.body)}</div></div>`).join("")}
      </div>`
    : "";

  const layers = d.layers.map((L, i) => layerCard(L, i)).join("");

  const checks = d.checks
    .map((c) => {
      const mark = c.level === "error" ? "!" : c.level === "warn" ? "!" : "i";
      return `<div class="check ${c.level}">
        <span class="mark">${mark}</span>
        <div>
          <b>${esc(c.title)}</b>
          <p>${esc(c.detail)}</p>
          <div class="refs">${(c.refs || []).map((r) => `<span>${esc(r)}</span>`).join("")}</div>
        </div>
      </div>`;
    })
    .join("");

  const questions = d.questions.length
    ? `<ul class="qlist">${d.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>`
    : `<p style="font-size:11.5px;color:var(--muted)">当前输入已覆盖关键条件，无强制追问项。</p>`;

  const mix = s.evidence_mix;
  const total = Math.max(1, mix.clause + mix.standard + mix.document);
  const evbar = `<div class="evbar">
      <i style="width:${(mix.standard / total) * 100}%;background:#14635c"></i>
      <i style="width:${(mix.document / total) * 100}%;background:#c08a3e"></i>
      <i style="width:${(mix.clause / total) * 100}%;background:#b05b34"></i>
    </div>
    <div class="ev-legend">
      <div><b style="background:#14635c"></b>标准级 <strong>${mix.standard}</strong></div>
      <div><b style="background:#c08a3e"></b>文件级 <strong>${mix.document}</strong></div>
      <div><b style="background:#b05b34"></b>条款级 <strong>${mix.clause}</strong></div>
    </div>`;

  const asm = `<ul class="asm-list">${d.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`;

  $("#resultPanel").innerHTML = `
    <div class="summary">
      <div class="summary-top">
        <div>
          <div class="summary-input">输入 ${esc(s.input_model)}</div>
          <div class="summary-model">${esc(s.recommended_model)}</div>
          <div class="summary-note">
            ${s.temp_class}℃ 耐温等级 · ${esc(s.structure)}
            ${s.model_changed ? " · 型号已按需求升级" : " · 型号无需变更"}
            ${s.armor_note ? " · " + esc(s.armor_note) : ""}
          </div>
        </div>
        <div class="conf">
          <div class="conf-num">${s.confidence.toFixed(2)}<small>/1.00</small></div>
          <div class="conf-label">置信度 · ${esc(s.confidence_label)}</div>
          <div class="segbar">${confSeg}</div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><b>${s.counts.layers}</b><span>结构层</span></div>
        <div class="stat"><b>${s.counts.candidates}</b><span>候选材料</span></div>
        <div class="stat"><b>${s.counts.excluded}</b><span>硬约束排除</span></div>
        <div class="stat ${s.counts.checks_warn ? "warn" : ""}"><b>${s.counts.checks_warn}</b><span>风险提示</span></div>
        <div class="stat ${s.counts.checks_error ? "err" : ""}"><b>${s.counts.checks_error}</b><span>结构冲突</span></div>
      </div>
    </div>
    ${nar}
    <div class="result-grid">
      <div class="layers">${layers}</div>
      <div class="rail">
        <div class="card"><h3>结构校核 <span class="count">${d.checks.length}</span></h3>${checks}</div>
        <div class="card"><h3>需要确认 <span class="count">${d.questions.length}</span></h3>${questions}</div>
        <div class="card"><h3>证据构成</h3>${evbar}</div>
        <div class="card"><h3>建模假设</h3>${asm}</div>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ 材料库 */

const CAT_LABEL = {
  conductor: "导体", insulation: "绝缘", filler: "填充", tape: "包带",
  shield: "屏蔽", armor: "铠装", sheath: "护套",
};

function renderMaterials() {
  const cats = ["all", ...Object.keys(CAT_LABEL)];
  $("#catFilter").innerHTML = cats
    .map((c) => `<button class="${state.cat === c ? "on" : ""}" data-cat="${c}">${c === "all" ? "全部" : CAT_LABEL[c]}</button>`)
    .join("");
  $$("#catFilter button").forEach((b) =>
    b.addEventListener("click", () => {
      state.cat = b.dataset.cat;
      renderMaterials();
    })
  );

  const q = state.q.trim().toLowerCase();
  const rows = state.materials.filter((m) => {
    if (state.cat !== "all" && m.category !== state.cat) return false;
    if (!q) return true;
    return (m.name_cn + m.name_en + m.grade + m.applications.join("")).toLowerCase().includes(q);
  });

  $("#matTable").innerHTML = `<table class="mat">
    <thead><tr>
      <th style="width:70px">类别</th><th style="width:280px">材料 / 牌号</th>
      <th style="width:330px">关键性能</th><th>适用与限制</th>
      <th style="width:70px">成本</th><th style="width:200px">来源</th>
    </tr></thead>
    <tbody>
    ${rows
      .map((m) => {
        const props = m.props.slice(0, 4).map((p) => `<span>${esc(p.k)} <b>${esc(p.v)}</b> ${esc(p.u || "")}</span>`).join("");
        return `<tr>
          <td><span class="cat">${CAT_LABEL[m.category]}</span></td>
          <td><div class="mname">${esc(m.name_cn)}</div><span class="mgrade">${esc(m.grade)}</span></td>
          <td class="props">${props}</td>
          <td class="lim">${esc(m.applications.slice(0, 2).join("；"))}<br /><span style="color:#9a9388">限制：${esc(m.limitations[0])}</span></td>
          <td style="font-family:var(--mono);font-size:12px">${"●".repeat(Math.round(m.cost_index))}<span style="color:#ddd5c6">${"●".repeat(5 - Math.round(m.cost_index))}</span></td>
          <td class="src">${m.sources.map((s) => esc(s)).join("<br />")}<br /><span style="color:#b0a795">更新 ${esc(m.updated_at)}</span></td>
        </tr>`;
      })
      .join("")}
    </tbody></table>`;
}

$("#matSearch")?.addEventListener("input", (e) => {
  state.q = e.target.value;
  renderMaterials();
});

/* ------------------------------------------------------------------ 证据链 */

function renderCitations() {
  $("#citeCount").textContent = state.standards.length;
  $("#citeList").innerHTML = state.standards
    .map((s) => {
      const lv = s.precision === "clause" ? "std" : s.precision === "standard" ? "std" : "doc";
      const label = s.precision === "clause" ? "条款级" : s.precision === "standard" ? "标准级" : "文件级";
      return `<div class="cite-item">
        <div><span class="code">${esc(s.code)}</span>
          <span class="lv ${lv}" style="margin-left:8px">${label}</span></div>
        <div class="ttl">${esc(s.title)}</div>
        <div class="ttl" style="color:#9a9388">${esc(s.publisher)} · ${esc(s.scope)}</div>
        <div class="use">${(s.key_points || []).slice(0, 3).map((k) => `<span>${esc(k)}</span>`).join("")}</div>
      </div>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ 验证 */

function renderValidation() {
  const pass = state.validation.filter((v) => v.verdict === "pass").length;
  $("#valScore").innerHTML = `通过 <b>${pass}</b> / ${state.validation.length} · 失败 1`;
  $("#valList").innerHTML = state.validation
    .map(
      (v) => `<div class="val ${v.verdict === "fail" ? "fail" : ""}">
        <div class="val-head">
          <span class="id">${esc(v.id)}</span>
          <h3>${esc(v.name)}</h3>
          <span class="verdict ${v.verdict === "fail" ? "v-fail" : "v-pass"}">${v.verdict === "fail" ? "失败 / 待改进" : "通过"}</span>
        </div>
        <div class="val-grid">
          <div class="val-row"><b>输入</b><span>${esc(v.input)}</span></div>
          <div class="val-row"><b>预期</b><span>${esc(v.expect)}</span></div>
          <div class="val-row"><b>实际</b><span>${esc(v.actual)}</span></div>
          <div class="val-row"><b>判断</b><span>${esc(v.note)}</span></div>
        </div>
      </div>`
    )
    .join("");
}

/* ------------------------------------------------------------------ 事件 */

$("#runBtn").addEventListener("click", run);
$("#f-model").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run();
});

boot();
