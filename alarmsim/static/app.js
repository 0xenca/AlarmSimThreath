/* AlarmSim – utilidades comunes */
(function () {
  const AH = (window.AH = {});
  const csrf = document.querySelector('meta[name="csrf"]')?.content || "";
  const listeners = [];
  AH.onLive = (fn) => listeners.push(fn);
  AH.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  AH.post = async (url, body, method = "POST") => {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-CSRF": csrf }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    return j;
  };
  AH.get = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); };

  AH.toast = (msg, level) => {
    const box = document.getElementById("toasts"); if (!box) return;
    const el = document.createElement("div"); el.className = "toast " + (level || ""); el.textContent = msg;
    el.onclick = () => el.remove(); box.appendChild(el); setTimeout(() => el.remove(), 7000);
  };

  AH.renderResult = (el, r) => {
    const ok = r.result === "ACK";
    el.innerHTML = `<div class="result ${ok ? "ok" : "bad"}">
      <div><span class="res res-${AH.esc(r.result).replace("?", "")}">${AH.esc(r.result)}</span>
      ${r.latency_ms != null ? `<b>${r.latency_ms} ms</b>` : ""} · secuencia ${AH.esc(r.sequence)} ${r.encrypted ? "· 🔒 cifrado" : ""}
      ${r.attempts > 1 ? `· ${r.attempts} intentos` : ""}</div>
      ${r.error ? `<div class="danger-text small">${AH.esc(r.error)}</div>` : ""}
      ${r.response ? `<div class="small muted">Respuesta</div><pre class="frame">${AH.esc(r.response)}</pre>` : ""}
      ${r.details && r.details.decrypted ? `<div class="small muted">Respuesta descifrada</div><pre class="frame">${AH.esc(r.details.decrypted)}</pre>` : ""}
    </div>`;
  };

  AH.showMessage = async (id) => {
    const m = await AH.get("/api/history/" + id);
    document.getElementById("msg-body").textContent =
      `#${m.id}  ${m.created_at}  origen=${m.source}\nAbonado ${m.account} → ${m.target}\n${m.summary}\n\nEn claro:\n${m.plaintext}\n\nTrama:\n${m.frame}\n\nResultado: ${m.result} ${m.latency_ms ?? ""} ms (${m.attempts} intentos) ${m.error}\nRespuesta:\n${m.response}`;
    document.getElementById("msg-dlg").showModal();
  };

  // --- trabajos en segundo plano (escenarios / carga)
  let currentJob = null;
  const jobs = {};
  function drawJobs() {
    const box = document.getElementById("jobs"); if (!box) return;
    box.innerHTML = Object.values(jobs).sort((a, b) => (a.id < b.id ? 1 : -1)).slice(0, 15).map((j) => `
      <div class="job" onclick="AH.showJob('${j.id}')"><span><b>${AH.esc(j.name)}</b> <small class="muted">${j.id}</small><br>
      <small>${AH.esc(Object.entries(j.stats || {}).map(([k, v]) => k + "=" + v).join(" · "))}</small></span>
      <span><span class="st-${j.status}">${j.status}</span> ${j.status === "running" ? `<button class="btn small danger" onclick="event.stopPropagation();AH.stopJob('${j.id}')">■</button>` : ""}</span></div>`).join("") || '<p class="muted">Ninguna</p>';
  }
  AH.initJobs = (list) => { list.forEach((j) => (jobs[j.id] = j)); drawJobs(); if (list[0]) AH.showJob(list[0].id); };
  AH.showJob = async (id) => { currentJob = id; const j = await AH.get("/api/jobs/" + id); document.getElementById("job-log").textContent = j.log.join("\n"); };
  AH.stopJob = (id) => AH.post(`/api/jobs/${id}/stop`);
  AH.runScenario = async (id) => {
    try { const j = await AH.post(`/api/scenarios/${id}/run`); jobs[j.id] = j; drawJobs(); AH.showJob(j.id); }
    catch (e) { AH.toast(e.message, "danger"); }
  };
  AH.onLive((m) => {
    if (m.type !== "job") return;
    jobs[m.job.id] = m.job; drawJobs();
    const log = document.getElementById("job-log");
    if (log && m.line && (currentJob === m.job.id || !currentJob)) { currentJob = m.job.id; log.textContent += (log.textContent ? "\n" : "") + m.line; log.scrollTop = log.scrollHeight; }
  });

  // --- tiempo real
  if (document.getElementById("live-dot")) {
    const dot = document.getElementById("live-dot");
    const es = new EventSource("/api/stream");
    es.onopen = () => dot.classList.add("on");
    es.onerror = () => dot.classList.remove("on");
    es.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch (e) { return; } listeners.forEach((fn) => { try { fn(m); } catch (e) { console.error(e); } }); };
  }
})();
