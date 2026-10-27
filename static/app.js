// 智能简历筛选系统 - 前端逻辑
const API = "/api/v1";
let libraryItems = [];
let lastScreening = null;

const $ = (id) => document.getElementById(id);

// 转义 HTML，防止 XSS
function escapeHtml(text) {
  if (text == null) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 简单 Markdown 渲染：标题、加粗、斜体、列表、代码块
function markdownToHtml(md) {
  if (!md) return "";
  const lines = md.split("\n");
  let html = "";
  let inList = false;
  let listType = null;

  const closeList = () => {
    if (inList) {
      html += listType === "ol" ? "</ol>" : "</ul>";
      inList = false;
      listType = null;
    }
  };

  const inline = (text) => {
    return escapeHtml(text)
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { closeList(); continue; }
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (headerMatch) {
      closeList();
      const level = Math.min(headerMatch[1].length + 2, 6);
      html += `<h${level}>${inline(headerMatch[2])}</h${level}>`;
      continue;
    }
    const ulMatch = trimmed.match(/^[-*]\s+(.*)$/);
    if (ulMatch) {
      if (!inList || listType !== "ul") { closeList(); html += "<ul>"; inList = true; listType = "ul"; }
      html += `<li>${inline(ulMatch[1])}</li>`;
      continue;
    }
    const olMatch = trimmed.match(/^\d+\.\s+(.*)$/);
    if (olMatch) {
      if (!inList || listType !== "ol") { closeList(); html += "<ol>"; inList = true; listType = "ol"; }
      html += `<li>${inline(olMatch[1])}</li>`;
      continue;
    }
    closeList();
    html += `<p>${inline(line)}</p>`;
  }

  closeList();
  return html;
}

// ---------------- 健康检查 ----------------
async function checkHealth() {
  const badge = $("health-badge");
  try {
    const res = await fetch(`${API}/health`);
    const data = await res.json();
    if (res.ok && data.status === "ok") {
      const configRes = await fetch(`${API}/deployment-status`); if (configRes.ok && !(await configRes.json()).configured) { badge.textContent = "待配置智谱密钥（运行 Configure.cmd）"; badge.className = "badge badge-unknown"; return; } badge.textContent = "正常";
      badge.className = "badge badge-ok";
    } else {
      throw new Error("异常");
    }
  } catch (e) {
    badge.textContent = "无法连接";
    badge.className = "badge badge-err";
  }
}

// ---------------- 上传简历 ----------------
async function uploadResumes() {
  const input = $("resume-files");
  const log = $("upload-log");
  const btn = $("upload-btn");
  const files = Array.from(input.files || []);
  if (files.length === 0) {
    log.innerHTML = '<span class="err">请先选择文件</span>';
    return;
  }
  btn.disabled = true;
  let ok = 0, fail = 0;
  log.textContent = `开始上传 ${files.length} 个文件…`;
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/resumes`, { method: "POST", body: fd });
      const data = await res.json();
      if (res.ok) {
        ok++;
        log.innerHTML += `\n<span class="ok">✓ ${escapeHtml(file.name)} 上传成功</span>`;
      } else {
        fail++;
        log.innerHTML += `\n<span class="err">✗ ${escapeHtml(file.name)}: ${escapeHtml(data.detail || "失败")}</span>`;
      }
    } catch (e) {
      fail++;
      log.innerHTML += `\n<span class="err">✗ ${escapeHtml(file.name)}: ${escapeHtml(e.message)}</span>`;
    }
  }
  log.innerHTML += `\n完成：成功 ${ok}，失败 ${fail}`;
  btn.disabled = false;
  input.value = "";
  updateSelectedFiles();
  loadResumeList();
}

// ---------------- 简历列表 ----------------
async function loadResumeList() {
  const list = $("resume-list");
  const count = $("resume-count");
  try {
    const res = await fetch(`${API}/resumes`);
    const data = await res.json();
    count.textContent = String(data.total);
    $("stat-resumes").textContent = String(data.total);
    libraryItems = data.resumes || [];
    renderLibrary();
    return;
  } catch (e) {
    list.innerHTML = `<li class="empty">加载失败：${escapeHtml(e.message)}</li>`;
  }
}

async function screeningFetch(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300000);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('等待超过五分钟，请稍后查看服务状态。不要连续重复点击，以免产生重复模型调用。');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------- 提交查询 + 获取结果 ----------------
let currentTask = null;
let pollingTimer = null;
let taskGeneration = 0;

function setTaskBusy(busy) {
  $("query-btn").disabled = busy;
  $("upload-btn").disabled = busy;
  $("query-btn").textContent = busy ? "后台任务处理中…" : "开始智能筛选 →";
}

function displayTask(job) {
  currentTask = job;
  $("task-progress").hidden = false;
  $("task-stage").textContent = job.stage;
  $("task-bar").value = job.progress || 0;
  $("task-elapsed").textContent = `${Math.round(job.elapsed_seconds || 0)} 秒`;
  const usage=job.usage || {};
  $("task-usage").textContent = `${usage.calls || 0} 次请求 · 已报告 ${(usage.input_tokens || 0)+(usage.output_tokens || 0)} Token · 参考费用 ¥${Number(usage.estimated_cost_cny || 0).toFixed(4)}${usage.unreported_calls ? "（有调用未返回用量）" : ""}`;
  $("retry-task").hidden = !["failed", "interrupted"].includes(job.status);
  if (["queued", "running"].includes(job.status)) {
    setTaskBusy(true);
    $("query-log").textContent = "任务在后台运行，刷新页面不会重复提交。";
    $("results").innerHTML = `<div class="spinner">${escapeHtml(job.stage)} · 已用时 ${Math.round(job.elapsed_seconds || 0)} 秒</div>`;
  } else {
    setTaskBusy(false);
    if (job.status === "completed" && job.result) {
      renderResults(job.result);
      if (job.reused) $("query-log").textContent = "已复用相同岗位和简历库的历史结果，本次没有重新调用模型。";
    } else {
      $("query-log").textContent = job.error || "任务未完成";
      $("results").innerHTML = `<div class="results-empty"><h3>任务记录已保存</h3><p>${escapeHtml(job.error || "请稍后重试")}</p></div>`;
    }
  }
}

async function followTask(id, generation) {
  clearTimeout(pollingTimer);
  if (generation !== taskGeneration) return;
  try {
    const response = await screeningFetch(`${API}/tasks/${encodeURIComponent(id)}`);
    const job=await response.json();
    if (response.status === 404) {
      localStorage.removeItem("shicai-active-task");
      $("query-log").textContent="这条任务记录不存在，请重新创建筛选任务。";
      setTaskBusy(false); return;
    }
    if (!response.ok) throw new Error(job.detail || "无法读取任务");
    if (generation !== taskGeneration) return;
    displayTask(job);
    if (["queued", "running"].includes(job.status)) {
      pollingTimer=setTimeout(() => followTask(id,generation),1500);
    } else { loadHistory(); }
  } catch (error) {
    if (generation !== taskGeneration) return;
    $("query-log").textContent = "暂时无法连接任务，5 秒后重试。任务记录保存在服务端。";
    pollingTimer=setTimeout(() => followTask(id,generation),5000);
  }
}

async function runQuery() {
  const text=$("query-text").value.trim();
  if (!text) { $("query-log").textContent="请输入岗位需求"; return; }
  if (currentTask && ["queued","running"].includes(currentTask.status)) return;
  clearTimeout(pollingTimer);
  const generation=++taskGeneration;
  setTaskBusy(true); $("query-log").textContent="正在创建后台任务…";
  try {
    const response=await screeningFetch(`${API}/tasks`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query_text:text})});
    const job=await response.json();
    if (!response.ok) throw new Error(job.detail || "创建任务失败");
    localStorage.setItem("shicai-active-task",job.id);
    displayTask(job); loadHistory();
    if (["queued","running"].includes(job.status)) followTask(job.id,generation);
  } catch(error) { $("query-log").textContent=error.message; setTaskBusy(false); }
}

async function selectHistory(id) {
  clearTimeout(pollingTimer);
  const generation=++taskGeneration;
  localStorage.setItem("shicai-active-task",id);
  await followTask(id,generation);
  if (currentTask?.text) { $("query-text").value=currentTask.text; updateCharCount(); }
}

async function loadHistory() {
  try {
    const [historyRes,usageRes]=await Promise.all([fetch(`${API}/history`),fetch(`${API}/usage/summary`)]);
    if (!historyRes.ok || !usageRes.ok) throw new Error();
    const data=await historyRes.json(), usage=await usageRes.json();
    $("usage-calls").textContent=String(usage.calls);
    $("usage-tokens").textContent=Number(usage.input_tokens+usage.output_tokens).toLocaleString();
    $("usage-cost").textContent=Number(usage.estimated_cost_cny).toFixed(4);
    $("usage-note").textContent=usage.note+(usage.unreported_calls ? ` ${usage.unreported_calls} 次调用未返回用量，费用可能不完整。` : "");
    const statusLabels={queued:"排队中",running:"处理中",completed:"已完成",failed:"失败",interrupted:"已中断"};
    $("history-list").innerHTML=data.tasks.length ? data.tasks.map(job => `<button class="history-item" data-job="${escapeHtml(job.id)}"><span class="history-title">${escapeHtml(job.text)}</span><span class="history-status ${escapeHtml(job.status)}">${escapeHtml(statusLabels[job.status] || job.status)}</span><span class="history-time">${escapeHtml(new Date(job.created_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}))}</span><span class="history-open">查看 ↗</span></button>`).join("") : '<p class="empty">还没有筛选记录。完成一次筛选后，可在这里随时查看。</p>';
  } catch(error) { $("history-list").innerHTML='<p class="empty">暂时无法读取历史记录，请点击刷新。</p>'; }
}

function renderResults(data) {
  lastScreening = data;
  $("stat-results").textContent = String((data.candidates || []).length);
  $("match-caption").textContent = "本次筛选已完成";
  $("result-meta").textContent = `共 ${(data.candidates || []).length} 位候选人${data.screening_note ? " · 宽松匹配" : ""}`;
  $("query-log").innerHTML = '<span class="ok">✓ 筛选完成，结果已更新</span>';
  $("result-toolbar").hidden = !(data.candidates || []).length;
  $("export-btn").disabled = !(data.candidates || []).length;
  $("screening-summary").textContent=data.screening_note || "历史筛选结果，按当时规则生成。";
  drawCandidates();
}

function drawCandidates() {
  const term = $("result-search").value.trim().toLowerCase();
  const candidates = [...(lastScreening?.candidates || [])].filter(c =>
    [c.name, ...(c.skills || [])].join(" ").toLowerCase().includes(term));
  candidates.sort($("result-sort").value === "name" ?
    (a, b) => String(a.name || "").localeCompare(String(b.name || ""), "zh-CN") :
    (a, b) => (b.overall_score || 0) - (a.overall_score || 0));
  if (!candidates.length) {
    $("results").innerHTML = '<div class="results-empty"><div class="empty-illustration">⌕</div><h3>暂时没有找到匹配的候选人</h3><p>可以尝试调整搜索词、岗位条件，或补充更多简历。</p></div>';
    return;
  }
  $("results").innerHTML = candidates.map(c => {
    const value = typeof c.overall_score === "number" && Number.isFinite(c.overall_score) ? Math.max(0, Math.min(100, Math.round(c.overall_score * 100))) : null;
    const name = c.name || "未命名候选人";
    const skills = (c.skills || []).map(skill => `<span class="tag">${escapeHtml(skill)}</span>`).join("");
    const contact = [c.email, c.phone].filter(Boolean).map(escapeHtml).join(" · ");
    const locations = (c.preferred_locations || []).map(escapeHtml).join(" / ");
    return `<article class="candidate"><div class="candidate-head"><div class="candidate-person"><span class="candidate-avatar">${escapeHtml(name.charAt(0))}</span><div><span class="name">${escapeHtml(name)}</span><span class="rank">候选人 #${escapeHtml(c.rank || "—")} · 待人工复核</span></div></div><div class="score">${value === null ? "—" : value}<small>${value === null ? "" : "%"}</small><div class="score-label">规则匹配分数</div></div></div><div class="score-track"><i style="width:${value || 0}%"></i></div><div class="meta">${contact || "未提供联系方式"}${locations ? "<br>期望地点：" + locations : ""}${c.expected_salary ? "<br>期望薪资：" + escapeHtml(c.expected_salary) : ""}</div>${skills ? `<div class="skills">${skills}</div>` : ""}${(c.match_notes || []).length ? `<div class="match-notes"><strong>条件待核实</strong><ul>${c.match_notes.map(n=>`<li>${escapeHtml(n)}</li>`).join("")}</ul></div>` : ""}<details open><summary>AI 详细匹配分析（点击收起 / 展开）</summary><div class="analysis">${c.analysis ? markdownToHtml(c.analysis) : "暂无分析报告"}</div></details></article>`;
  }).join("");
}

