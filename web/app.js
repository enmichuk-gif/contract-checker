/* Прототип «Проверка условий договоров поставки».
   Vanilla JS без зависимостей. Все данные приходят с сервера (/api/analyze,
   /api/question); клиент не принимает продуктовых решений — только отображает
   значения, статусы и доказательства (файл, пункт, цитата, позиция в тексте). */
"use strict";

const state = {
  files: [],          // [{name, b64}]
  analysis: null,     // последний ответ /api/analyze
  expanded: new Set() // раскрытые ячейки: `${contractNo}|${fieldKey}`
};

/* ---------------- утилиты ---------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtRuDate(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
}

function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(bin);
}

async function postJSON(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

/* ---------------- загрузка файлов ---------------- */

function addFile(name, b64) {
  const existing = state.files.findIndex((f) => f.name === name);
  if (existing >= 0) state.files[existing] = { name, b64 };
  else state.files.push({ name, b64 });
  renderFiles();
}

function readFilesAsB64(fileList) {
  const readers = [...fileList].map((file) => new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve({ name: file.name, b64: String(fr.result).split(",")[1] || "" });
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(file);
  }));
  return Promise.all(readers);
}

function renderFiles() {
  const box = $("#fileList");
  box.innerHTML = state.files.map((f, i) =>
    `<span class="filechip">📄 ${esc(f.name)}
       <button class="x" type="button" data-rm="${i}" title="Убрать файл">✕</button>
     </span>`).join("");
  $("#analyzeBtn").disabled = state.files.length === 0;
}

$("#fileInput").addEventListener("change", async (e) => {
  const items = await readFilesAsB64(e.target.files);
  items.forEach((f) => addFile(f.name, f.b64));
  e.target.value = "";
});

const dz = $("#dropzone");
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", async (e) => {
  e.preventDefault();
  dz.classList.remove("dragover");
  const items = await readFilesAsB64(e.dataTransfer.files);
  items.forEach((f) => addFile(f.name, f.b64));
});

$("#fileList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-rm]");
  if (!btn) return;
  state.files.splice(Number(btn.dataset.rm), 1);
  renderFiles();
});

$("#sampleBtn").addEventListener("click", async () => {
  setStatus("Загружаю учебные документы…");
  try {
    const resp = await fetch("/api/sample-documents");
    const data = await resp.json();
    (data.documents || []).forEach((d) => addFile(d.name, b64encode(d.content)));
    setStatus(`Учебные документы добавлены (${(data.documents || []).length}). Нажмите «Проанализировать».`, true);
  } catch (err) {
    setStatus(`Не удалось загрузить учебные документы: ${err.message}`);
  }
});

$("#clearBtn").addEventListener("click", () => {
  state.files = [];
  state.analysis = null;
  state.expanded.clear();
  renderFiles();
  hideResults();
  setStatus("");
});

function setStatus(text, ok = false) {
  const el = $("#analyzeStatus");
  el.textContent = text;
  el.style.color = ok ? "var(--green)" : "var(--muted)";
}

/* ---------------- анализ ---------------- */

$("#analyzeBtn").addEventListener("click", analyze);
$("#asOf").addEventListener("change", () => {
  if (state.analysis && state.files.length) analyze(); // дата изменена — пересчитать
});

async function analyze() {
  if (!state.files.length) return;
  setStatus("Анализирую…");
  $("#analyzeBtn").disabled = true;
  try {
    const data = await postJSON("/api/analyze", {
      as_of: $("#asOf").value || undefined,
      files: state.files.map((f) => ({ name: f.name, content_b64: f.b64 }))
    });
    state.analysis = data;
    state.expanded.clear();
    renderAll();
    const n = (data.contracts || []).length;
    setStatus(`Готово: договоров — ${n}, документов — ${(data.documents || []).length}. Дата проверки ${fmtRuDate(data.as_of)}.`, true);
  } catch (err) {
    setStatus(`Ошибка: ${err.message}`);
  } finally {
    $("#analyzeBtn").disabled = state.files.length === 0;
  }
}

