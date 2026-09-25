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
  document.querySelectorAll(".prop input[name=pick]").forEach((cb) => (cb.checked = on));
  countPicked();
}

function countPicked() {
  const el = document.getElementById("picked-count");
  if (el) el.textContent = document.querySelectorAll(".prop input[name=pick]:checked").length;
}

document.addEventListener("DOMContentLoaded", countPicked);
document.addEventListener("htmx:afterSwap", countPicked);

// 離開前提醒未儲存的詞彙變更
window.addEventListener("beforeunload", (e) => {
  const el = document.getElementById("dirty");
  if (el && !el.hidden) e.preventDefault();
});
