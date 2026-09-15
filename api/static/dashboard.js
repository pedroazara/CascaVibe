"use strict";

const $ = (id) => document.getElementById(id);
const state = { data: null, traces: [], unit: "ms2", paused: false, busy: false,
  revision: 0, axes: [true, true, true], error: false, fetchedAt: 0, geometry: null,
  displayEnd: null, animationFrom: null, animationStart: 0, lastFrame: 0 };
const colors = ["#6bccaa", "#78acff", "#e5ad69"];
const number = (n, digits = 3) => n.toLocaleString("pt-BR", {maximumFractionDigits: digits, minimumFractionDigits: digits});
const date = (s) => new Date(s).toLocaleString("pt-BR", {day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit"});
const unit = () => state.unit === "g" ? "g" : "m/s²";

async function getJSON(path) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error(`Falha na consulta (${response.status})`);
  return response.json();
}

function setOptions(select, entries, selected) {
  const signature = JSON.stringify(entries);
  if (select.dataset.options !== signature) {
    select.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
    select.dataset.options = signature;
  }
  select.value = entries.some(([value]) => value === selected) ? selected : (entries[0]?.[0] ?? "");
}

function empty(title, message) {
  $("empty").hidden = false;
  $("empty").querySelector("h3").textContent = title;
  $("empty").querySelector("p").textContent = message;
}

function status() {
  const element = $("status");
  let text = "Aguardando coleta", kind = "";
  if (state.error) { text = "Falha na conexão"; kind = "error"; }
  else if (state.paused) text = "Pausado";
  else if (state.data) {
    const age = Date.parse(state.data.server_time) - Date.parse(state.data.last_received_at) + Date.now() - state.fetchedAt;
    if ($("session").value) { text = "Histórico"; kind = "stale"; }
    else if (age <= 6000) { text = "Ao vivo"; kind = "live"; }
    else { text = "Sem atualização"; kind = "stale"; }
  }
  element.className = `status ${kind}`;
  element.querySelector("b").textContent = text;
}

async function refresh() {
  if (state.busy || state.paused) return;
  state.busy = true;
  const revision = state.revision;
  try {
    const devices = await getJSON("/api/v1/dashboard/devices");
    if (revision !== state.revision) return;
    setOptions($("device"), devices.map(d => [d.device_id, d.device_id]), $("device").value);
    if (!devices.length) {
      state.data = null; state.error = false;
      empty("Sem dados", "Aguardando o ESP32.");
      render(); return;
    }
    const deviceId = $("device").value;
    const sessions = await getJSON(`/api/v1/dashboard/sessions?device_id=${encodeURIComponent(deviceId)}`);
    if (revision !== state.revision) return;
    const selectedSession = $("session").value;
    const sessionEntries = [["", "Ao vivo"], ...sessions.map(s => [s.boot_id,
      `${date(s.first_received_at)} · ${s.boot_id.slice(0, 8)}`])];
    // Keep a selected older session even when it leaves the latest 100 results.
    if (selectedSession && !sessions.some(s => s.boot_id === selectedSession)) {
      sessionEntries.push([selectedSession, $("session").selectedOptions[0].textContent]);
    }
    setOptions($("session"), sessionEntries, selectedSession);
    const params = new URLSearchParams({device_id: deviceId, segundos: $("window").value});
    if ($("session").value) params.set("boot_id", $("session").value);
    const endpoint = selectedSession ? "signal" : "timeseries";
    const data = await getJSON(`/api/v1/dashboard/${endpoint}?${params}`);
    if (revision !== state.revision) return;
    if (data.end_time_ms && data.end_time_ms !== state.data?.end_time_ms) {
      state.displayEnd = state.displayEnd === null ? data.end_time_ms : Math.min(state.displayEnd, data.end_time_ms);
      state.animationFrom = state.displayEnd;
      state.animationStart = performance.now();
    }
    state.data = data; state.error = false; state.fetchedAt = Date.now();
    $("empty").hidden = true;
    render();
  } catch (error) {
    if (revision !== state.revision) return;
    state.error = true;
    $("notice").hidden = false;
    $("notice").textContent = state.data
      ? "Sem conexão. Exibindo a última leitura."
      : "Servidor indisponível. Tentando reconectar…";
    if (!state.data) empty("Sem conexão", "Tentando reconectar…");
  } finally {
    state.busy = false; status();
    if (revision !== state.revision && !state.paused) refresh();
  }
}

function render() {
  const data = state.data;
  $("time-caption").textContent = data?.end_time_ms ? "Horário estimado" : "Tempo relativo (s)";
  $("export").disabled = !data;
  document.querySelectorAll(".unit-label").forEach(el => { el.textContent = unit(); });
  if (!data) {
    state.traces = [];
    ["rms", "peak", "samples", "rate", "received"].forEach(id => { $(id).textContent = "—"; });
    if (!state.error) $("notice").hidden = true;
    draw(); status(); return;
  }
  const scale = state.unit === "g" ? 1 / 9.80665 : 1;
  let squared = 0, peak = 0;
  state.traces = data.traces.map(trace => {
    const means = [0, 1, 2].map(axis => trace.points.reduce((sum, p) => sum + p[axis + 1], 0) / trace.points.length);
    return {...trace, points: trace.points.map(p => {
      const ac = means.map((mean, axis) => (p[axis + 1] - mean) * scale);
      const magnitudeSquared = ac.reduce((sum, n) => sum + n * n, 0);
      squared += magnitudeSquared; peak = Math.max(peak, Math.sqrt(magnitudeSquared));
      return [p[0], ...($("mode").value === "ac" ? ac : p.slice(1).map(n => n * scale))];
    })};
  });
  $("rms").textContent = number(Math.sqrt(squared / data.sample_count));
  $("peak").textContent = number(peak);
  $("samples").textContent = number(data.sample_count, 0);
  $("rate").textContent = number(data.sample_rate_hz, 0);
  $("received").textContent = date(data.last_received_at);
  const warnings = [];
  if (data.boot_count > 1) warnings.push(`Lacunas na captura · ${data.boot_count - 1} reinício(s) do ESP32.`);
  else if (data.traces.length > 1) warnings.push("Lacunas na captura.");
  if (data.clipped_samples) warnings.push(`Saturação: ${data.clipped_samples} amostras.`);
  $("notice").hidden = warnings.length === 0;
  $("notice").textContent = warnings.join(" ");
  if (state.error) {
    $("notice").hidden = false;
    $("notice").textContent = "Sem conexão. Exibindo a última leitura.";
  }
  draw(); status();
}

function draw() {
  const canvas = $("plot"), ctx = canvas.getContext("2d");
  const {width, height} = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  ctx.scale(dpr, dpr);
  const left = 60, right = width - 20, top = 30, bottom = height - 33;
  const seconds = state.data?.window_seconds ?? Number($("window").value);
  const offset = state.data?.end_time_ms ? (state.displayEnd - state.data.end_time_ms) / 1000 : 0;
  let low = Infinity, high = -Infinity;
  for (const trace of state.traces) for (const point of trace.points) {
    state.axes.forEach((visible, axis) => { if (visible) { low = Math.min(low, point[axis + 1]); high = Math.max(high, point[axis + 1]); } });
  }
  if (!Number.isFinite(low)) { low = -1; high = 1; }
  const span = Math.max(high - low, state.unit === "g" ? .002 : .02);
  low -= span * .12; high += span * .12;
  const x = t => left + (t - offset + seconds) / seconds * (right - left);
  const y = value => bottom - (value - low) / (high - low) * (bottom - top);
  state.geometry = {left, right, top, bottom, seconds, offset, x, y};
  ctx.font = "10px ui-monospace, monospace"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const value = low + (high - low) * i / 4, py = y(value);
    ctx.strokeStyle = "#30383b"; ctx.setLineDash([2, 5]); ctx.beginPath();ctx.moveTo(left, py);ctx.lineTo(right, py);ctx.stroke();
    ctx.fillStyle = "#94a09a";ctx.textAlign = "right"; ctx.fillText(number(value, span < .02 ? 4 : span < .2 ? 3 : 2), left - 9, py + 3);
  }
  for (let i = 0; i <= 5; i++) {
    const t = -seconds + seconds * i / 5 + offset, px = x(t);
    ctx.strokeStyle = "#252e32";ctx.beginPath();ctx.moveTo(px, top);ctx.lineTo(px, bottom);ctx.stroke();
    ctx.fillStyle = "#94a09a";ctx.textAlign = "center";
    const label = state.data?.end_time_ms
      ? new Date(state.data.end_time_ms + t * 1000).toLocaleTimeString("pt-BR", {hour12:false})
      : number(t, seconds === 1 ? 1 : 0);
    ctx.fillText(label, px, bottom + 22);
  }
  ctx.setLineDash([]);ctx.textAlign = "left";ctx.fillStyle = "#a0ada6";ctx.fillText(unit(), left, 15);
  ctx.save();ctx.beginPath();ctx.rect(left, top, right - left, bottom - top);ctx.clip();
  state.axes.forEach((visible, axis) => {
    if (!visible) return;
    ctx.strokeStyle = colors[axis];ctx.lineWidth = 1.25;ctx.lineJoin = "round";
    for (const trace of state.traces) {
      ctx.beginPath();trace.points.forEach((point, i) => {
        if (i === 0) ctx.moveTo(x(point[0]), y(point[axis + 1]));
        else ctx.lineTo(x(point[0]), y(point[axis + 1]));
      });ctx.stroke();
    }
  });ctx.restore();
  canvas.setAttribute("aria-label", `Aceleração XYZ em ${unit()}, ${$("mode").value === "ac" ? "média removida" : "original"}, últimos ${seconds} segundos. ${state.data?.sample_count ?? 0} amostras.`);
}