function hideResults() {
  ["resultsCard", "proposalsCard", "warningsCard", "questionsCard", "registryCard"].forEach((id) => {
    $("#" + id).hidden = true;
  });
}

function renderAll() {
  const a = state.analysis;
  if (!a) return hideResults();
  renderContracts(a);
  renderProposals(a);
  renderWarnings(a);
  renderQuestions(a);
  renderRegistry(a);
}

/* ---------------- таблица договоров ---------------- */

const DOC_TYPE_BADGE = {
  contract: ["b-ok", "договор"],
  amendment_signed: ["b-signed", "ДС подписано"],
  amendment_draft: ["b-warn", "проект ДС"],
  amendment_unsigned: ["b-warn", "ДС не подписано"],
  amendment_unknown: ["b-review", "ДС: статус?"],
  email: ["b-info", "письмо"],
  unknown: ["b-error", "не распознан"]
};

function fieldCellHtml(c, fk, fv) {
  const key = `${c.number}|${fk}`;
  let badge = "";
  if (fv.status === "changed") badge = `<span class="badge b-changed" title="Изменено: ${esc(fv.changed_by || "")}">изменено</span>`;
  else if (fv.status === "no_data") badge = `<span class="badge b-nodata">нет данных</span>`;
  else if (fv.status === "needs_review") badge = `<span class="badge b-review">требует проверки</span>`;
  const val = fv.status === "no_data"
    ? `<span class="valmain nodata">нет данных</span>`
    : `<span class="valmain">${esc(fv.display)}</span>`;
  return `<td class="valuecell" data-ct="${esc(c.number)}" data-fk="${esc(fk)}" title="Показать основания">
      ${val}<br>${badge}
    </td>`;
}

function renderContracts(a) {
  $("#resultsCard").hidden = false;
  $("#resultsSubhead").textContent = `— по состоянию на ${fmtRuDate(a.as_of)}`;
  const fields = a.fields_meta || [];
  const anyReview = (a.contracts || []).some((c) => c.needs_review);
  $("#reviewBanner").innerHTML = anyReview
    ? `<div class="banner">⚠ Есть ситуации, которые не удалось разрешить надёжно: они отмечены <span class="badge b-review">требует проверки</span> в таблице и описаны в разделе «Требует проверки».</div>`
    : "";

  const head = `<thead><tr>
      <th>Договор</th><th>Поставщик</th>
      ${fields.map((f) => `<th>${esc(f.title)}${f.unit ? ", " + esc(f.unit) : ""}</th>`).join("")}
      <th>Документы</th></tr></thead>`;

  const rows = (a.contracts || []).map((c) => {
    const docs = (c.documents || []).map((d) => {
      const [cls, title] = DOC_TYPE_BADGE[d.doc_type] || ["b-info", d.doc_type_title];
      const no = d.doc_type.startsWith("amendment") && d.number ? ` №${esc(d.number)}` : "";
      return `<div><span class="badge ${cls}">${esc(title)}${no}</span>
        <button class="filelink" data-file="${esc(d.file_name)}">${esc(d.file_name)}</button></div>`;
    }).join("");
    const main = `<tr>
      <td><span class="contrno">${esc(c.number)}</span><br>
        <span class="hint">от ${fmtRuDate(c.doc_date)}</span><br>
        <button class="filelink" data-file="${esc(c.file_name)}">${esc(c.file_name)}</button></td>
      <td>${c.supplier ? esc(c.supplier) : '<span class="valmain nodata">нет данных</span>'}</td>
      ${fields.map((f) => fieldCellHtml(c, f.key, c.fields[f.key])).join("")}
      <td class="docs">${docs}</td>
    </tr>`;
    const detailRows = fields.map((f) => {
      const key = `${c.number}|${f.key}`;
      if (!state.expanded.has(key)) return "";
      return `<tr class="detailrow"><td colspan="${fields.length + 3}">
        ${detailHtml(c, f.key, c.fields[f.key])}</td></tr>`;
    }).join("");
    return main + detailRows;
  }).join("");

  const empty = (a.contracts || []).length ? "" :
    `<tr class="emptyrow"><td colspan="${fields.length + 3}">Договоры не найдены. Загрузите TXT-файлы договоров.</td></tr>`;

  $("#contractsTable").innerHTML = head + `<tbody>${rows}${empty}</tbody>`;
}