function renderLibrary() {
  const term = $("library-search").value.trim().toLowerCase();
  const items = libraryItems.filter(r => [r.name, r.filename].join(" ").toLowerCase().includes(term));
  $("resume-list").innerHTML = items.length ? items.map(r => {
    const extension = String(r.filename || "TXT").split(".").pop().toUpperCase().slice(0, 4);
    return `<li><span class="file-icon">${escapeHtml(extension)}</span><div class="resume-info"><div class="fn">${escapeHtml(r.name || "未命名候选人")}</div><div class="rid" title="${escapeHtml(r.filename || "")}">${escapeHtml(r.filename || "")}</div></div><div class="resume-actions"><button class="view-resume" data-view="${escapeHtml(r.resume_id)}" aria-label="查看 ${escapeHtml(r.filename || "简历")}">查看</button><a class="download-resume" href="${API}/resumes/${encodeURIComponent(r.resume_id)}/pdf?download=true" title="下载 PDF：原件或文字重建版">PDF</a><button class="delete-resume" data-delete="${escapeHtml(r.resume_id)}" aria-label="删除 ${escapeHtml(r.filename || r.name || "简历")}">删除</button></div></li>`;
  }).join("") : `<li class="empty">${libraryItems.length ? "没有符合搜索条件的简历" : "简历库还是空的，上传第一份简历吧"}</li>`;
}

