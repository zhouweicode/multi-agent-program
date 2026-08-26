const $ = (selector) => document.querySelector(selector);
const state = { threadId: null, cursor: 0, events: [], eventSource: null, streaming: false, graphState: {}, running: false, stopping: false, webSearchEnabled: true };

const eventInfo = {
  QUERY_STARTED: ["query", "查询已进入 LangGraph"], ROUTER_COMPLETED: ["router", "Router 完成结构化路由"],
  ENTITY_RESOLUTION_INTERRUPTED: ["entity", "检测到同名实体，等待用户确认"], ENTITY_RESOLUTION_COMPLETED: ["entity", "实体消歧完成"],
  ENTITY_NOT_FOUND: ["entity", "知识库中未找到实体"],
  SUPERVISOR_PLANNED: ["supervisor", "Supervisor 已生成执行计划"], AGENT_TOOL_CALLED: ["agents", "Agent 发起 Tool Call"],
  AGENT_TOOL_COMPLETED: ["agents", "Tool Observation 已返回"], AGENT_COMPLETED: ["agents", "领域 Agent 执行完成"],
  MERGE_COMPLETED: ["merge", "领域结果与证据已合并"], RULE_VALIDATION_COMPLETED: ["validator", "Rule Validator 校验完成"],
  VERIFICATION_COMPLETED: ["verification", "Verification Agent 判断完成"], ANSWER_GENERATED: ["answer", "最终答案已生成"],
  QUERY_RESUMED: ["entity", "从实体消歧中断点恢复"], QUERY_FAILED: ["answer", "查询执行失败"],
  NODE_EXECUTED: [null, "LangGraph Node 执行完成"], NODE_INTERRUPTED: [null, "LangGraph Node 已中断"],
  NODE_FAILED: [null, "LangGraph Node 执行失败"], RUN_STATUS_CHANGED: [null, "Graph Run 状态已更新"]
};

const nodeLabels = {
  router: "Router", entity_resolution: "Entity Resolution", supervisor: "Supervisor",
  talent_agent: "TalentAgent", achievement_agent: "AchievementAgent",
  enterprise_agent: "EnterpriseRelationAgent", industry_agent: "IndustryChainAgent",
  graph_reasoning_agent: "GraphReasoningAgent", merge: "Merge", validator: "Rule Validator",
  web_research_agent: "WebResearchAgent",
  verification_agent: "VerificationAgent", answer: "Answer"
};

function newThreadId() { return `ui-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`; }
function setRunStatus(text, running = false) { const el = $("#runStatus"); el.textContent = text; el.classList.toggle("running", running); }
function setSubmitMode(mode) {
  const button = $("#submitBtn");
  state.running = mode === "running" || mode === "stopping";
  state.stopping = mode === "stopping";
  button.classList.toggle("stop-mode", state.running);
  button.classList.toggle("stopping", state.stopping);
  button.disabled = state.stopping;
  $("#webSearchToggle").disabled = state.running;
  $("#submitLabel").textContent = mode === "running" ? "停止分析" : mode === "stopping" ? "正在停止" : "开始分析";
  $("#submitIcon").textContent = mode === "running" ? "■" : mode === "stopping" ? "…" : "→";
}
function renderWebSearchToggle() {
  const button = $("#webSearchToggle");
  button.classList.toggle("enabled", state.webSearchEnabled);
  button.setAttribute("aria-pressed", String(state.webSearchEnabled));
  button.title = state.webSearchEnabled ? "点击关闭联网搜索" : "点击开启联网搜索";
  $("#webSearchToggleLabel").textContent = `联网搜索：${state.webSearchEnabled ? "已开启" : "已关闭"}`;
}
function finishRun(statusText) {
  setSubmitMode("idle");
  setRunStatus(statusText);
}
function resetUI() {
  stopEventStream();
  state.cursor = 0; state.events = []; state.graphState = {};
  $("#workspace").classList.remove("hidden"); $("#selectionPanel").classList.add("hidden"); $("#answerPanel").classList.add("hidden");
  $("#timeline").innerHTML = '<div class="empty-state"><span class="pulse-ring"></span>等待执行事件…</div>';
  $("#stateJson").textContent = "{}"; $("#eventCount").textContent = "0 events";
  document.querySelectorAll(".flow-node,.agent-cluster span").forEach(el => el.classList.remove("active", "done", "skipped"));
  setRunStatus("执行中", true);
}