function evidenceHtml(ev, extraClass = "") {
  if (!ev) return "";
  const clause = ev.clause ? `п. ${esc(ev.clause)} · ` : "";
  return `<div class="chainitem ${extraClass}">
    ${ev.label ? `<div class="chainnote">${esc(ev.label)}</div>` : ""}
    <blockquote class="quote">«${esc(ev.quote)}»
      <span class="qmeta">${clause}${esc(ev.file_name)}, строка ${ev.line}
        <button class="tiny ghost showtext" type="button"
          data-file="${esc(ev.file_name)}" data-start="${ev.char_start}" data-end="${ev.char_end}"
          data-line="${ev.line}">показать в тексте →</button>
      </span>
    </blockquote>
  </div>`;
}

function stepHtml(step, kind) {
  const badge = kind === "pending"
    ? `<span class="badge b-pending">не действует на дату проверки</span>`
    : (kind === "base" ? `<span class="badge b-ok">основание</span>` : `<span class="badge b-changed">действующее изменение</span>`);
  return `<div class="chainitem ${kind === "pending" ? "pending" : ""}">
    <div class="chainhead">${badge}
      <strong>${esc(step.display)}</strong>
      <span class="chainnote">${esc(step.source_file)}${step.clause ? " · п. " + esc(step.clause) : ""}${step.effective ? " · действует с " + fmtRuDate(step.effective) : ""}</span>
    </div>
    <div class="chainnote">${esc(step.note)}</div>
    ${evidenceHtml(step.evidence)}
  </div>`;
}

function detailHtml(c, fk, fv) {
  const parts = [];
  if (fv.review_reason) parts.push(`<div class="banner">⚠ ${esc(fv.review_reason)}</div>`);
  parts.push('<div class="chain">');
  if (fv.status === "no_data") {
    parts.push(`<div class="chainitem nodata">
      <div class="chainhead"><span class="badge b-nodata">нет данных</span>
      <span class="chainnote">${esc(fv.no_data_reason || "Значение не найдено")}</span></div>
      ${fv.no_data_evidence ? evidenceHtml(fv.no_data_evidence) : '<div class="chainnote">Цитата-основание отсутствует: подходящая формулировка в документах не найдена.</div>'}
    </div>`);
  }
  (fv.history || []).forEach((s, i) => {
    parts.push(stepHtml(s, i === fv.history.length - 1 && fv.changed_by ? "change" : "base"));
  });
  (fv.pending || []).forEach((s) => parts.push(stepHtml(s, "pending")));
  (c.proposals || []).filter((p) => p.field_key === fk).forEach((p) => {
    parts.push(`<div class="chainitem proposal">
      <div class="chainhead"><span class="badge b-warn">несогласованное предложение</span>
        <strong>${esc(p.proposed_display)}</strong>
        <span class="chainnote">сейчас действует: ${esc(p.current_display || "—")}</span></div>
      <div class="chainnote">${esc(p.source_file)} · ${esc(p.status_note)}</div>
      ${evidenceHtml(p.evidence)}
    </div>`);
  });
  parts.push("</div>");
  return parts.join("");
}

/* ---------------- предложения ---------------- */