function changeQuery() {
  state.revision++; state.data = null; state.error = false; state.displayEnd = null;
  empty("Carregando…", "");
  state.paused = false; $("pause").textContent = "Pausar";
  $("pause").setAttribute("aria-pressed", "false");
  render(); refresh();
}
$("device").addEventListener("change", () => { $("session").value = ""; changeQuery(); });
$("session").addEventListener("change", changeQuery);
$("window").addEventListener("change", changeQuery);
$("mode").addEventListener("change", render);
$("pause").addEventListener("click", () => {
  state.paused = !state.paused; state.revision++;
  if (state.paused && state.data?.end_time_ms) { state.displayEnd = state.data.end_time_ms; draw(); }
  $("pause").textContent = state.paused ? "Retomar" : "Pausar";
  $("pause").setAttribute("aria-pressed", String(state.paused));
  status(); if (!state.paused) refresh();
});
document.querySelectorAll("[data-unit]").forEach(button => button.addEventListener("click", () => {
  state.unit = button.dataset.unit;
  document.querySelectorAll("[data-unit]").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
  render();
}));
document.querySelectorAll("[data-axis]").forEach(input => input.addEventListener("change", () => {
  state.axes[Number(input.dataset.axis)] = input.checked; draw();
}));
$("plot").addEventListener("pointermove", (event) => {
  if (!state.traces.length || !state.geometry) return;
  const rect = $("plot").getBoundingClientRect(), mouseX = event.clientX - rect.left, mouseY = event.clientY - rect.top;
  const {left, right, top, bottom, seconds, offset} = state.geometry;
  if (mouseX < left || mouseX > right || mouseY < top || mouseY > bottom) { $("tooltip").hidden = true; return; }
  const t = (mouseX - left) / (right - left) * seconds - seconds + offset;
  let nearest = null, distance = Infinity;
  for (const trace of state.traces) for (const p of trace.points) {
    const delta = Math.abs(p[0] - t); if (delta < distance) { nearest = p; distance = delta; }
  }
  if (distance > seconds / (right - left) * 10) { $("tooltip").hidden = true; return; }
  const tooltip = $("tooltip");
  const timeLabel = state.data.end_time_ms
    ? `${new Date(state.data.end_time_ms + nearest[0] * 1000).toLocaleTimeString("pt-BR", {hour12:false})} · estimado`
    : `t ${number(nearest[0])} s`;
  tooltip.textContent = `${timeLabel}\n` + ["X", "Y", "Z"].filter((_, i) => state.axes[i]).map(axis => `${axis}  ${number(nearest["XYZ".indexOf(axis) + 1], 4)} ${unit()}`).join("\n");
  tooltip.hidden = false;
  tooltip.style.left = `${Math.max(0, Math.min(mouseX + 12, rect.width - tooltip.offsetWidth - 5))}px`;
  tooltip.style.top = `${Math.max(0, mouseY - tooltip.offsetHeight - 8)}px`;
});
$("plot").addEventListener("pointerleave", () => { $("tooltip").hidden = true; });
$("export").addEventListener("click", () => {
  if (!state.data) return;
  const rows = [["device_id", "boot_id", "trecho", "segment_id", "tempo_relativo_s", "x", "y", "z", "unidade", "tratamento", "horario_estimado_utc"]];
  state.traces.forEach((trace, index) => trace.points.forEach(p => rows.push([
    state.data.device_id, trace.boot_id ?? state.data.boot_id, index + 1, trace.segment_id,
    ...p, unit(), $("mode").value === "ac" ? "media_removida_por_trecho" : "original",
    state.data.end_time_ms ? new Date(state.data.end_time_ms + p[0] * 1000).toISOString() : ""
  ])));
  const url = URL.createObjectURL(new Blob(["\uFEFF", rows.map(row => row.join(",")).join("\r\n")], {type:"text/csv;charset=utf-8"}));
  const link = document.createElement("a");link.href = url;
  link.download = `cascavibe-${state.data.device_id}-${state.data.boot_id.slice(0, 8)}-${$("mode").value}.csv`;
  link.click();setTimeout(() => URL.revokeObjectURL(url), 1000);
});
new ResizeObserver(draw).observe($("plot-wrap"));
function animate(now) {
  if (!state.paused && !document.hidden && state.data?.end_time_ms && state.displayEnd !== state.data.end_time_ms && now - state.lastFrame >= 50) {
    const progress = Math.min(1, (now - state.animationStart) / 950);
    state.displayEnd = state.animationFrom + (state.data.end_time_ms - state.animationFrom) * progress;
    state.lastFrame = now;
    draw();
  }
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
setInterval(() => { status(); refresh(); }, 1000);
refresh();