// ---------------- 事件绑定 ----------------
$("upload-btn").addEventListener("click", uploadResumes);
$("refresh-btn").addEventListener("click", loadResumeList);
$("query-btn").addEventListener("click", runQuery);

const templates = {
  backend: "招聘一位有 3 年以上经验的 Python 后端工程师，熟悉 FastAPI、SQL 和接口开发，本科及以上学历，可在北京工作。有 Docker 和分布式系统实践经验优先。",
  product: "寻找一位 AI 产品经理，有 2 年以上产品经验，熟悉大模型应用、需求分析和产品原型设计。有 RAG 或 Agent 项目落地经验优先。",
  graduate: "招聘应届毕业生，计算机相关专业本科及以上，具备 Python 编程能力，有课程项目或实习经历。重视学习能力和清晰的沟通表达。"
};
function updateCharCount() { $("char-count").textContent = `${$("query-text").value.length} 字`; }
function updateSelectedFiles() {
  const files = Array.from($("resume-files").files || []);
  $("selected-files").textContent = files.length ? `已选择 ${files.length} 份 · ` + files.map(f => f.name).join("、") : "尚未选择文件";
}
$("resume-files").addEventListener("change", updateSelectedFiles);
$("query-text").addEventListener("input", updateCharCount);
$("clear-query").addEventListener("click", () => { $("query-text").value = ""; updateCharCount(); document.querySelectorAll(".chip").forEach(c => c.classList.remove("selected")); $("query-text").focus(); });
document.querySelectorAll("[data-template]").forEach(chip => chip.addEventListener("click", () => {
  $("query-text").value = templates[chip.dataset.template]; updateCharCount();
  document.querySelectorAll(".chip").forEach(c => c.classList.toggle("selected", c === chip));
  $("query-text").focus();
}));
$("library-search").addEventListener("input", renderLibrary);
$("result-search").addEventListener("input", drawCandidates);
$("result-sort").addEventListener("change", drawCandidates);
$("export-btn").addEventListener("click", () => {
  if (!lastScreening) return;
  const blob = new Blob([JSON.stringify(lastScreening, null, 2)], {type: "application/json;charset=utf-8"});
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = `拾才-匹配结果-${new Date().toISOString().slice(0,10)}.json`;
  document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("guide-btn").addEventListener("click", () => $("guide-dialog").showModal());
[$("close-guide"), $("guide-done")].forEach(b => b.addEventListener("click", () => $("guide-dialog").close()));
const dropzone = $("dropzone");
["dragenter", "dragover"].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add("dragging"); }));
["dragleave", "drop"].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
dropzone.addEventListener("drop", event => {
  if (event.dataTransfer?.files.length) { $("resume-files").files = event.dataTransfer.files; updateSelectedFiles(); }
});
$("today").textContent = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "short"}).format(new Date());
checkHealth();
loadResumeList();
setInterval(checkHealth, 30000);