function renderProposals(a) {
  const card = $("#proposalsCard");
  const list = a.proposals || [];
  card.hidden = list.length === 0;
  if (!list.length) return;
  $("#proposalsTable").innerHTML = `
    <thead><tr><th>Договор</th><th>Поле</th><th>Действует сейчас</th><th>Предлагается</th><th>Источник</th><th>Статус</th><th>Цитата</th></tr></thead>
    <tbody>${list.map((p) => `<tr>
      <td class="contrno">${esc(p.contract_no || "—")}</td>
      <td>${esc(p.field_title)}</td>
      <td>${esc(p.current_display || "—")}</td>
      <td><strong>${esc(p.proposed_display)}</strong></td>
      <td><button class="filelink" data-file="${esc(p.source_file)}">${esc(p.source_file)}</button></td>
      <td><span class="badge b-warn">${esc(p.status_note)}</span></td>
      <td><button class="tiny ghost showtext" type="button" data-file="${esc(p.evidence.file_name)}"
        data-start="${p.evidence.char_start}" data-end="${p.evidence.char_end}" data-line="${p.evidence.line}">показать →</button></td>
    </tr>`).join("")}</tbody>`;
}

/* ---------------- предупреждения ---------------- */

function renderWarnings(a) {
  const card = $("#warningsCard");
  const list = a.warnings || [];
  card.hidden = list.length === 0;
  if (!list.length) return;
  $("#warningsList").innerHTML = list.map((w) =>
    `<div class="warnitem ${w.level === "warn" ? "warn" : "info"}">
      ${w.level === "warn" ? "⚠" : "ℹ"} <span>${esc(w.text)}${w.file_name ? ` <button class="filelink" data-file="${esc(w.file_name)}">открыть</button>` : ""}</span>
    </div>`).join("");
}

/* ---------------- вопросы ---------------- */

const Q_LABELS = { q1: "В1", q2: "В2", q3: "В3" };

