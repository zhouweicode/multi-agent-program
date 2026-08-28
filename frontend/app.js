const $ = (selector) => document.querySelector(selector);
const defaultLoginPasswords = {
  "user-admin": "Admin@123",
  "user-researcher": "Research@123",
  "user-analyst": "Analyst@123",
};
const state = {
  threadId: null, cursor: 0, events: [], eventSource: null, streaming: false,
  graphState: {}, running: false, stopping: false, webSearchEnabled: true,
  currentUser: null, conversationId: null, memoryEnabled: false,
  experienceMemoryEnabled: true,
  memoryEntities: [], memoryTurnCount: 0,
  memoryFacts: [], memorySummary: null, editingMemoryFactId: null,
};

const memoryCategoryLabels = {
  preference: "偏好", focus: "长期关注", correction: "用户修正",
  constraint: "稳定约束", output_format: "输出格式", context: "其他上下文",
};

const eventInfo = {
  QUERY_STARTED: ["query", "查询已进入 LangGraph"],
  MEMORY_RECALLED: ["memory", "已召回当前会话记忆"],
  MEMORY_REFERENCE_RESOLVED: ["memory", "已通过会话记忆完成指代解析"],
  MEMORY_REFERENCE_AMBIGUOUS: ["memory", "会话指代存在多个候选，等待确认"],
  MEMORY_WRITTEN: ["memory", "本轮实体已写入会话记忆"],
  EXPERIENCE_RECALL_HIT: ["experience", "命中历史查询策略"],
  EXPERIENCE_RECALL_MISS: ["experience", "未命中历史查询策略"],
  EXPERIENCE_RECALL_DISABLED: ["experience", "查询经验记忆已关闭"],
  EXPERIENCE_ROUTE_COMPARED: ["experience", "历史路由与当前路由已比较"],
  EXPERIENCE_WRITTEN: ["experience", "本轮执行经验已沉淀"],
  EXPERIENCE_WRITEBACK_SKIPPED: ["experience", "本轮经验写回已跳过"],
  LONG_TERM_MEMORY_RECALLED: ["memory", "已召回相关长期记忆"],
  LONG_TERM_MEMORY_RECALL_FAILED_OPEN: ["memory", "长期记忆召回失败，已降级继续"],
  LONG_TERM_MEMORY_UPDATE_QUEUED: ["memory", "长期记忆抽取任务已进入队列"],
  LONG_TERM_MEMORY_EXTRACTED: ["memory", "可复用长期记忆已完成抽取"],
  ROUTER_COMPLETED: ["router", "Router 完成结构化路由"],
  SKILL_SELECTED: ["skill", "已选择运行时 Skill"],
  SKILL_PLAN_CREATED: ["supervisor", "Skill 能力已展开为领域任务"],
  ENTITY_RESOLUTION_INTERRUPTED: ["entity", "检测到同名实体，等待用户确认"], ENTITY_RESOLUTION_COMPLETED: ["entity", "实体消歧完成"],
  ENTITY_NOT_FOUND: ["entity", "知识库中未找到实体"],
  SUPERVISOR_PLANNED: ["supervisor", "Supervisor 已生成执行计划"], AGENT_TOOL_CALLED: ["agents", "Agent 发起 Tool Call"],
  AGENT_TOOL_COMPLETED: ["agents", "Tool Observation 已返回"], AGENT_COMPLETED: ["agents", "领域 Agent 执行完成"],
  MERGE_COMPLETED: ["merge", "领域结果与证据已合并"], RULE_VALIDATION_COMPLETED: ["validator", "Rule Validator 校验完成"],
  VERIFICATION_COMPLETED: ["verification", "Verification Agent 判断完成"], ANSWER_GENERATED: ["answer", "最终答案已生成"],
  EXPERT_REPORT_GENERATED: ["skill", "专家报告已完成证据化组装"],
  INDUSTRY_LANDSCAPE_GENERATED: ["skill", "产业全景报告已完成证据化组装"],
  QUERY_RESUMED: ["entity", "从实体消歧中断点恢复"], QUERY_FAILED: ["answer", "查询执行失败"],
  NODE_EXECUTED: [null, "LangGraph Node 执行完成"], NODE_INTERRUPTED: [null, "LangGraph Node 已中断"],
  NODE_FAILED: [null, "LangGraph Node 执行失败"], RUN_STATUS_CHANGED: [null, "Graph Run 状态已更新"]
};

const nodeLabels = {
  conversation_memory_recall: "Conversation Memory Recall",
  conversation_memory_writeback: "Conversation Memory Writeback",
  query_experience_recall: "Query Experience Recall",
  query_experience_writeback: "Query Experience Writeback",
  router: "Router", entity_resolution: "Entity Resolution", supervisor: "Supervisor",
  talent_agent: "TalentAgent", achievement_agent: "AchievementAgent",
  enterprise_agent: "EnterpriseRelationAgent", industry_agent: "IndustryChainAgent",
  graph_reasoning_agent: "GraphReasoningAgent", merge: "Merge", validator: "Rule Validator",
  web_research_agent: "WebResearchAgent",
  verification_agent: "VerificationAgent", expert_report: "Expert Report Composer",
  industry_landscape: "Industry Landscape Composer", answer: "Answer"
};