$("refresh-history").addEventListener("click",loadHistory);
$("history-list").addEventListener("click",event => {
  const item=event.target.closest("[data-job]");
  if (item) selectHistory(item.dataset.job);
});
$("retry-task").addEventListener("click",async () => {
  if (!currentTask) return;
  $("retry-task").disabled=true;
  try {
    const response=await screeningFetch(`${API}/tasks/${encodeURIComponent(currentTask.id)}/retry`,{method:"POST"});
    const job=await response.json();
    if (!response.ok) throw new Error(job.detail || "无法重试");
    await selectHistory(job.id);
  } catch(error) { $("query-log").textContent=error.message; }
  finally { $("retry-task").disabled=false; }
});
loadHistory();
const savedTask=localStorage.getItem("shicai-active-task");
if (savedTask) selectHistory(savedTask);

async function loadTrash() {
  try {
    const response=await fetch(`${API}/trash`);
    if (!response.ok) throw new Error();
    const data=await response.json();
    $("trash-count").textContent=String(data.resumes.length);
    $("trash-list").innerHTML=data.resumes.length ? data.resumes.map(r=>`<li><div class="resume-info"><div class="fn">${escapeHtml(r.name || r.filename)}</div><div class="rid">${escapeHtml(r.filename)}</div></div><div class="resume-actions"><button class="text-btn" data-restore="${escapeHtml(r.resume_id)}">恢复</button><button class="delete-resume" data-purge="${escapeHtml(r.resume_id)}">永久删除</button></div></li>`).join("") : '<li class="empty">回收站为空</li>';
  } catch(error) { $("trash-list").innerHTML='<li class="empty">回收站暂时无法加载</li>'; }
}
let pendingDeleteId=null;
$("resume-list").addEventListener("click",event=>{
  const button=event.target.closest("[data-delete]");
  if (!button) return;
  const item=libraryItems.find(r=>r.resume_id===button.dataset.delete);
  pendingDeleteId=button.dataset.delete;
  $("delete-filename").textContent=item?.filename || "这份简历";
  $("delete-dialog").showModal();
});
$("cancel-delete").addEventListener("click",()=>$("delete-dialog").close());
$("delete-dialog").addEventListener("close",()=>{pendingDeleteId=null;});
$("confirm-delete").addEventListener("click",async()=>{
  const id=pendingDeleteId;
  if (!id) return;
  $("confirm-delete").disabled=true;
  $("delete-dialog").close();
  try {
    const response=await fetch(`${API}/resumes/${encodeURIComponent(id)}`,{method:"DELETE"});
    const data=await response.json();
    if (!response.ok) throw new Error(data.detail || "删除失败");
    $("upload-log").textContent=data.message;
    await loadResumeList(); await loadTrash();
  } catch(error) { $("upload-log").textContent=error.message; }
  finally { $("confirm-delete").disabled=false; }
});
$("trash-list").addEventListener("click",async event=>{
  const button=event.target.closest("[data-restore]");
  if (!button) return;
  button.disabled=true;
  try {
    const response=await fetch(`${API}/trash/${encodeURIComponent(button.dataset.restore)}/restore`,{method:"POST"});
    const data=await response.json();
    if (!response.ok) throw new Error(data.detail || "恢复失败");
    $("upload-log").textContent=data.message;
    await loadResumeList(); await loadTrash();
  } catch(error) { $("upload-log").textContent=error.message; button.disabled=false; }
});
loadTrash();