function renderQuestions(a) {
  const card = $("#questionsCard");
  const metas = a.questions_meta || [];
  card.hidden = metas.length === 0;
  if (!metas.length) return;
  $("#questionsGrid").innerHTML = metas.map((m) => {
    const params = (m.params || []).map((p) => {
      const id = `qp-${m.id}-${p.name}`;
      if (p.type === "contract") {
        return `<select id="${id}">${(p.options || []).map((o) =>
          `<option value="${esc(o.value)}"${o.value === p.default ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
      }
      return `<input type="date" id="${id}" value="${esc(p.default || "")}">`;
    }).join("");
    return `<div class="qcard" data-qid="${m.id}">
      <div class="qtitle">${esc(Q_LABELS[m.id] || m.id)} · ${esc(m.title)}</div>
      <div class="qparams">${params}<button class="primary tiny qask" type="button" data-qid="${m.id}">Спросить</button><button class="ghost tiny qclear" type="button" data-qid="${m.id}">Очистить</button></div>
      <div class="qanswer" id="qa-${m.id}"></div>
    </div>`;
  }).join("");
}

$("#questionsGrid").addEventListener("click", async (e) => {
  const btn = e.target.closest("button.qask");
  const clearBtn = e.target.closest("button.qclear");
  if (clearBtn) {
    const qid = clearBtn.dataset.qid;
    const box = $(`#qa-${qid}`);
    if (box) box.innerHTML = "";
    return;
  }
  if (!btn || !state.analysis) return;
  const qid = btn.dataset.qid;
  const meta = (state.analysis.questions_meta || []).find((m) => m.id === qid);
  if (!meta) return;
  const params = {};
  (meta.params || []).forEach((p) => {
    const el = $(`#qp-${qid}-${p.name}`);
    if (el) params[p.name] = el.value;
  });
  const box = $(`#qa-${qid}`);
  box.innerHTML = '<span class="hint">Формирую ответ…</span>';
  try {
    const ans = await postJSON("/api/question", {
      session_id: state.analysis.session_id, question_id: qid, params
    });
    renderAnswer(box, ans);
  } catch (err) {
    box.innerHTML = `<div class="banner">Ошибка: ${esc(err.message)}</div>`;
  }
});

function renderAnswer(box, ans) {
  const statusBadge = {
    ok: '<span class="badge b-ok">данные найдены</span>',
    changed: '<span class="badge b-changed">изменено допсоглашением</span>',
    no_data: '<span class="badge b-nodata">нет данных</span>',
    needs_review: '<span class="badge b-review">требует проверки</span>',
    error: '<span class="badge b-error">ошибка</span>'
  }[ans.status] || "";
  const parts = [`<div class="chainhead">${statusBadge}</div>`,
    `<div class="answer-text">${esc(ans.answer)}</div>`];
  if (ans.value_display && ans.status !== "no_data" && ans.status !== "error") {
    parts.push(`<div class="qvalue">${esc(ans.value_display)}</div>`);
  }
  if ((ans.history || []).length) {
    parts.push('<div class="chainnote" style="margin:6px 0 4px">Основания значения:</div><div class="chain">');
    ans.history.forEach((s, i) => {
      parts.push(stepHtml(s, i === ans.history.length - 1 && ans.history.length > 1 ? "change" : "base"));
    });
    parts.push("</div>");
  }
  if (ans.no_data_evidence) {
    parts.push('<div class="chainnote" style="margin:6px 0 4px">Почему нет данных:</div>');
    parts.push(evidenceHtml(ans.no_data_evidence, "nodata"));
  }
  if ((ans.pending || []).length) {
    parts.push('<div class="chainnote" style="margin:6px 0 4px">Подписано, но ещё не действует:</div><div class="chain">');
    ans.pending.forEach((s) => parts.push(stepHtml(s, "pending")));
    parts.push("</div>");
  }
  if ((ans.matched || []).length) {
    parts.push('<div class="chainnote" style="margin:6px 0 4px">Детали по договорам:</div><div class="chain">');
    ans.matched.forEach((m) => {
      const last = (m.history || [])[m.history.length - 1];
      parts.push(`<div class="chainitem">
        <div class="chainhead"><strong>${esc(m.contract_no)}</strong> · ${esc(m.value_display)}
        ${m.status === "needs_review" ? '<span class="badge b-review">требует проверки</span>' : ""}</div>
        ${last ? evidenceHtml(last.evidence) : ""}
        ${(m.proposals || []).map((p) => `<div class="chainitem proposal">
          <div class="chainhead"><span class="badge b-warn">несогласованное предложение</span>
            <strong>${esc(p.proposed_display)}</strong></div>
          <div class="chainnote">${esc(p.source_file)} · ${esc(p.status_note)}</div>
          ${evidenceHtml(p.evidence)}
        </div>`).join("")}
      </div>`);
    });
    parts.push("</div>");
  }
  if ((ans.notes || []).length) {
    parts.push('<ul class="answer-notes">' + ans.notes.map((n) => `<li>${esc(n)}</li>`).join("") + "</ul>");
  }
  box.innerHTML = parts.join("");
}

/* ---------------- реестр документов ---------------- */

function renderRegistry(a) {
  $("#registryCard").hidden = false;
  const rows = (a.documents || []).map((d) => `<tr>
    <td><button class="filelink" data-file="${esc(d.file_name)}">${esc(d.file_name)}</button></td>
    <td>${esc(d.doc_type_title)}</td>
    <td>${esc(d.number || "—")}</td>
    <td>${esc(d.contract_no || "—")}</td>
    <td>${fmtRuDate(d.doc_date)}</td>
    <td>${d.signed === true ? '<span class="badge b-ok">подписан</span>'
        : d.signed === false ? '<span class="badge b-warn">не подписан</span>'
        : '<span class="badge b-info">статус не определён</span>'}</td>
    <td>${d.effective_date ? "с " + fmtRuDate(d.effective_date) : "—"}</td>
    <td class="hint">${esc(d.encoding || "")}</td>
  </tr>`).join("");
  $("#registryTable").innerHTML = `
    <thead><tr><th>Файл</th><th>Тип</th><th>Номер</th><th>К договору</th><th>Дата</th><th>Подписание</th><th>Вступление в силу</th><th>Кодировка</th></tr></thead>
    <tbody>${rows}</tbody>`;
  const unl = a.unlinked_documents || [];
  $("#unlinkedBlock").innerHTML = unl.length
    ? `<p class="hint" style="margin-top:10px">Не связаны с договорами: ${unl.map((d) =>
        `<button class="filelink" data-file="${esc(d.file_name)}">${esc(d.file_name)}</button>`).join(", ")} — см. раздел «Требует проверки».</p>`
    : "";
}

/* ---------------- просмотрщик документа с подсветкой цитаты ---------------- */

function openViewer(fileName, start, end, lineNo) {
  const text = state.analysis && state.analysis.document_texts
    ? state.analysis.document_texts[fileName] : undefined;
  if (text == null) {
    alert("Текст документа недоступен. Выполните анализ заново.");
    return;
  }
  $("#viewerFile").textContent = fileName;
  $("#viewerMeta").textContent = lineNo ? `абзац/строка ${lineNo} — цитата подсвечена` : "полный текст";

  const body = $("#viewerBody");
  body.innerHTML = "";
  const hasRange = Number.isInteger(start) && Number.isInteger(end) && end > start;
  let pos = 0;
  let firstMark = null;
  text.split("\n").forEach((lineText, idx) => {
    const ls = pos;
    const le = pos + lineText.length;
    pos = le + 1;
    const div = document.createElement("div");
    div.className = "docline";
    const num = document.createElement("span");
    num.className = "lineno";
    num.textContent = String(idx + 1);
    div.appendChild(num);

    const o1 = Math.max(ls, start), o2 = Math.min(le, end);
    if (hasRange && o1 < o2) {
      div.classList.add("hitline");
      const before = document.createTextNode(lineText.slice(0, o1 - ls));
      const mark = document.createElement("mark");
      mark.className = "hit";
      mark.textContent = lineText.slice(o1 - ls, o2 - ls);
      const after = document.createTextNode(lineText.slice(o2 - ls));
      div.append(before, mark, after);
      if (!firstMark) firstMark = mark;
    } else {
      div.appendChild(document.createTextNode(lineText.length ? lineText : " "));
    }
    body.appendChild(div);
  });

  $("#viewerModal").hidden = false;
  document.body.style.overflow = "hidden";
  if (firstMark) {
    body.scrollTop = 0;
    requestAnimationFrame(() => firstMark.scrollIntoView({ block: "center" }));
  }
}

function closeViewer() {
  $("#viewerModal").hidden = true;
  document.body.style.overflow = "";
}

$("#viewerClose").addEventListener("click", closeViewer);
$("#viewerModal").addEventListener("click", (e) => {
  if (e.target === $("#viewerModal")) closeViewer();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#viewerModal").hidden) closeViewer();
});

/* ---------------- глобальная делегация событий ---------------- */

document.addEventListener("click", (e) => {
  const st = e.target.closest("button.showtext");
  if (st) {
    openViewer(st.dataset.file, Number(st.dataset.start), Number(st.dataset.end), st.dataset.line);
    return;
  }
  const fl = e.target.closest("button.filelink");
  if (fl) {
    openViewer(fl.dataset.file);
    return;
  }
  const cell = e.target.closest("td.valuecell");
  if (cell) {
    const key = `${cell.dataset.ct}|${cell.dataset.fk}`;
    if (state.expanded.has(key)) state.expanded.delete(key);
    else state.expanded.add(key);
    if (state.analysis) renderContracts(state.analysis);
  }
});

renderFiles();