function newThreadId() { return `ui-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`; }
function newConversationId() { return `conv-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`; }
function prefillLoginPassword() {
  $("#loginPassword").value = defaultLoginPasswords[$("#loginUser").value] || "";
}
function userStorageKey(name) { return `graphrag.${state.currentUser?.user_id || "anonymous"}.${name}`; }
function loadUserSessionState() {
  state.conversationId = sessionStorage.getItem(userStorageKey("conversationId"));
  state.memoryEnabled = sessionStorage.getItem(userStorageKey("memoryEnabled")) === "true";
  state.experienceMemoryEnabled = sessionStorage.getItem(userStorageKey("experienceMemoryEnabled")) !== "false";
  state.memoryEntities = [];
  state.memoryTurnCount = 0;
}

function showLogin(message = "") {
  stopEventStream();
  if ($("#memoryManagerModal")?.open) $("#memoryManagerModal").close();
  state.currentUser = null;
  document.body.classList.add("auth-pending");
  $("#loginScreen").classList.remove("hidden");
  $("#userMenu").classList.add("hidden");
  $("#loginError").textContent = message;
  $("#loginError").classList.toggle("hidden", !message);
  prefillLoginPassword();
}

async function showApplication(user) {
  state.currentUser = user;
  loadUserSessionState();
  $("#currentUserName").textContent = user.display_name;
  $("#currentUsername").textContent = `@${user.username}`;
  $("#userMenu").classList.remove("hidden");
  $("#loginScreen").classList.add("hidden");
  document.body.classList.remove("auth-pending");
  renderWebSearchToggle();
  renderExperienceMemoryToggle();
  renderConversationMemory();
  if (state.memoryEnabled) await refreshConversationMemory();
  await loadRunOptions();
}

async function loadLoginUsers() {
  const select = $("#loginUser");
  try {
    const response = await fetch("/auth/users");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    select.innerHTML = "";
    (payload.users || []).forEach(user => {
      const option = document.createElement("option");
      option.value = user.user_id;
      option.textContent = `${user.display_name}（${user.username}）`;
      select.appendChild(option);
    });
    select.disabled = !payload.users?.length;
    prefillLoginPassword();
  } catch (error) {
    select.innerHTML = '<option value="">用户列表加载失败</option>';
    select.disabled = true;
    showLogin(`无法读取用户列表：${error.message}`);
  }
}

async function initializeAuth() {
  await loadLoginUsers();
  try {
    const response = await fetch("/auth/me");
    if (!response.ok) { showLogin(); return; }
    const payload = await response.json();
    await showApplication(payload.user);
  } catch (error) {
    showLogin(`连接认证服务失败：${error.message}`);
  }
}