let viewerGeneration=0;
function showTextView() {
  $("viewer-text").hidden=false; $("viewer-frame").hidden=true;
  $("view-text").classList.add("selected"); $("view-original").classList.remove("selected");
}
$("resume-list").addEventListener("click",async event=>{
  const button=event.target.closest("[data-view]");
  if (!button) return;
  const generation=++viewerGeneration;
  $("viewer-title").textContent="正在打开简历…";
  $("viewer-note").textContent="正在读取本地资料，不会调用模型。";
  $("viewer-text").textContent=""; $("view-original").hidden=true;
  $("viewer-download").hidden=true; showTextView();
  $("resume-viewer").showModal();
  try {
    const response=await fetch(`${API}/resumes/${encodeURIComponent(button.dataset.view)}/preview`);
    const data=await response.json();
    if (generation!==viewerGeneration) return;
    if (!response.ok) throw new Error(data.detail || "无法打开简历");
    $("viewer-title").textContent=data.filename;
    $("viewer-note").textContent=data.note;
    $("viewer-text").textContent=data.text || "暂无可显示的文字";
    $("view-original").hidden=false; $("view-original").textContent=data.is_pdf ? "原始 PDF" : "PDF 文字重建版";
    $("viewer-frame").dataset.source=`${API}/resumes/${encodeURIComponent(data.resume_id)}/pdf?download=false`;
    $("viewer-download").href=`${API}/resumes/${encodeURIComponent(data.resume_id)}/pdf?download=true`;
    $("viewer-download").textContent=data.is_pdf ? "↓ 下载 PDF 原件" : "↓ 下载 PDF（文字重建版）";
    $("viewer-download").hidden=false;
  } catch(error) { $("viewer-note").textContent=error.message; }
});
$("view-text").addEventListener("click",showTextView);
$("view-original").addEventListener("click",()=>{
  $("viewer-frame").src=$("viewer-frame").dataset.source;
  $("viewer-text").hidden=true; $("viewer-frame").hidden=false;
  $("view-original").classList.add("selected"); $("view-text").classList.remove("selected");
});
$("close-viewer").addEventListener("click",()=>$("resume-viewer").close());
$("resume-viewer").addEventListener("close",()=>{viewerGeneration++;$("viewer-frame").removeAttribute("src");$("viewer-text").textContent="";});

