// 詞彙決策：把分組片段設為標準譯法／變體（只改表單，需按「儲存詞彙表」）
function setRole(event, role) {
  event.preventDefault();
  event.stopPropagation();
  const details = event.target.closest("details");
  const label = details.dataset.label;
  const ok = document.getElementById("f-ok");
  const vr = document.getElementById("f-var");
  const split = (el) => el.value.split("|").map((s) => s.trim()).filter(Boolean);
  let okList = split(ok).filter((x) => x !== label);
  let varList = split(vr).filter((x) => x !== label);
  if (role === "ok") okList.push(label);
  if (role === "var") varList.push(label);
  ok.value = okList.join("|");
  vr.value = varList.join("|");
  details.dataset.pending = role;
  markDirty();
}

function markDirty() {
  const el = document.getElementById("dirty");
  if (el) el.hidden = false;
}

function markActive(a) {
  document.querySelectorAll(".term-list li.active").forEach((li) => li.classList.remove("active"));
  a.closest("li").classList.add("active");
}

// 批次取代
function pickAll(on) {
  document.querySelectorAll(".prop:not([hidden]) input[name=pick]:not(:disabled)").forEach((cb) => (cb.checked = on));
  countPicked();
}

// 上傳頁：點 Weblate 狀態標籤只顯示並勾選該狀態的項目，再點一次恢復全部顯示
function filterState(btn) {
  const on = !btn.classList.contains("active");
  btn.parentElement.querySelectorAll("button.badge").forEach((b) => b.classList.toggle("active", b === btn && on));
  document.querySelectorAll(".prop input[name=pick]").forEach((cb) => {
    const match = cb.dataset.state === btn.dataset.state;
    cb.closest(".prop").hidden = on && !match;
    if (on) cb.checked = !cb.disabled && match;
  });
  countPicked();
}

function countPicked() {
  const el = document.getElementById("picked-count");
  if (el) el.textContent = document.querySelectorAll(".prop input[name=pick]:checked").length;
}

document.addEventListener("DOMContentLoaded", countPicked);
document.addEventListener("htmx:afterSwap", countPicked);

// 上傳確認：勾選項目在 Weblate 的檢閱狀態高於要上傳的狀態時（例如已核可 → 等候檢閱）特別警告
document.addEventListener("htmx:confirm", (e) => {
  if (e.detail.question !== "push") return;
  e.preventDefault();
  const form = e.detail.elt;
  const sel = form.querySelector("select[name=state]");
  const opt = sel.selectedOptions[0];
  const target = Number(opt.dataset.num);
  const picked = [...form.querySelectorAll("input[name=pick]:checked")];
  const labels = { 10: "需要編輯", 20: "等候檢閱", 30: "已核可" };
  const down = picked.filter((cb) => cb.dataset.state !== "" && Number(cb.dataset.state) > target);
  let msg = `用您的 Weblate 帳號上傳 ${picked.length} 條，檢閱狀態設為「${opt.textContent.trim()}」？`;
  if (down.length) {
    const byState = {};
    down.forEach((cb) => (byState[cb.dataset.state] = (byState[cb.dataset.state] || 0) + 1));
    const summary = Object.entries(byState).map(([s, n]) => `${labels[s] || s} ${n} 條`).join("、");
    const sample = down.slice(0, 5).map((cb) => "  • " + cb.dataset.key).join("\n");
    msg = `⚠ 降級警告：其中 ${down.length} 條在 Weblate 上的狀態較高（${summary}），` +
      `上傳後會變成「${opt.textContent.trim()}」。\n\n${sample}${down.length > 5 ? "\n  …" : ""}\n\n` +
      `若要維持原狀態，請取消並把檢閱狀態改為「已核可」。\n仍要上傳嗎？`;
  }
  if (confirm(msg)) e.detail.issueRequest(true);
});

// htmx 預設不顯示錯誤回應，改為跳出提示
document.addEventListener("htmx:responseError", (e) => {
  const xhr = e.detail.xhr;
  alert(`請求失敗（HTTP ${xhr.status}）：\n${xhr.responseText.slice(0, 500)}`);
});
document.addEventListener("htmx:sendError", () => alert("無法連線到伺服器，請確認 kc-tc serve 仍在執行。"));

// 離開前提醒未儲存的詞彙變更
window.addEventListener("beforeunload", (e) => {
  const el = document.getElementById("dirty");
  if (el && !el.hidden) e.preventDefault();
});
