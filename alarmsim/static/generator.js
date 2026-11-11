/* AlarmSim – compositor de alarmas */
(function () {
  const $ = (id) => document.getElementById(id);
  if (!$("panel")) return;
  let panels = {};
  let panel = null;

  const token = () => document.querySelector('input[name="token"]:checked').value;
  const restoreOf = (code) => {
    const map = { BA: "BR", FA: "FR", PA: "PR", HA: "HR", MA: "MR", TA: "TR", WA: "WR", GA: "GR", KA: "KR", ZA: "ZR", UA: "UR", QA: "QR", SA: "SR", DF: "DR", BV: "BR" };
    return map[code] || (code[1] === "A" ? code[0] + "R" : code);
  };

  // ------------------------------------------------------------ catálogo de códigos
  function fillCodes() {
    const list = $("codes-list");
    const src = token() === "ADM-CID" ? window.CID : window.SIA;
    list.innerHTML = Object.entries(src).map(([k, v]) => `<option value="${k}">${k} · ${AH.esc(v[0])} (${v[1]})</option>`).join("");
    describeCode();
  }
  function describeCode() {
    const src = token() === "ADM-CID" ? window.CID : window.SIA;
    const c = $("code").value.trim().toUpperCase();
    const v = src[c];
    $("code-desc").textContent = v ? `${v[0]} · ${v[1]} · la dirección es ${v[2] === "user" ? "un USUARIO" : v[2] === "zone" ? "una ZONA" : "opcional"}` : c ? "Código no catalogado (se enviará tal cual)" : "";
  }

  // ------------------------------------------------------------ panel
  async function loadPanels() {
    const list = await AH.get("/api/panels");
    panels = Object.fromEntries(list.map((p) => [String(p.id), p]));
    selectPanel();
  }
  function selectPanel() {
    panel = panels[$("panel").value];
    if (!panel) return;
    $("panel-info").innerHTML = `${AH.esc(panel.target || "⚠ sin receptora destino")} · ${panel.has_key ? "🔒 clave AES" : "sin clave"} (${panel.encryption}) · última secuencia ${String(panel.sequence).padStart(4, "0")}`;
    document.querySelector(`input[name="token"][value="${panel.protocol}"]`).checked = true;
    $("zones-list").innerHTML = panel.zones.map((z) => `<option value="${z.number}">${AH.esc(z.name)} (part. ${z.partition})</option>`).join("");
    $("parts-list").innerHTML = panel.partitions.map((x) => `<option value="${x.number}">${AH.esc(x.name)}</option>`).join("");
    $("users-list").innerHTML = panel.users.map((u) => `<option value="${u.number}">${AH.esc(u.name)}</option>`).join("");
    onToken();
    drawQuick();
    history.replaceState(null, "", "/?panel=" + panel.id);
  }
  function onToken() {
    const t = token();
    document.querySelectorAll(".cid-only").forEach((e) => (e.hidden = t !== "ADM-CID"));
    document.querySelectorAll(".sia-only").forEach((e) => (e.hidden = t !== "SIA-DCS"));
    $("event-fields").hidden = t === "NULL";
    if (t === "ADM-CID" && !/^[0-9A-F]{3}$/i.test($("code").value)) $("code").value = "130";
    if (t === "SIA-DCS" && !/^[A-Z]{2}$/i.test($("code").value)) $("code").value = "BA";
    fillCodes();
    schedulePreview();
  }

  // ------------------------------------------------------------ eventos adicionales (SIA)
  function addExtra(ev = {}) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input class="ex-code" list="codes-list" value="${AH.esc(ev.code || "BA")}" size="4"></td>
      <td><input class="ex-zone" list="zones-list" placeholder="zona" value="${AH.esc(ev.zone || "")}" size="4"></td>
      <td><input class="ex-part" list="parts-list" placeholder="part." value="${AH.esc(ev.partition || "")}" size="3"></td>
      <td><input class="ex-user" list="users-list" placeholder="usr" value="${AH.esc(ev.user || "")}" size="3"></td>
      <td><button type="button" class="btn small danger">✕</button></td>`;
    tr.querySelector("button").onclick = () => { tr.remove(); schedulePreview(); };
    tr.querySelectorAll("input").forEach((i) => (i.oninput = schedulePreview));
    $("extra-table").querySelector("tbody").appendChild(tr);
    schedulePreview();
  }

  // ------------------------------------------------------------ especificación
  function currentSpec() {
    const t = token();
    const spec = { token: t, auto_xdata: $("auto_xdata").checked, options: {} };
    if (t !== "NULL") {
      spec.code = $("code").value.trim().toUpperCase();
      spec.zone = $("zone").value.trim();
      spec.partition = $("partition").value.trim();
      spec.user = $("user").value.trim();
      if (t === "ADM-CID") spec.qualifier = $("qualifier").value;
      if (t === "SIA-DCS") {
        const mode = $("ti-mode").value;
        spec.time_ti = mode === "manual" ? $("ti").value.trim() : mode;
        spec.inline_text = $("inline_text").value;
        spec.extra_events = [...document.querySelectorAll("#extra-table tbody tr")].map((tr) => ({
          code: tr.querySelector(".ex-code").value.trim().toUpperCase(), zone: tr.querySelector(".ex-zone").value.trim(),
          partition: tr.querySelector(".ex-part").value.trim(), user: tr.querySelector(".ex-user").value.trim(),
        })).filter((e) => e.code);
      }
      spec.xdata = {};
      document.querySelectorAll(".xd").forEach((i) => { if (i.value.trim()) spec.xdata[i.dataset.k] = i.value.trim(); });
    }
    const o = spec.options;
    o.encryption = $("encryption").value;
    o.timestamp = $("timestamp").checked;
    o.timestamp_offset = parseInt($("timestamp_offset").value || "0", 10);
    o.account_in_data = $("account_in_data").checked;
    o.corrupt_crc = $("corrupt_crc").checked;
    o.corrupt_length = $("corrupt_length").checked;
    if ($("sequence").value) o.sequence = parseInt($("sequence").value, 10);
    return spec;
  }
  AH.currentSpec = currentSpec;

  function applySpec(spec) {
    if (spec.token) document.querySelector(`input[name="token"][value="${spec.token}"]`).checked = true;
    onToken();
    ["code", "zone", "partition", "user", "inline_text"].forEach((k) => { if (spec[k] !== undefined) $(k).value = spec[k]; });
    if (spec.qualifier) $("qualifier").value = spec.qualifier;
    document.querySelectorAll(".xd").forEach((i) => (i.value = (spec.xdata || {})[i.dataset.k] || ""));
    $("extra-table").querySelector("tbody").innerHTML = "";
    (spec.extra_events || []).forEach(addExtra);
    const o = spec.options || {};
    if (o.encryption) $("encryption").value = o.encryption;
    describeCode();
    schedulePreview();
  }

  // ------------------------------------------------------------ vista previa
  let pvTimer = null;
  function schedulePreview() { clearTimeout(pvTimer); pvTimer = setTimeout(preview, 250); }
  async function preview() {
    if (!panel) return;
    describeCode();
    const v = $("v-open"); if (v) v.href = document.querySelector('.xd[data-k="V"]').value || "#";
    try {
      const p = await AH.post("/api/preview", { panel_id: panel.id, spec: currentSpec() });
      $("summary").textContent = p.summary;
      $("pv-plain").textContent = p.plaintext;
      $("pv-frame").textContent = p.frame;
      $("pv-hex").textContent = p.hex;
      $("pv-meta").textContent = `· ${p.bytes} bytes · seq ${p.sequence} ${p.encrypted ? "· 🔒 AES" : "· en claro"}`;
      $("pv-error").hidden = true;
    } catch (e) {
      $("pv-error").hidden = false;
      $("pv-error").textContent = e.message;
    }
  }

  // ------------------------------------------------------------ envío
  async function send(spec, label) {
    if (!panel) return;
    const btn = $("send"); btn.disabled = true;
    try {
      const count = Math.max(1, parseInt($("count").value || "1", 10));
      const r = await AH.post("/api/send", { panel_id: panel.id, spec: spec || currentSpec(), count: spec ? 1 : count, interval: parseFloat($("interval").value || "0") });
      const last = r.results ? r.results[r.results.length - 1] : r;
      AH.renderResult($("result"), last);
      if (r.results) AH.toast(`${r.results.filter((x) => x.result === "ACK").length}/${r.results.length} ACK`, "ok");
      else if (label) AH.toast(`${label} → ${last.result}`, last.result === "ACK" ? "ok" : "danger");
      panel.sequence = parseInt(last.sequence || panel.sequence, 10);
      $("sequence").value = "";
      selectPanelInfoOnly();
    } catch (e) {
      AH.toast(e.message, "danger");
    } finally { btn.disabled = false; schedulePreview(); }
  }
  function selectPanelInfoOnly() {
    $("panel-info").innerHTML = $("panel-info").innerHTML.replace(/última secuencia \d+/, "última secuencia " + String(panel.sequence).padStart(4, "0"));
  }

  // ------------------------------------------------------------ acciones rápidas
  function quickBtn(label, spec, cls = "") {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn small " + cls; b.textContent = label;
    b.onclick = () => send({ auto_xdata: true, options: {}, ...spec }, label);
    b.oncontextmenu = (e) => { e.preventDefault(); applySpec(spec); };
    b.title = "Clic: enviar · Clic derecho: cargar en el formulario";
    return b;
  }
  function drawQuick() {
    const g = $("quick-global"); g.innerHTML = "";
    const cid = panel.protocol === "ADM-CID";
    const part = panel.partitions[0] ? String(panel.partitions[0].number) : "1";
    const usr = panel.users[0] ? String(panel.users[0].number) : "1";
    const S = (code, extra = {}) => cid ? null : { token: "SIA-DCS", code, partition: part, ...extra };
    const C = (q, code, extra = {}) => ({ token: "ADM-CID", qualifier: q, code, partition: part, zone: "0", ...extra });
    const actions = [
      ["🔒 Armar", cid ? C("R", "401", { zone: usr }) : S("CL", { user: usr })],
      ["🔓 Desarmar", cid ? C("E", "401", { zone: usr }) : S("OP", { user: usr })],
      ["🆘 Pánico", cid ? C("E", "120") : S("PA")],
      ["🔥 Incendio", cid ? C("E", "110") : S("FA")],
      ["⚡ Fallo 230V", cid ? C("E", "301") : S("AT")],
      ["⚡ Red OK", cid ? C("R", "301") : S("AR")],
      ["🔋 Batería baja", cid ? C("E", "302") : S("YT")],
      ["🧪 Test", cid ? C("E", "602") : S("RP")],
      ["🔗 NULL", { token: "NULL" }],
    ];
    actions.forEach(([l, s]) => g.appendChild(quickBtn(l, s, l.includes("Pánico") || l.includes("Incendio") ? "danger" : "")));
    const z = $("quick-zones"); z.innerHTML = panel.zones.length ? "" : '<span class="muted small">El panel no tiene zonas (añádalas en Paneles)</span>';
    panel.zones.forEach((zone) => {
      const d = document.createElement("div"); d.className = "zone";
      d.innerHTML = `<b title="${AH.esc(zone.name)}">${zone.number}</b><span class="small">${AH.esc(zone.name).slice(0, 18)}</span>`;
      const zp = String(zone.partition || part);
      if (cid) {
        d.appendChild(quickBtn("Alarma", C("E", zone.cid, { zone: String(zone.number), partition: zp }), "danger"));
        d.appendChild(quickBtn("Rest.", C("R", zone.cid, { zone: String(zone.number), partition: zp })));
      } else {
        d.appendChild(quickBtn(zone.code, S(zone.code, { zone: String(zone.number), partition: zp }), "danger"));
        d.appendChild(quickBtn(restoreOf(zone.code), S(restoreOf(zone.code), { zone: String(zone.number), partition: zp })));
        d.appendChild(quickBtn("TA", S("TA", { zone: String(zone.number), partition: zp })));
      }
      z.appendChild(d);
    });
  }

  // ------------------------------------------------------------ plantillas
  async function loadTemplates() {
    const list = await AH.get("/api/templates");
    $("tpl").innerHTML = '<option value="">Plantillas…</option>' + list.map((t) => `<option value='${AH.esc(JSON.stringify(t.spec))}'>${AH.esc(t.name)}</option>`).join("");
  }
  $("save-tpl").onclick = async () => {
    const name = prompt("Nombre de la plantilla:", $("summary").textContent);
    if (!name) return;
    await AH.post("/api/templates", { name, spec: currentSpec() });
    loadTemplates(); AH.toast("Plantilla guardada", "ok");
  };
  $("tpl").onchange = () => { if ($("tpl").value) applySpec(JSON.parse($("tpl").value)); $("tpl").value = ""; };

  // ------------------------------------------------------------ últimos envíos (tiempo real)
  AH.onLive((m) => {
    if (m.type !== "sent") return;
    const r = m.message;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="nowrap">${new Date(r.created_at).toLocaleTimeString()}</td><td>${AH.esc(r.account)}</td><td>${AH.esc(r.summary)}</td>
      <td><span class="res res-${AH.esc(r.result).replace("?", "")}">${AH.esc(r.result)}</span></td><td>${r.latency_ms ?? ""}</td>`;
    const tb = $("recent").querySelector("tbody"); tb.prepend(tr);
    while (tb.children.length > 15) tb.lastChild.remove();
  });

  // ------------------------------------------------------------ eventos del formulario
  $("panel").onchange = selectPanel;
  document.querySelectorAll('input[name="token"]').forEach((r) => (r.onchange = onToken));
  $("ti-mode").onchange = () => { $("ti-manual-wrap").hidden = $("ti-mode").value !== "manual"; schedulePreview(); };
  $("add-extra").onclick = () => addExtra();
  $("send").onclick = () => send();
  $("h-now") && ($("h-now").onclick = () => {
    const d = new Date(), p = (n) => String(n).padStart(2, "0");
    document.querySelector('.xd[data-k="H"]').value = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())},${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}-${d.getUTCFullYear()}`;
    schedulePreview();
  });
  document.querySelectorAll(".gen input, .gen select").forEach((el) => {
    if (el.id === "panel" || el.name === "token") return;
    el.addEventListener("input", schedulePreview); el.addEventListener("change", schedulePreview);
  });
  $("zone").addEventListener("change", () => {
    // al elegir una zona se propone su código y su partición
    const z = panel && panel.zones.find((x) => String(x.number) === $("zone").value.trim());
    if (!z) return;
    if (!$("partition").value) $("partition").value = z.partition;
    $("code").value = token() === "ADM-CID" ? z.cid : z.code;
    schedulePreview();
  });
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") send(); });

  loadPanels(); loadTemplates();
})();