fetch(`${API}/deployment-status`).then(r=>{if(!r.ok)throw new Error();return r.json();}).then(d=>{
$("model-llm").textContent=d.llm_model || '未配置';
$("model-embedding").textContent=d.embedding_model || '未配置';
}).catch(()=>{$("model-llm").textContent='暂时无法读取配置';$("model-embedding").textContent='暂时无法读取配置';});

let pendingPurgeId=null;
$("trash-list").addEventListener("click",event=>{
  const button=event.target.closest("[data-purge]");
  if(!button)return;
  pendingPurgeId=button.dataset.purge;
  $("purge-filename").textContent=button.closest("li").querySelector(".rid").textContent;
  $("purge-dialog").showModal();
});
$("cancel-purge").addEventListener("click",()=>$("purge-dialog").close());
$("purge-dialog").addEventListener("close",()=>{pendingPurgeId=null;});
$("confirm-purge").addEventListener("click",async()=>{
  const id=pendingPurgeId;if(!id)return;
  $("confirm-purge").disabled=true;$("cancel-purge").disabled=true;
  try{
    const response=await fetch(`${API}/trash/${encodeURIComponent(id)}`,{method:"DELETE"});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "永久删除失败");
    $("purge-dialog").close();$("upload-log").textContent=data.message;
    await loadTrash();
  }catch(error){$("purge-error").textContent=error.message;}
  finally{$("confirm-purge").disabled=false;$("cancel-purge").disabled=false;}
});
$("purge-dialog").addEventListener("close",()=>{$("purge-error").textContent="";});