function setRunStatus(text, running = false) { const el = $("#runStatus"); el.textContent = text; el.classList.toggle("running", running); }
function setSubmitMode(mode) {
  const button = $("#submitBtn");
  state.running = mode === "running" || mode === "stopping";
  state.stopping = mode === "stopping";
  button.classList.toggle("stop-mode", state.running);
  button.classList.toggle("stopping", state.stopping);
  button.disabled = state.stopping;
  $("#webSearchToggle").disabled = state.running;
  $("#conversationMemoryToggle").disabled = state.running;
  $("#experienceMemoryToggle").disabled = state.running;
  $("#clearConversationMemory").disabled = state.running || !state.conversationId;
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
function renderExperienceMemoryToggle() {
  const button = $("#experienceMemoryToggle");
  button.classList.toggle("enabled", state.experienceMemoryEnabled);
  button.setAttribute("aria-pressed", String(state.experienceMemoryEnabled));
  button.title = state.experienceMemoryEnabled ? "点击关闭查询经验记忆" : "点击开启查询经验记忆";
  $("#experienceMemoryToggleLabel").textContent = `经验记忆：${state.experienceMemoryEnabled ? "已开启" : "已关闭"}`;
}
function ensureConversationId() {
  if (!state.conversationId) {
    state.conversationId = newConversationId();
    sessionStorage.setItem(userStorageKey("conversationId"), state.conversationId);
  }
  return state.conversationId;
}
function renderConversationMemory() {
  const button = $("#conversationMemoryToggle");
  button.classList.toggle("enabled", state.memoryEnabled);
  button.setAttribute("aria-pressed", String(state.memoryEnabled));
  button.title = state.memoryEnabled ? "点击关闭对话记忆（不会删除已有记忆）" : "点击开启对话记忆";
  $("#conversationMemoryToggleLabel").textContent = `对话记忆：${state.memoryEnabled ? "已开启" : "已关闭"}`;
  $("#clearConversationMemory").disabled = state.running || !state.conversationId;
  $("#memoryContext").classList.toggle("hidden", !state.memoryEnabled);
  const root = $("#memoryEntityChips"); root.innerHTML = "";
  if (!state.memoryEntities.length) {
    const empty = document.createElement("span"); empty.textContent = "尚未记住实体"; root.appendChild(empty);
  } else {
    state.memoryEntities.forEach(entity => {
      const chip = document.createElement("span");
      chip.textContent = `${entity.name}${entity.organization ? ` · ${entity.organization}` : ""}`;
      root.appendChild(chip);
    });
  }
  $("#memoryTurnCount").textContent = `${state.memoryTurnCount} 轮对话`;
}
async function refreshConversationMemory() {
  if (!state.conversationId) { state.memoryEntities = []; state.memoryTurnCount = 0; renderConversationMemory(); return; }
  try {
    const response = await fetch(`/conversations/${encodeURIComponent(state.conversationId)}/memory`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const memory = await response.json();
    state.memoryEntities = memory.entities || [];
    state.memoryTurnCount = Number(memory.turn_count || 0);
  } catch (error) {
    console.warn("会话记忆加载失败", error);
  }
  renderConversationMemory();
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
  $("#memoryUsagePanel").classList.add("hidden");
  $("#memoryUsageFacts").innerHTML = "";
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
  if (event.event === "MEMORY_REFERENCE_AMBIGUOUS") setRunStatus("等待指代确认");
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

function renderExperience(graphState) {
  const panel = $("#experiencePanel");
  if (!graphState.experience_memory_enabled) { panel.classList.add("hidden"); return; }
  const match = graphState.experience_match;
  const pattern = graphState.experience_pattern;
  const strategy = graphState.experience_strategy || pattern?.strategy || {};
  const hit = graphState.experience_recall_status === "HIT" && match;
  $("#experienceStatus").textContent = hit ? "HIT · SHADOW" : "MISS · LEARNED";
  $("#experienceSummary").textContent = hit
    ? `命中历史模式 ${match.pattern_id}。当前处于 Shadow 模式，仅比较并展示策略，不绕过 Router、实体消歧或 Validator。`
    : `本次没有可复用的历史模式；执行完成后已${graphState.experience_writeback_status === "WRITTEN" ? "沉淀" : "检查"}本轮经验。`;
  const specs = hit ? [
    ["相似度", `${(Number(match.similarity || 0) * 100).toFixed(1)}%`],
    ["置信度", `${(Number(match.confidence || 0) * 100).toFixed(1)}%`],
    ["历史样本", String(match.sample_count || 0)],
    ["成功率", `${(Number(match.success_rate || 0) * 100).toFixed(1)}%`],
    ["路由一致", match.route_agreement ? "是" : "否"],
    ["可进入 Assist", match.applicable ? "是" : "样本不足"],
  ] : [
    ["模式样本", String(pattern?.sample_count || 1)],
    ["本轮质量", `${(Number(pattern?.average_quality || 0) * 100).toFixed(1)}%`],
    ["写回状态", graphState.experience_writeback_status || "SKIPPED"],
  ];
  const metrics = $("#experienceMetrics"); metrics.innerHTML = "";
  specs.forEach(([label, value]) => {
    const card = document.createElement("div");
    const name = document.createElement("span"); name.textContent = label;
    const main = document.createElement("strong"); main.textContent = value;
    card.append(name, main); metrics.appendChild(card);
  });
  const agents = (strategy.agents || []).join(" → ") || "暂无推荐 Agent";
  const toolRows = Object.entries(strategy.tools_by_agent || {}).map(([agent, tools]) => `${agent}: ${tools.join(" → ")}`).join("；");
  $("#experienceStrategy").textContent = `历史策略：${agents}${toolRows ? `｜${toolRows}` : ""}`;
  panel.classList.remove("hidden");
}

function formatMemoryDate(value, empty = "长期有效") {
  if (!value) return empty;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {hour12:false});
}

function renderMemoryUsage(graphState) {
  const panel = $("#memoryUsagePanel");
  const root = $("#memoryUsageFacts");
  root.innerHTML = "";
  const status = graphState.long_term_memory_recall_status;
  const facts = graphState.long_term_memory_facts || [];
  if (!status || status === "DISABLED" || status === "SKIPPED") {
    panel.classList.add("hidden");
    return;
  }
  const applied = new Set(graphState.long_term_memory_applied_fact_ids || []);
  $("#memoryUsageStatus").textContent = `${status} · ${facts.length}`;
  if (!facts.length) {
    const empty = document.createElement("p");
    empty.className = "memory-usage-empty";
    empty.textContent = status === "FAILED_OPEN"
      ? "记忆检索本轮已安全降级，不影响知识库查询。"
      : "本轮未召回与问题相关的长期记忆。";
    root.appendChild(empty);
  }
  facts.forEach(fact => {
    const card = document.createElement("article"); card.className = "memory-usage-fact";
    const heading = document.createElement("div"); heading.className = "memory-fact-heading";
    const category = document.createElement("span"); category.className = "memory-category";
    category.textContent = memoryCategoryLabels[fact.category] || fact.category || "记忆";
    const use = document.createElement("span"); use.className = applied.has(fact.fact_id) ? "memory-applied" : "memory-recalled";
    use.textContent = applied.has(fact.fact_id) ? "已应用到答案" : "已召回";
    const content = document.createElement("p"); content.textContent = fact.content || "";
    const meta = document.createElement("small");
    meta.textContent = `置信度 ${(Number(fact.confidence || 0) * 100).toFixed(0)}% · 来源 ${fact.source_conversation_id || fact.source_run_id || "未知"}`;
    heading.append(category, use); card.append(heading, content, meta); root.appendChild(card);
  });
  panel.classList.remove("hidden");
}

function renderResult(payload) {
  state.graphState = payload.state || {}; $("#stateJson").textContent = JSON.stringify(state.graphState, null, 2);
  if (state.graphState.report_draft) renderExpertReport(state.graphState.report_draft);
  else { $("#answerText").classList.remove("expert-report"); $("#answerText").textContent = payload.final_answer || state.graphState.final_answer || "暂无答案"; }
  const validation = state.graphState.validation_result || {}; const badge = $("#validationBadge");
  badge.textContent = validation.valid ? "VALIDATED" : "VALIDATION FAILED"; badge.style.color = validation.valid ? "var(--green)" : "var(--danger)";
  const chips = $("#entityChips"); chips.innerHTML = "";
  Object.entries(state.graphState.resolved_entities || {}).forEach(([name, id]) => { const chip = document.createElement("span"); chip.textContent = `${name} · ${id}`; chips.appendChild(chip); });
  renderWebSources(state.graphState);
  renderExperience(state.graphState);
  renderMemoryUsage(state.graphState);
  if (state.memoryEnabled) {
    state.memoryEntities = state.graphState.conversation_entities || state.memoryEntities;
    state.memoryTurnCount = Number(state.graphState.conversation_turn_count || state.memoryTurnCount);
    renderConversationMemory();
  }
  $("#answerPanel").classList.remove("hidden"); $("#answerPanel").scrollIntoView({behavior:"smooth", block:"start"}); finishRun("已完成");
  setTimeout(loadRunOptions, 250);
}

function renderExpertReport(report) {
  const root = $("#answerText"); root.innerHTML = ""; root.classList.add("expert-report");
  const evidenceOrder = new Map((report.evidence_ids || []).map((id, index) => [id, index + 1]));
  const addText = (tag, value, className = "") => {
    const element = document.createElement(tag); element.textContent = value;
    if (className) element.className = className; root.appendChild(element); return element;
  };
  const citation = ids => {
    const numbers = (ids || []).map(id => evidenceOrder.get(id)).filter(Boolean);
    return numbers.length ? `〔证据 ${numbers.join(",")}〕` : "";
  };
  const isIndustry = report.skill_id === "industry_landscape";
  const industryTitle = report.industry_name?.endsWith("产业") || report.industry_name?.endsWith("产业链") || report.industry_name?.endsWith("行业")
    ? `${report.industry_name}全景报告` : `${report.industry_name}产业全景报告`;
  addText("h2", isIndustry ? industryTitle : `${report.entity_name}专家报告`);
  addText("p", `${isIndustry ? "产业" : "实体"} ${isIndustry ? report.industry_id : report.entity_id} · ${report.report_type} · 面向 ${report.audience} · 证据覆盖率 ${(Number(report.evidence_coverage || 0) * 100).toFixed(0)}%`, "report-meta");
  addText("h3", "执行摘要"); addText("p", report.executive_summary || "");
  (report.sections || []).forEach(section => {
    addText("h3", section.title);
    addText("p", `状态：${section.status}。${section.summary}`, `report-status ${section.status}`);
    const list = document.createElement("ul");
    (section.claims || []).forEach(claim => {
      const item = document.createElement("li"); item.textContent = `${claim.text} ${citation(claim.evidence_ids)}`; list.appendChild(item);
    });
    root.appendChild(list);
  });
  addText("h3", "风险与数据缺口");
  const gaps = document.createElement("ul");
  (report.risks_and_gaps || []).forEach(value => { const item = document.createElement("li"); item.textContent = value; gaps.appendChild(item); });
  root.appendChild(gaps);
  addText("h3", "证据目录");
  const catalog = document.createElement("ol");
  (report.evidence_catalog || []).forEach(record => {
    const item = document.createElement("li"); item.textContent = `${record.source_name} / ${record.source_record_id}（${record.fact_type}）`; catalog.appendChild(item);
  });
  root.appendChild(catalog);
}

function runOptionLabel(run) {
  const version = run.metadata?.workflow_version || "unknown";
  return `${run.run_id} · ${run.status} · ${version}`;
}

function setRunOptions(select, runs, selected) {
  select.innerHTML = "";
  runs.forEach(run => {
    const option = document.createElement("option");
    option.value = run.run_id; option.textContent = runOptionLabel(run);
    if (run.run_id === selected) option.selected = true;
    select.appendChild(option);
  });
}

async function loadRunOptions() {
  try {
    const response = await fetch("/observability/runs?limit=30");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const runs = (await response.json()).runs || [];
    const previousLeft = $("#leftRunSelect").value;
    const previousRight = $("#rightRunSelect").value;
    const left = runs.some(run => run.run_id === previousLeft) ? previousLeft : runs[1]?.run_id || runs[0]?.run_id;
    const right = runs.some(run => run.run_id === previousRight) ? previousRight : runs[0]?.run_id;
    setRunOptions($("#leftRunSelect"), runs, left);
    setRunOptions($("#rightRunSelect"), runs, right);
    $("#compareRuns").disabled = runs.length < 2;
    $("#compareEmpty").textContent = runs.length < 2 ? "至少完成两个查询后即可进行 Run 对比。" : "选择两个 Run，查看质量与性能差异。";
  } catch (error) {
    $("#compareEmpty").textContent = `运行记录加载失败：${error.message}`;
  }
}

function compactNumber(value, digits = 2) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", {maximumFractionDigits: digits});
}

function renderRunHeader(target, run) {
  const meta = run.metadata || {};
  target.innerHTML = "";
  const title = document.createElement("h3"); title.textContent = run.run_id;
  const detail = document.createElement("p");
  detail.textContent = `${meta.model_name || meta.model_provider || "unknown model"} · workflow ${meta.workflow_version || "unknown"} · prompt ${meta.prompt_version || "unknown"}`;
  const status = document.createElement("span"); status.className = `compare-status ${run.status === "COMPLETED" ? "ok" : ""}`; status.textContent = run.status;
  target.append(title, detail, status);
}

function renderSpanList(target, spans) {
  target.innerHTML = "";
  const ordered = [...spans].sort((a, b) => Number(b.duration_ms) - Number(a.duration_ms)).slice(0, 18);
  if (!ordered.length) { target.textContent = "该 Run 暂无 Span。"; return; }
  ordered.forEach(span => {
    const row = document.createElement("div"); row.className = "compare-span-row";
    const name = document.createElement("span"); name.textContent = span.name;
    const metrics = document.createElement("small");
    metrics.textContent = `${span.kind} · ${compactNumber(span.duration_ms)} ms · ${compactNumber(span.total_tokens, 0)} tokens`;
    const status = document.createElement("i"); status.className = span.status === "OK" ? "ok" : "error"; status.textContent = span.status;
    row.append(name, metrics, status); target.appendChild(row);
  });
}

function renderCompareMetrics(payload) {
  const root = $("#compareMetrics"); root.innerHTML = "";
  const left = payload.left.summary; const right = payload.right.summary; const delta = payload.delta;
  const successRate = summary => summary.tool_calls ? summary.tool_successes / summary.tool_calls * 100 : 100;
  const specs = [
    ["总耗时", `${compactNumber(left.duration_ms)} → ${compactNumber(right.duration_ms)} ms`, `${compactNumber(delta.duration_ms)} ms`],
    ["Token", `${compactNumber(left.total_tokens, 0)} → ${compactNumber(right.total_tokens, 0)}`, `${compactNumber(delta.total_tokens, 0)}`],
    ["模型成本", `${compactNumber(left.cost, 6)} → ${compactNumber(right.cost, 6)} ${right.cost_currency}`, `${compactNumber(delta.cost, 6)}`],
    ["工具成功率", `${compactNumber(successRate(left))}% → ${compactNumber(successRate(right))}%`, `${right.tool_successes}/${right.tool_calls}`],
    ["重规划", `${left.replan_count} → ${right.replan_count}`, `${delta.replan_count >= 0 ? "+" : ""}${delta.replan_count}`],
    ["错误数", `${left.error_count} → ${right.error_count}`, `${delta.error_count >= 0 ? "+" : ""}${delta.error_count}`],
  ];
  specs.forEach(([label, value, change]) => {
    const card = document.createElement("div");
    const name = document.createElement("span"); name.textContent = label;
    const main = document.createElement("strong"); main.textContent = value;
    const diff = document.createElement("small"); diff.textContent = `Δ ${change}`;
    card.append(name, main, diff); root.appendChild(card);
  });
}

async function compareSelectedRuns() {
  const left = $("#leftRunSelect").value; const right = $("#rightRunSelect").value;
  if (!left || !right || left === right) { $("#compareEmpty").textContent = "请选择两个不同的 Run。"; return; }
  $("#compareRuns").disabled = true; $("#compareEmpty").textContent = "正在加载 Trace 对比…";
  try {
    const response = await fetch(`/observability/compare?left_run_id=${encodeURIComponent(left)}&right_run_id=${encodeURIComponent(right)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderCompareMetrics(payload);
    renderRunHeader($("#leftRunHeader"), payload.left); renderRunHeader($("#rightRunHeader"), payload.right);
    renderSpanList($("#leftSpanList"), payload.left.spans); renderSpanList($("#rightSpanList"), payload.right.spans);
    $("#compareResult").classList.remove("hidden"); $("#compareEmpty").textContent = "Span 按耗时从高到低展示。";
  } catch (error) {
    $("#compareEmpty").textContent = `Run 对比失败：${error.message}`;
  } finally { $("#compareRuns").disabled = false; }
}

function renderMemorySummary(summary) {
  state.memorySummary = summary;
  const root = $("#memorySummaryCards"); root.innerHTML = "";
  const specs = [
    ["长期事实", summary.fact_count || 0],
    ["当前有效", summary.active_fact_count || 0],
    ["待复核", summary.review_due_count || 0],
    ["累计召回", summary.total_recall_count || 0],
    ["实际应用", summary.total_application_count || 0],
    ["最近更新", formatMemoryDate(summary.last_updated_at, "暂无")],
  ];
  specs.forEach(([label, value]) => {
    const card = document.createElement("div");
    const name = document.createElement("span"); name.textContent = label;
    const content = document.createElement("strong"); content.textContent = String(value);
    card.append(name, content); root.appendChild(card);
  });
}

function renderMemoryFacts(facts) {
  state.memoryFacts = facts;
  const root = $("#memoryFactList"); root.innerHTML = "";
  $("#memoryEmpty").classList.toggle("hidden", Boolean(facts.length));
  facts.forEach(fact => {
    const card = document.createElement("article");
    card.className = `memory-fact-card${fact.expired ? " expired" : ""}`;
    const heading = document.createElement("div"); heading.className = "memory-fact-heading";
    const category = document.createElement("span"); category.className = "memory-category";
    category.textContent = memoryCategoryLabels[fact.category] || fact.category || "其他上下文";
    const status = document.createElement("span"); status.className = fact.expired ? "memory-expired" : "memory-active";
    status.textContent = fact.expired ? "已过期" : "有效";
    const content = document.createElement("p"); content.textContent = fact.content || "";
    const meta = document.createElement("small");
    meta.textContent = `置信度 ${(Number(fact.confidence || 0) * 100).toFixed(0)}% · 召回 ${fact.recall_count || 0} 次 · 应用 ${fact.application_count || 0} 次 · 最近召回 ${formatMemoryDate(fact.last_recalled_at, "尚未召回")} · 有效期 ${formatMemoryDate(fact.expected_valid_until)} · 来源 ${fact.source_conversation_id || fact.source_run_id || "未知"} · revision ${fact.revision || 1}`;
    const actions = document.createElement("div"); actions.className = "memory-fact-actions";
    const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "编辑";
    edit.addEventListener("click", () => openMemoryEditor(fact));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger"; remove.textContent = "删除";
    remove.addEventListener("click", () => deleteMemoryFact(fact));
    if (fact.review_status === "due") {
      const renew = document.createElement("button"); renew.type = "button"; renew.textContent = "续期 90 天";
      renew.addEventListener("click", () => reviewMemoryFact(fact, "renew"));
      const archive = document.createElement("button"); archive.type = "button"; archive.className = "danger"; archive.textContent = "归档";
      archive.addEventListener("click", () => reviewMemoryFact(fact, "archive"));
      actions.append(renew, archive);
    }
    heading.append(category, status); actions.append(edit, remove); card.append(heading, content, meta, actions); root.appendChild(card);
  });
}

async function loadMemoryManager() {
  const query = $("#memorySearchInput").value.trim();
  const category = $("#memoryCategoryFilter").value;
  const params = new URLSearchParams({limit: "100"});
  if (query) params.set("query", query);
  if (category) params.set("category", category);
  $("#memoryEmpty").textContent = "正在读取个人记忆…";
  $("#memoryEmpty").classList.remove("hidden");
  try {
    const [summaryResponse, factsResponse] = await Promise.all([
      fetch("/memory/summary"), fetch(`/memory/facts?${params}`),
    ]);
    if (!summaryResponse.ok || !factsResponse.ok) throw new Error(`HTTP ${summaryResponse.ok ? factsResponse.status : summaryResponse.status}`);
    renderMemorySummary(await summaryResponse.json());
    renderMemoryFacts((await factsResponse.json()).facts || []);
    $("#memoryEmpty").textContent = "当前筛选条件下没有长期记忆。";
  } catch (error) {
    $("#memoryEmpty").textContent = `记忆加载失败：${error.message}`;
    $("#memoryEmpty").classList.remove("hidden");
  }
}

function openMemoryEditor(fact = null) {
  state.editingMemoryFactId = fact?.fact_id || null;
  $("#memoryFactId").value = state.editingMemoryFactId || "";
  $("#memoryFactContent").value = fact?.content || "";
  $("#memoryFactCategory").value = fact?.category || "preference";
  $("#memoryFactConfidence").value = fact?.confidence ?? 1;
  const expiry = fact?.expected_valid_until ? new Date(fact.expected_valid_until) : null;
  $("#memoryFactExpiry").value = expiry && !Number.isNaN(expiry.getTime())
    ? new Date(expiry.getTime() - expiry.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
  $("#memoryFormError").classList.add("hidden");
  $("#memoryFactForm").classList.remove("hidden");
  $("#memoryFactContent").focus();
}

function closeMemoryEditor() {
  state.editingMemoryFactId = null;
  $("#memoryFactForm").reset();
  $("#memoryFactConfidence").value = "1";
  $("#memoryFactForm").classList.add("hidden");
  $("#memoryFormError").classList.add("hidden");
}

async function saveMemoryFact(event) {
  event.preventDefault();
  const factId = state.editingMemoryFactId;
  const expiry = $("#memoryFactExpiry").value;
  const body = {
    content: $("#memoryFactContent").value.trim(),
    category: $("#memoryFactCategory").value,
    confidence: Number($("#memoryFactConfidence").value),
    expected_valid_until: expiry ? new Date(expiry).toISOString() : null,
  };
  if (factId) {
    const current = state.memoryFacts.find(fact => fact.fact_id === factId);
    body.expected_revision = Number(current?.revision || 1);
  }
  const button = $("#saveMemoryFact"); button.disabled = true;
  try {
    const response = await fetch(factId ? `/memory/facts/${encodeURIComponent(factId)}` : "/memory/facts", {
      method: factId ? "PATCH" : "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    closeMemoryEditor(); await loadMemoryManager();
  } catch (error) {
    $("#memoryFormError").textContent = error.message;
    $("#memoryFormError").classList.remove("hidden");
  } finally { button.disabled = false; }
}

async function reviewMemoryFact(fact, action) {
  const label = action === "renew" ? "续期 90 天" : "归档";
  if (!window.confirm(`确认将这条记忆${label}吗？`)) return;
  const response = await fetch(`/memory/facts/${encodeURIComponent(fact.fact_id)}/review`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, expected_revision: Number(fact.revision), review_days: 90}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) { alert(`复核失败：${payload.detail || `HTTP ${response.status}`}`); return; }
  await loadMemoryManager();
}

async function deleteMemoryFact(fact) {
  if (!window.confirm(`确认删除这条${memoryCategoryLabels[fact.category] || ""}记忆吗？`)) return;
  const response = await fetch(`/memory/facts/${encodeURIComponent(fact.fact_id)}?expected_revision=${encodeURIComponent(fact.revision)}`, {method: "DELETE"});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) { alert(`删除失败：${payload.detail || `HTTP ${response.status}`}`); return; }
  if (state.editingMemoryFactId === fact.fact_id) closeMemoryEditor();
  await loadMemoryManager();
}

async function exportMemory() {
  const response = await fetch("/memory/export");
  if (!response.ok) { alert(`导出失败：HTTP ${response.status}`); return; }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url;
  link.download = `user-memory-${state.currentUser?.user_id || "export"}.json`;
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}

async function clearAllPersonalMemory() {
  if (!window.confirm("此操作会清除当前用户的长期事实、全部会话记忆、私有查询经验和待处理任务。确认继续吗？")) return;
  const confirmation = window.prompt("请输入 DELETE_ALL_PERSONAL_MEMORY 完成二次确认：", "");
  if (confirmation !== "DELETE_ALL_PERSONAL_MEMORY") { alert("确认文本不匹配，未执行清除。"); return; }
  const response = await fetch("/memory", {
    method: "DELETE", headers: {"Content-Type": "application/json"}, body: JSON.stringify({confirmation}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) { alert(`清除失败：${payload.detail || `HTTP ${response.status}`}`); return; }
  state.conversationId = null; state.memoryEntities = []; state.memoryTurnCount = 0;
  sessionStorage.removeItem(userStorageKey("conversationId"));
  renderConversationMemory(); closeMemoryEditor(); await loadMemoryManager();
  alert(`个人记忆已清除：长期事实 ${payload.deleted_facts || 0} 条，会话 ${payload.deleted_conversations || 0} 个。`);
}

async function handleResponse(response) {
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    if (response.status === 401) showLogin("登录已失效，请重新登录");
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  const payload = await response.json();
  state.threadId = payload.run_id || payload.thread_id || state.threadId;
  if (payload.conversation_id) {
    state.conversationId = payload.conversation_id;
    sessionStorage.setItem(userStorageKey("conversationId"), state.conversationId);
  }
  if (payload.status === "RUNNING") { setSubmitMode("running"); setRunStatus("执行中", true); startEventStream(); }
  else if (payload.status === "NEED_USER_SELECTION") { showCandidates(payload.interrupt); finishRun("等待实体选择"); }
  else { renderResult(payload); stopEventStream(); }
}

$("#queryForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.running) { await cancelCurrentAnalysis(); return; }
  resetUI(); state.threadId = newThreadId(); setSubmitMode("running");
  const button = $("#submitBtn"); button.disabled = true;
  try { const response = await fetch("/queries", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({question:$("#question").value.trim(), thread_id:state.threadId, max_replans:Number($("#maxReplans").value), web_search_enabled:state.webSearchEnabled, memory_enabled:state.memoryEnabled, conversation_id:state.memoryEnabled ? ensureConversationId() : null, experience_memory_enabled:state.experienceMemoryEnabled})}); await handleResponse(response); }
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
$("#experienceMemoryToggle").addEventListener("click", () => {
  if (state.running) return;
  state.experienceMemoryEnabled = !state.experienceMemoryEnabled;
  sessionStorage.setItem(userStorageKey("experienceMemoryEnabled"), String(state.experienceMemoryEnabled));
  renderExperienceMemoryToggle();
});
$("#conversationMemoryToggle").addEventListener("click", async () => {
  if (state.running) return;
  state.memoryEnabled = !state.memoryEnabled;
  sessionStorage.setItem(userStorageKey("memoryEnabled"), String(state.memoryEnabled));
  if (state.memoryEnabled) ensureConversationId();
  await refreshConversationMemory();
});
$("#clearConversationMemory").addEventListener("click", async () => {
  if (state.running || !state.conversationId) return;
  if (!window.confirm("确认清除当前对话的全部记忆吗？此操作不会删除知识图谱数据。")) return;
  const response = await fetch(`/conversations/${encodeURIComponent(state.conversationId)}/memory`, {method:"DELETE"});
  if (!response.ok) { const payload = await response.json().catch(() => ({})); alert(`清除失败：${payload.detail || `HTTP ${response.status}`}`); return; }
  state.conversationId = newConversationId();
  sessionStorage.setItem(userStorageKey("conversationId"), state.conversationId);
  state.memoryEntities = []; state.memoryTurnCount = 0;
  renderConversationMemory();
  alert("当前对话记忆已清空，后续问题将从新的会话开始。");
});
$("#copyState").addEventListener("click", async () => { await navigator.clipboard.writeText($("#stateJson").textContent); $("#copyState").textContent = "已复制"; setTimeout(() => $("#copyState").textContent = "复制", 1200); });
$("#closeEventModal").addEventListener("click", () => $("#eventModal").close());
$("#eventModal").addEventListener("click", event => { if (event.target === $("#eventModal")) $("#eventModal").close(); });
$("#refreshRuns").addEventListener("click", loadRunOptions);
$("#compareRuns").addEventListener("click", compareSelectedRuns);
$("#memoryManagerButton").addEventListener("click", async () => {
  $("#memoryManagerModal").showModal();
  await loadMemoryManager();
});
$("#closeMemoryManager").addEventListener("click", () => $("#memoryManagerModal").close());
$("#memoryManagerModal").addEventListener("click", event => { if (event.target === $("#memoryManagerModal")) $("#memoryManagerModal").close(); });
$("#searchMemoryFacts").addEventListener("click", loadMemoryManager);
$("#memorySearchInput").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); loadMemoryManager(); } });
$("#memoryCategoryFilter").addEventListener("change", loadMemoryManager);
$("#addMemoryFact").addEventListener("click", () => openMemoryEditor());
$("#cancelMemoryEdit").addEventListener("click", closeMemoryEditor);
$("#memoryFactForm").addEventListener("submit", saveMemoryFact);
$("#exportMemory").addEventListener("click", exportMemory);
$("#clearAllPersonalMemory").addEventListener("click", clearAllPersonalMemory);
$("#loginUser").addEventListener("change", prefillLoginPassword);

$("#loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#loginButton");
  button.disabled = true;
  $("#loginError").classList.add("hidden");
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user_id: $("#loginUser").value, password: $("#loginPassword").value}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    $("#loginPassword").value = "";
    await showApplication(payload.user);
  } catch (error) {
    showLogin(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#logoutButton").addEventListener("click", async () => {
  if (state.running && !window.confirm("当前分析仍在执行，确认退出登录吗？")) return;
  stopEventStream();
  try { await fetch("/auth/logout", {method: "POST"}); } catch (error) { console.warn("退出请求失败", error); }
  state.threadId = null;
  state.graphState = {};
  showLogin();
});

fetch("/health").then(response => response.json()).then(payload => { $("#healthDot").classList.add("online"); $("#healthText").textContent = `Stage ${payload.stage} · ${payload.embedding_provider} · ${payload.entity_backend}`; }).catch(() => { $("#healthText").textContent = "系统离线"; });
initializeAuth();