function updateFlow(event) {
  const [node] = eventInfo[event.event] || [];
  if (!node) return;
  if (node === "agents") {
    const agent = event.agent_name; const chip = agent && document.querySelector(`[data-agent="${agent}"]`);
    if (chip) { chip.classList.toggle("done", event.event === "AGENT_COMPLETED"); chip.classList.toggle("active", event.event !== "AGENT_COMPLETED"); }
  } else {
    const el = document.querySelector(`[data-node="${node}"]`);
    if (el) { document.querySelectorAll(".flow-node.active").forEach(x => x.classList.remove("active")); el.classList.add(event.event.endsWith("COMPLETED") || event.event === "ANSWER_GENERATED" ? "done" : "active"); }
  }
  if (event.event === "ENTITY_RESOLUTION_INTERRUPTED") setRunStatus("等待实体选择");
  if (event.event === "ANSWER_GENERATED") setRunStatus("已完成");
}

function renderEvent(event) {
  if (state.events.length === 1) $("#timeline").innerHTML = "";
  let [, label = event.event] = eventInfo[event.event] || [];
  if (event.event.startsWith("NODE_") && event.node_name) {
    const suffix = event.event === "NODE_EXECUTED" ? "执行完成" : event.event === "NODE_INTERRUPTED" ? "等待恢复" : "执行失败";
    label = `${nodeLabels[event.node_name] || event.node_name} · ${suffix}`;
  }
  const kind = event.event.includes("TOOL") ? "tool" : event.event.includes("INTERRUPTED") ? "warning" : event.event.includes("COMPLETED") || event.event === "NODE_EXECUTED" || event.event === "ANSWER_GENERATED" ? "success" : "";
  const ignored = new Set(["event", "timestamp", "thread_id", "sequence", "node_input", "node_output", "tool_input", "tool_output"]);
  const details = Object.entries(event).filter(([key]) => !ignored.has(key)).map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`).join(" · ");
  const item = document.createElement("div"); item.className = `timeline-item ${kind}`;
  const time = new Date(event.timestamp).toLocaleTimeString("zh-CN", {hour12:false});
  item.innerHTML = `<button class="event-dot" type="button" aria-label="查看节点完整输入输出"></button><div><div class="event-title"></div><div class="event-meta"></div></div>`;
  item.querySelector(".event-title").textContent = label;
  item.querySelector(".event-meta").textContent = `${time} · ${details || event.event}`;
  item.querySelector(".event-dot").addEventListener("click", () => showEventDetail(event, label));
  $("#timeline").appendChild(item); $("#timeline").scrollTop = $("#timeline").scrollHeight;
  $("#eventCount").textContent = `${state.events.length} events`; updateFlow(event);
}

function formattedJson(value, fallback) {
  return JSON.stringify(value === undefined ? fallback : value, null, 2);
}

function showEventDetail(event, label) {
  const modal = $("#eventModal");
  $("#eventModalTitle").textContent = label;
  $("#eventModalMeta").textContent = `${new Date(event.timestamp).toLocaleString("zh-CN", {hour12:false})} · ${event.event} · sequence=${event.sequence}`;
  const input = event.node_input ?? event.tool_input;
  const output = event.node_output ?? event.tool_output;
  $("#eventInput").textContent = formattedJson(input, {info: "该业务事件没有独立输入；请点击相邻的 NODE 事件查看完整 State。"});
  $("#eventOutput").textContent = formattedJson(output, {info: "该业务事件没有独立输出；请点击相邻的 NODE 事件查看完整 State Update。"});
  $("#eventRaw").textContent = formattedJson(event, {});
  modal.showModal();
}

function stopEventStream() {
  state.streaming = false;
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
}

function startEventStream() {
  stopEventStream();
  state.streaming = true;
  const source = new EventSource(`/queries/${encodeURIComponent(state.threadId)}/stream?after=${state.cursor}`);
  state.eventSource = source;
  source.addEventListener("trace", message => {
    const event = JSON.parse(message.data);
    if (event.sequence <= state.cursor) return;
    state.cursor = event.sequence;
    state.events.push(event);
    renderEvent(event);
  });
  source.addEventListener("status", message => {
    const payload = JSON.parse(message.data);
    stopEventStream();
    if (payload.status === "NEED_USER_SELECTION") {
      showCandidates(payload.interrupt);
      finishRun("等待实体选择");
    } else if (payload.status === "ENTITY_NOT_FOUND") {
      const names = (payload.interrupt?.mentions || []).join("、");
      finishRun("实体未找到");
      alert(`未找到实体：${names || "未知实体"}。请补充机构、职称或研究方向后重新提问。`);
    } else if (payload.status === "COMPLETED") {
      renderResult(payload);
    } else if (payload.status === "CANCELLED") {
      finishRun("已取消，可重新提问");
    } else if (payload.status === "TIMED_OUT") {
      finishRun("执行超时");
      alert("查询超过运行时限，已安全终止。请缩小问题范围后重试。");
    } else {
      finishRun("执行失败");
      alert(`执行失败：${payload.error?.message || "未知错误"}`);
    }
  });
  source.onerror = () => {
    source.close();
    if (state.streaming) setTimeout(startEventStream, 1000);
  };
}

function showCandidates(interrupt) {
  const root = $("#candidateGroups"); root.innerHTML = "";
  Object.entries(interrupt.candidates || {}).forEach(([mention, candidates]) => {
    const group = document.createElement("div"); group.className = "candidate-group";
    const heading = document.createElement("h3"); heading.textContent = `“${mention}” 的候选专家`; group.appendChild(heading);
    const grid = document.createElement("div"); grid.className = "candidate-grid";
    candidates.forEach((candidate, index) => {
      const label = document.createElement("label"); label.className = "candidate-card";
      const input = document.createElement("input"); input.type = "radio"; input.name = `candidate-${mention}`; input.value = candidate.entity_id; input.dataset.mention = mention; input.required = true; if (index === 0) input.checked = true;
      const body = document.createElement("div"); body.className = "candidate-body";
      const name = document.createElement("span"); name.className = "candidate-name"; name.textContent = `${candidate.name} · ${candidate.title || "职称未知"}`;
      const org = document.createElement("span"); org.className = "candidate-org"; org.textContent = candidate.organization || "机构未知";
      const id = document.createElement("span"); id.className = "candidate-id"; id.textContent = candidate.entity_id;
      const score = document.createElement("span"); score.className = "candidate-id"; score.textContent = candidate.final_score === undefined ? "" : `置信分 ${candidate.final_score} · ${(candidate.match_reasons || []).join("、")}`;
      body.append(name, org, id, score); label.append(input, body); grid.appendChild(label);
    }); group.appendChild(grid); root.appendChild(group);
  });
  $("#selectionPanel").classList.remove("hidden"); $("#selectionPanel").scrollIntoView({behavior:"smooth", block:"center"});
}

function renderWebSources(graphState) {
  const panel = $("#webSourcesPanel");
  const root = $("#webSourceCards");
  root.innerHTML = "";
  const rows = [];
  const seen = new Set();
  for (const fact of graphState.web_result?.facts || []) {
    if (fact.tool !== "search_web") continue;
    for (const row of fact.data?.results || []) {
      if (!row?.url || seen.has(row.url) || !/^https:\/\//i.test(row.url)) continue;
      seen.add(row.url); rows.push(row);
    }
  }
  if (!rows.length) { panel.classList.add("hidden"); return; }
  rows.slice(0, 3).forEach((row, index) => {
    const card = document.createElement("article"); card.className = "web-source-card";
    const marker = document.createElement("span"); marker.className = "source-index"; marker.textContent = String(index + 1).padStart(2, "0");
    const body = document.createElement("div");
    const link = document.createElement("a"); link.href = row.url; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.textContent = row.title || "未命名网页";
    const meta = document.createElement("div"); meta.className = "source-meta";
    try { meta.textContent = new URL(row.url).hostname; } catch { meta.textContent = "公开网页"; }
    const snippet = document.createElement("p");
    const cleanSnippet = String(row.snippet || "").replace(/\s+/g, " ").replace(/\|+/g, " ").trim();
    snippet.textContent = cleanSnippet ? `${cleanSnippet.slice(0, 100)}${cleanSnippet.length > 100 ? "…" : ""}` : "该来源未返回摘要。";
    body.append(link, meta, snippet); card.append(marker, body); root.appendChild(card);
  });
  $("#webSourcesCount").textContent = `展示 ${Math.min(rows.length, 3)} / ${rows.length} 条`;
  panel.classList.remove("hidden");
}

function renderResult(payload) {
  state.graphState = payload.state || {}; $("#stateJson").textContent = JSON.stringify(state.graphState, null, 2);
  $("#answerText").textContent = payload.final_answer || state.graphState.final_answer || "暂无答案";
  const validation = state.graphState.validation_result || {}; const badge = $("#validationBadge");
  badge.textContent = validation.valid ? "VALIDATED" : "VALIDATION FAILED"; badge.style.color = validation.valid ? "var(--green)" : "var(--danger)";
  const chips = $("#entityChips"); chips.innerHTML = "";
  Object.entries(state.graphState.resolved_entities || {}).forEach(([name, id]) => { const chip = document.createElement("span"); chip.textContent = `${name} · ${id}`; chips.appendChild(chip); });
  renderWebSources(state.graphState);
  $("#answerPanel").classList.remove("hidden"); $("#answerPanel").scrollIntoView({behavior:"smooth", block:"start"}); finishRun("已完成");
}

async function handleResponse(response) {
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.detail || `HTTP ${response.status}`); }
  const payload = await response.json();
  state.threadId = payload.run_id || payload.thread_id || state.threadId;
  if (payload.status === "RUNNING") { setSubmitMode("running"); setRunStatus("执行中", true); startEventStream(); }
  else if (payload.status === "NEED_USER_SELECTION") { showCandidates(payload.interrupt); finishRun("等待实体选择"); }
  else { renderResult(payload); stopEventStream(); }
}

$("#queryForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.running) { await cancelCurrentAnalysis(); return; }
  resetUI(); state.threadId = newThreadId(); setSubmitMode("running");
  const button = $("#submitBtn"); button.disabled = true;
  try { const response = await fetch("/queries", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({question:$("#question").value.trim(), thread_id:state.threadId, max_replans:Number($("#maxReplans").value), web_search_enabled:state.webSearchEnabled})}); await handleResponse(response); }
  catch (error) { finishRun("执行失败"); alert(`执行失败：${error.message}`); stopEventStream(); }
  finally { if (!state.stopping) button.disabled = false; }
});

$("#selectionForm").addEventListener("submit", async event => {
  event.preventDefault(); const selections = {};
  document.querySelectorAll('#candidateGroups input[type="radio"]:checked').forEach(input => { selections[input.dataset.mention] = input.value; });
  $("#selectionPanel").classList.add("hidden"); setSubmitMode("running"); setRunStatus("恢复执行中", true);
  try { const response = await fetch(`/queries/${encodeURIComponent(state.threadId)}/resume`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({selections})}); await handleResponse(response); }
  catch (error) { finishRun("恢复失败"); alert(`恢复失败：${error.message}`); stopEventStream(); }
});

async function cancelCurrentAnalysis() {
  if (!state.threadId || state.stopping) return;
  setSubmitMode("stopping"); setRunStatus("正在停止", true);
  try {
    const response = await fetch(`/queries/${encodeURIComponent(state.threadId)}/cancel`, {method:"POST"});
    if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail || `HTTP ${response.status}`); }
    const payload = await response.json();
    if (payload.status === "CANCELLED") { stopEventStream(); finishRun("已取消，可重新提问"); }
  } catch (error) {
    setSubmitMode("running"); setRunStatus("执行中", true); alert(`停止失败：${error.message}`);
  }
}

document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => { $("#question").value = button.dataset.example; $("#question").focus(); }));
$("#webSearchToggle").addEventListener("click", () => {
  if (state.running) return;
  state.webSearchEnabled = !state.webSearchEnabled;
  renderWebSearchToggle();
});
$("#copyState").addEventListener("click", async () => { await navigator.clipboard.writeText($("#stateJson").textContent); $("#copyState").textContent = "已复制"; setTimeout(() => $("#copyState").textContent = "复制", 1200); });
$("#closeEventModal").addEventListener("click", () => $("#eventModal").close());
$("#eventModal").addEventListener("click", event => { if (event.target === $("#eventModal")) $("#eventModal").close(); });

fetch("/health").then(response => response.json()).then(payload => { $("#healthDot").classList.add("online"); $("#healthText").textContent = `Stage ${payload.stage} · ${payload.embedding_provider} · ${payload.entity_backend}`; }).catch(() => { $("#healthText").textContent = "系统离线"; });
renderWebSearchToggle();
