/* AlarmHub – tiempo real (SSE), avisos y utilidades */
(function () {
  const AH = (window.AH = {});
  const listeners = [];
  AH.onLive = (fn) => listeners.push(fn);
  let reloadTimer = null;
  AH.debouncedReload = () => {
    if (document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => location.reload(), 1200);
  };
  const csrf = document.querySelector('meta[name="csrf"]')?.content;

  // --- sonido
  const soundKey = "ah_sound";
  const toggle = document.getElementById("sound-toggle");
  const soundEnabled = () => document.body.dataset.sound === "1" && localStorage.getItem(soundKey) !== "0";
  if (toggle) {
    toggle.checked = soundEnabled();
    toggle.onchange = () => localStorage.setItem(soundKey, toggle.checked ? "1" : "0");
  }
  let audioCtx = null;
  function beep(prio) {
    if (!soundEnabled()) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const times = prio <= 1 ? 3 : 1;
      for (let i = 0; i < times; i++) {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = prio <= 1 ? 1046 : 784;
        o.connect(g); g.connect(audioCtx.destination);
        const t = audioCtx.currentTime + i * 0.35;
        g.gain.setValueAtTime(0.15, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.3);
        o.start(t); o.stop(t + 0.3);
      }
    } catch (e) {}
  }

  // --- toasts
  AH.toast = (msg, level, href) => {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast " + (level || "");
    el.textContent = msg;
    el.onclick = () => (href ? (location = href) : el.remove());
    box.appendChild(el);
    setTimeout(() => el.remove(), 10000);
  };

  // --- SSE
  function connect() {
    if (!document.getElementById("live-dot")) return;
    const es = new EventSource("/stream");
    const dot = document.getElementById("live-dot");
    es.onopen = () => dot.classList.add("on");
    es.onerror = () => dot.classList.remove("on");
    es.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "alarm" && m.alarm && m.alarm.status === "new" && ["event", "created", "released"].includes(m.action)) {
        if (m.action !== "event" || m.alarm.event_count === 1) {
          AH.toast(`🚨 P${m.alarm.priority} ${m.alarm.title} – ${m.alarm.client || m.alarm.account}`,
            m.alarm.priority <= 2 ? "danger" : "warning", "/alarms/" + m.alarm.id);
          beep(m.alarm.priority);
        }
      }
      if (m.type === "toast") { AH.toast(m.message, m.level, m.alarm_id ? "/alarms/" + m.alarm_id : null); beep(m.level === "danger" ? 1 : 3); }
      listeners.forEach((fn) => { try { fn(m); } catch (e) {} });
    };
  }
  connect();

  // --- workflows
  AH.formatJSON = (id) => {
    const ta = document.getElementById(id);
    try { ta.value = JSON.stringify(JSON.parse(ta.value), null, 2); setStatus("JSON válido", true); }
    catch (e) { setStatus("JSON inválido: " + e.message, false); }
  };
  function setStatus(t, ok) {
    const s = document.getElementById("json-status");
    if (s) { s.textContent = t; s.style.color = ok ? "var(--ok)" : "var(--danger)"; }
  }
  AH.insertStep = (step) => {
    const ta = document.getElementById("definition");
    try {
      const d = JSON.parse(ta.value || "{}");
      d.steps = d.steps || [];
      d.steps.push(step);
      ta.value = JSON.stringify(d, null, 2);
      setStatus("Paso añadido", true);
    } catch (e) { setStatus("Corrige el JSON antes de insertar: " + e.message, false); }
  };
  AH.testTrigger = async () => {
    const body = new URLSearchParams({ csrf, definition: document.getElementById("definition").value,
      sample: document.getElementById("sample").value });
    const r = await fetch("/workflows/test-trigger", { method: "POST", body });
    const j = await r.json();
    const out = document.getElementById("test-result");
    if (j.error) out.textContent = "❌ " + j.error;
    else if (j.errors && j.errors.length) out.textContent = "❌ " + j.errors.join("; ");
    else out.textContent = j.matches ? "✅ El evento DISPARARÍA el workflow" : "⚪ El evento NO dispararía el workflow";
  };
  const def = document.getElementById("definition");
  if (def) def.addEventListener("input", () => { try { JSON.parse(def.value); setStatus("JSON válido", true); } catch (e) { setStatus("JSON inválido: " + e.message, false); } });
})();
