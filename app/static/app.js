// app.js - cliente de Atlas Audio

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  jobs: [],
  activeTtsJobId: null,
  activeSttJobId: null,
  activeTranslateJobId: null,
  selectedAudio: null,
  selectedTranslateAudio: null,
  loadedJobId: null,
  autoLoadedJobIds: new Set(),
};

/* ----------------- Helpers ----------------- */
function escapeHtml(s = "") {
  return String(s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function fmt(sec) {
  if (!Number.isFinite(sec)) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const mb = bytes / 1024 / 1024;
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

function progressPercent(job) {
  return Math.max(0, Math.min(100, Math.round((job.progress || 0) * 100)));
}

function statusText(job) {
  return {
    queued: "En cola",
    running: "Procesando",
    done: "Listo",
    failed: "Error",
    cancelled: "Cancelado",
  }[job.status] || job.status;
}

function kindText(job) {
  return {
    tts: "Audio",
    stt: "Texto",
    translate_audio: "Traduccion",
  }[job.kind] || job.kind;
}

function fileTooLarge(file) {
  const maxMb = window.ATLAS_LIMITS?.maxAudioMb || 200;
  if (file.size <= maxMb * 1024 * 1024) return false;
  alert(`Archivo demasiado grande. Maximo: ${maxMb} MB.`);
  return true;
}

async function parseError(res) {
  try {
    const data = await res.json();
    return data.detail || data.error || "Error";
  } catch {
    return res.statusText || "Error";
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("No autenticado");
  }
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

function setupDropzone(zone, input, browseBtn, onFile) {
  browseBtn.addEventListener("click", () => input.click());
  zone.addEventListener("click", (e) => {
    if (e.target !== browseBtn) input.click();
  });
  ["dragover", "dragenter"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
    })
  );
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => {
    if (input.files.length) onFile(input.files[0]);
  });
}

/* ----------------- Tabs ----------------- */
const tabs = $$(".tab");
const panels = {
  read: $("#panel-read"),
  listen: $("#panel-listen"),
  translate: $("#panel-translate"),
  transcribe: $("#panel-transcribe"),
};

function showTab(name) {
  tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
  Object.entries(panels).forEach(([k, el]) => {
    el.classList.toggle("is-active", k === name);
  });
}

tabs.forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

/* ----------------- Documento -> texto ----------------- */
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
const textInput = $("#text-input");
const titleInput = $("#title-input");
const estimate = $("#estimate");
const generateBtn = $("#generate-btn");

setupDropzone(dropzone, fileInput, $("#browse-btn"), handleDocument);

async function handleDocument(file) {
  estimate.textContent = "Extrayendo texto...";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const data = await fetchJson("/api/document", { method: "POST", body: fd });
    textInput.value = data.text;
    if (!titleInput.value.trim()) titleInput.value = file.name.replace(/\.[^.]+$/, "");
    if (data.warning) estimate.textContent = data.warning;
    updateEstimate();
  } catch (err) {
    estimate.textContent = `Error: ${err.message}`;
  }
}

/* ----------------- Estimacion ----------------- */
let estTimer;
textInput.addEventListener("input", () => {
  clearTimeout(estTimer);
  estTimer = setTimeout(updateEstimate, 450);
});

async function updateEstimate() {
  const text = textInput.value.trim();
  const maxChars = window.ATLAS_LIMITS?.maxTtsChars || 60000;
  if (!text) {
    estimate.textContent = "- caracteres · - fragmentos";
    generateBtn.disabled = false;
    return;
  }
  if (text.length > maxChars) {
    estimate.textContent = `${text.length.toLocaleString()} caracteres · supera el limite seguro (${maxChars.toLocaleString()})`;
    generateBtn.disabled = true;
    return;
  }
  generateBtn.disabled = false;
  estimate.textContent = `${text.length.toLocaleString()} caracteres · calculando...`;
  try {
    const data = await fetchJson("/api/tts/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    estimate.textContent = `${data.chars.toLocaleString()} caracteres · ${data.fragments} fragmentos`;
  } catch {
    estimate.textContent = `${text.length.toLocaleString()} caracteres`;
  }
}

/* ----------------- TTS jobs ----------------- */
const activeTtsJob = $("#active-tts-job");
const activeTtsTitle = $("#active-tts-title");
const progressText = $("#progress-text");
const ttsProgressBar = $("#tts-progress-bar");

generateBtn.addEventListener("click", async () => {
  const text = textInput.value.trim();
  if (!text) {
    alert("Pega texto o sube un documento primero.");
    return;
  }

  generateBtn.disabled = true;
  try {
    const job = await fetchJson("/api/tts/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        title: titleInput.value.trim(),
        voice: $("#voice-select").value,
      }),
    });
    state.activeTtsJobId = job.id;
    renderActiveJobs();
    showTab("listen");
    await refreshJobs();
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    generateBtn.disabled = false;
  }
});

$("#cancel-tts-btn").addEventListener("click", () => {
  if (state.activeTtsJobId) cancelJob(state.activeTtsJobId);
});

/* ----------------- STT jobs ----------------- */
const dropzoneAudio = $("#dropzone-audio");
const audioInput = $("#audio-input");
const transcribeBtn = $("#transcribe-btn");
const selectedAudioName = $("#selected-audio-name");
const activeSttJob = $("#active-stt-job");
const activeSttTitle = $("#active-stt-title");
const sttProgressText = $("#stt-progress-text");
const transcript = $("#transcript");

setupDropzone(dropzoneAudio, audioInput, $("#browse-audio-btn"), selectAudio);

function selectAudio(file) {
  if (fileTooLarge(file)) return;
  state.selectedAudio = file;
  selectedAudioName.textContent = `${file.name} · ${formatBytes(file.size)}`;
  transcribeBtn.disabled = false;
}

transcribeBtn.addEventListener("click", async () => {
  if (!state.selectedAudio) return;
  transcribeBtn.disabled = true;
  transcript.hidden = true;

  const fd = new FormData();
  fd.append("file", state.selectedAudio);
  fd.append("language", "es");
  try {
    const job = await fetchJson("/api/stt/jobs", { method: "POST", body: fd });
    state.activeSttJobId = job.id;
    renderActiveJobs();
    await refreshJobs();
  } catch (err) {
    transcript.hidden = false;
    transcript.innerHTML = `<p class="error-text">Error: ${escapeHtml(err.message)}</p>`;
  } finally {
    transcribeBtn.disabled = false;
  }
});

$("#cancel-stt-btn").addEventListener("click", () => {
  if (state.activeSttJobId) cancelJob(state.activeSttJobId);
});

/* ----------------- Traduccion de audio ----------------- */
const dropzoneTranslate = $("#dropzone-translate");
const translateInput = $("#translate-input");
const translateBtn = $("#translate-btn");
const selectedTranslateName = $("#selected-translate-name");
const translateEstimate = $("#translate-estimate");
const activeTranslateJob = $("#active-translate-job");
const activeTranslateTitle = $("#active-translate-title");
const translateProgressText = $("#translate-progress-text");
const translateProgressBar = $("#translate-progress-bar");
const translationTranscript = $("#translation-transcript");

setupDropzone(
  dropzoneTranslate,
  translateInput,
  $("#browse-translate-btn"),
  selectTranslateAudio,
);

function selectTranslateAudio(file) {
  if (fileTooLarge(file)) return;
  state.selectedTranslateAudio = file;
  selectedTranslateName.textContent = `${file.name} · ${formatBytes(file.size)}`;
  translateBtn.disabled = false;
}

translateBtn.addEventListener("click", async () => {
  if (!state.selectedTranslateAudio) return;
  translateBtn.disabled = true;
  translationTranscript.hidden = true;

  const fd = new FormData();
  fd.append("file", state.selectedTranslateAudio);
  fd.append("source_language", "en");
  fd.append("voice", $("#voice-select-translate").value);
  try {
    const job = await fetchJson("/api/translate-audio/jobs", { method: "POST", body: fd });
    state.activeTranslateJobId = job.id;
    translateEstimate.textContent = "Trabajo enviado a la cola.";
    renderActiveJobs();
    await refreshJobs();
  } catch (err) {
    translateEstimate.textContent = `Error: ${err.message}`;
  } finally {
    translateBtn.disabled = false;
  }
});

$("#cancel-translate-btn").addEventListener("click", () => {
  if (state.activeTranslateJobId) cancelJob(state.activeTranslateJobId);
});

/* ----------------- Trabajos / Biblioteca ----------------- */
const jobsList = $("#jobs-list");
$("#refresh-jobs").addEventListener("click", refreshJobs);

async function refreshJobs() {
  try {
    const data = await fetchJson("/api/jobs");
    state.jobs = data.jobs || [];
    renderJobs();
    renderActiveJobs();
    maybeAutoLoadCompletedAudio();
    maybeAutoRenderTranscript();
  } catch (err) {
    jobsList.innerHTML = `<div class="empty-state">No se pudo cargar la biblioteca: ${escapeHtml(err.message)}</div>`;
  }
}

function renderJobs() {
  if (!state.jobs.length) {
    jobsList.innerHTML = `<div class="empty-state">Todavia no hay trabajos.</div>`;
    return;
  }

  jobsList.innerHTML = state.jobs.map((job) => {
    const pct = progressPercent(job);
    const detail = jobDetail(job);
    const actions = jobActions(job);
    return `
      <article class="job-item ${job.status}">
        <div class="job-main">
          <div class="job-kicker">${kindText(job)} · ${statusText(job)}</div>
          <h3>${escapeHtml(job.title)}</h3>
          <p>${detail}</p>
          <div class="mini-progress"><span style="width:${pct}%"></span></div>
        </div>
        <div class="job-actions">${actions}</div>
      </article>
    `;
  }).join("");
}

function jobDetail(job) {
  if (job.kind === "tts" && job.result?.duration) {
    return `${fmt(job.result.duration)} · ${job.result.fragments || 0} fragmentos`;
  }
  if (job.kind === "translate_audio" && job.result?.duration) {
    return `${fmt(job.result.duration)} · ${job.result.fragments || 0} fragmentos · ${escapeHtml(job.result.translation_model || "Ollama")}`;
  }
  if (job.kind === "stt" && job.result?.duration) {
    return `${fmt(job.result.duration)} · ${job.result.segments_count || 0} segmentos`;
  }
  return escapeHtml(job.error || job.message || "");
}

function jobActions(job) {
  if (job.status === "queued" || job.status === "running") {
    return `<button class="t-btn danger" data-action="cancel" data-job="${job.id}">Cancelar</button>`;
  }
  if (job.status === "done" && (job.kind === "tts" || job.kind === "translate_audio")) {
    const textButton = job.kind === "translate_audio"
      ? `<button class="t-btn" data-action="transcript" data-job="${job.id}">Texto</button>`
      : "";
    return `
      <button class="t-btn" data-action="play" data-job="${job.id}">Reproducir</button>
      <a class="t-btn as-link" href="${job.result.audio_url}" download="${escapeHtml(job.result.filename || "atlas-audio.mp3")}">MP3</a>
      ${textButton}
      <button class="t-btn danger" data-action="delete" data-job="${job.id}">Borrar</button>
    `;
  }
  if (job.status === "done" && job.kind === "stt") {
    return `
      <button class="t-btn" data-action="transcript" data-job="${job.id}">Ver texto</button>
      <button class="t-btn danger" data-action="delete" data-job="${job.id}">Borrar</button>
    `;
  }
  return `<button class="t-btn danger" data-action="delete" data-job="${job.id}">Borrar</button>`;
}

jobsList.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const job = state.jobs.find((j) => j.id === el.dataset.job);
  if (!job) return;

  if (el.dataset.action === "cancel") await cancelJob(job.id);
  if (el.dataset.action === "play") loadAudioFromJob(job);
  if (el.dataset.action === "transcript") {
    if (job.kind === "translate_audio") {
      await loadTranscript(job.id, translationTranscript);
      showTab("translate");
    } else {
      await loadTranscript(job.id, transcript);
      showTab("transcribe");
    }
  }
  if (el.dataset.action === "delete") await deleteJob(job);
});

async function cancelJob(jobId) {
  try {
    await fetchJson(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    await refreshJobs();
  } catch (err) {
    alert(`No se pudo cancelar: ${err.message}`);
  }
}

async function deleteJob(job) {
  if (!confirm(`Borrar "${job.title}"?`)) return;
  try {
    await fetchJson(`/api/jobs/${job.id}`, { method: "DELETE" });
    if (state.loadedJobId === job.id) clearPlayer();
    await refreshJobs();
  } catch (err) {
    alert(`No se pudo borrar: ${err.message}`);
  }
}

function renderActiveJobs() {
  const ttsJob = state.jobs.find((j) => j.id === state.activeTtsJobId);
  if (ttsJob && ["queued", "running"].includes(ttsJob.status)) {
    activeTtsJob.hidden = false;
    activeTtsTitle.textContent = ttsJob.title;
    progressText.textContent = ttsJob.message || statusText(ttsJob);
    ttsProgressBar.style.width = `${progressPercent(ttsJob)}%`;
  } else {
    activeTtsJob.hidden = true;
  }

  const sttJob = state.jobs.find((j) => j.id === state.activeSttJobId);
  if (sttJob && ["queued", "running"].includes(sttJob.status)) {
    activeSttJob.hidden = false;
    activeSttTitle.textContent = sttJob.title;
    sttProgressText.textContent = sttJob.message || statusText(sttJob);
  } else {
    activeSttJob.hidden = true;
  }

  const translateJob = state.jobs.find((j) => j.id === state.activeTranslateJobId);
  if (translateJob && ["queued", "running"].includes(translateJob.status)) {
    activeTranslateJob.hidden = false;
    activeTranslateTitle.textContent = translateJob.title;
    translateProgressText.textContent = translateJob.message || statusText(translateJob);
    translateProgressBar.style.width = `${progressPercent(translateJob)}%`;
  } else {
    activeTranslateJob.hidden = true;
  }
}

function maybeAutoLoadCompletedAudio() {
  [state.activeTtsJobId, state.activeTranslateJobId].forEach((jobId) => {
    const job = state.jobs.find((j) => j.id === jobId);
    if (!job || job.status !== "done" || state.autoLoadedJobIds.has(job.id)) return;
    loadAudioFromJob(job);
    state.autoLoadedJobIds.add(job.id);
  });
}

function maybeAutoRenderTranscript() {
  const sttJob = state.jobs.find((j) => j.id === state.activeSttJobId);
  if (sttJob && sttJob.status === "done" && transcript.dataset.job !== sttJob.id) {
    loadTranscript(sttJob.id, transcript);
  }

  const translateJob = state.jobs.find((j) => j.id === state.activeTranslateJobId);
  if (
    translateJob
    && translateJob.status === "done"
    && translationTranscript.dataset.job !== translateJob.id
  ) {
    loadTranscript(translateJob.id, translationTranscript);
  }
}

/* ----------------- Reproductor ----------------- */
const playerEmpty = $("#player-empty");
const playerBody = $("#player-body");
const audio = $("#audio");
const playBtn = $("#play-btn");
const miniPlayer = $("#mini-player");
const miniPlay = $("#mini-play");
const seek = $("#seek");
const miniSeek = $("#mini-seek");
const tCur = $("#t-cur");
const tDur = $("#t-dur");
const miniTime = $("#mini-time");
const npDisc = $("#np-disc");
const downloadBtn = $("#download-btn");

function loadAudioFromJob(job) {
  const subtitle = job.kind === "translate_audio"
    ? `Traduccion ES · ${job.result.voice || "voz"} · ${fmt(job.result.duration || 0)}`
    : `${job.result.voice || "voz"} · ${fmt(job.result.duration || 0)}`;
  loadAudio(
    job.result.audio_url,
    job.title,
    subtitle,
    job.result.filename || "atlas-audio.mp3",
    job.id,
  );
  showTab("listen");
}

function loadAudio(url, title, subtitle, filename, jobId) {
  state.loadedJobId = jobId;
  audio.src = url;
  downloadBtn.href = url;
  downloadBtn.download = filename;
  $("#np-title").textContent = title;
  $("#np-sub").textContent = subtitle;
  $("#mini-title").textContent = title;
  $("#mini-status").textContent = subtitle;
  playerEmpty.hidden = true;
  playerBody.hidden = false;
  miniPlayer.hidden = false;
  audio.load();
  applySpeed($("#speed-slider").value || 1);
}

function clearPlayer() {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  state.loadedJobId = null;
  playerEmpty.hidden = false;
  playerBody.hidden = true;
  miniPlayer.hidden = true;
}

function setPlayState(isPlaying) {
  playBtn.textContent = isPlaying ? "||" : ">";
  miniPlay.textContent = isPlaying ? "||" : ">";
  npDisc.classList.toggle("spin", isPlaying);
}

function togglePlay() {
  if (!audio.src) return;
  if (audio.paused) audio.play();
  else audio.pause();
}

playBtn.addEventListener("click", togglePlay);
miniPlay.addEventListener("click", togglePlay);
audio.addEventListener("play", () => setPlayState(true));
audio.addEventListener("pause", () => setPlayState(false));
audio.addEventListener("ended", () => setPlayState(false));
audio.addEventListener("loadedmetadata", () => {
  tDur.textContent = fmt(audio.duration);
});
audio.addEventListener("timeupdate", () => {
  if (!audio.duration) return;
  const value = (audio.currentTime / audio.duration) * 1000;
  seek.value = value;
  miniSeek.value = value;
  tCur.textContent = fmt(audio.currentTime);
  miniTime.textContent = fmt(audio.currentTime);
});
seek.addEventListener("input", () => {
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
});
miniSeek.addEventListener("input", () => {
  if (audio.duration) audio.currentTime = (miniSeek.value / 1000) * audio.duration;
});
$("#back-btn").addEventListener("click", () => {
  audio.currentTime = Math.max(0, audio.currentTime - 15);
});
$("#fwd-btn").addEventListener("click", () => {
  audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 15);
});

/* ----------------- Velocidad ----------------- */
const speedSlider = $("#speed-slider");
const speedValue = $("#speed-value");
const presets = $$(".speed-presets button");

function applySpeed(rate) {
  rate = Math.min(3, Math.max(0.5, parseFloat(rate)));
  audio.playbackRate = rate;
  audio.preservesPitch = true;
  audio.mozPreservesPitch = true;
  audio.webkitPreservesPitch = true;
  speedSlider.value = rate;
  speedValue.textContent = `${rate.toFixed(1)}x`;
  presets.forEach((b) =>
    b.classList.toggle("active", parseFloat(b.dataset.speed) === rate)
  );
}

speedSlider.addEventListener("input", () => applySpeed(speedSlider.value));
presets.forEach((b) => b.addEventListener("click", () => applySpeed(b.dataset.speed)));
applySpeed(1);

document.addEventListener("keydown", (e) => {
  if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName)) return;
  if (miniPlayer.hidden) return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
  }
  if (e.code === "ArrowUp") {
    e.preventDefault();
    applySpeed(audio.playbackRate + 0.1);
  }
  if (e.code === "ArrowDown") {
    e.preventDefault();
    applySpeed(audio.playbackRate - 0.1);
  }
});

/* ----------------- Transcript ----------------- */
async function loadTranscript(jobId, target = transcript) {
  target.dataset.job = jobId; // marca antes del fetch para evitar doble carga desde el polling
  try {
    const data = await fetchJson(`/api/jobs/${jobId}/transcript`);
    renderTranscript(data, jobId, target);
  } catch (err) {
    target.hidden = false;
    target.innerHTML = `<p class="error-text">Error: ${escapeHtml(err.message)}</p>`;
  }
}

function renderTranscript(data, jobId, target = transcript) {
  target.hidden = false;
  target.dataset.job = jobId;

  if (data.source_text) {
    target.innerHTML = `
      <div class="transcript-actions">
        <button class="t-btn" data-copy="translated">Copiar traduccion</button>
        <button class="t-btn" data-copy="source">Copiar original</button>
        <span class="estimate">${escapeHtml(data.translation_model || "Ollama")} · ${fmt(data.duration)}</span>
      </div>
      <div class="translation-block">
        <h3>Espanol</h3>
        <p>${escapeHtml(data.text || "").replace(/\n/g, "<br>")}</p>
      </div>
      <details class="source-block">
        <summary>Original ingles</summary>
        <p>${escapeHtml(data.source_text || "").replace(/\n/g, "<br>")}</p>
      </details>
    `;
    target.querySelector('[data-copy="translated"]').addEventListener("click", (e) => {
      navigator.clipboard.writeText(data.text || "");
      e.currentTarget.textContent = "Copiado";
    });
    target.querySelector('[data-copy="source"]').addEventListener("click", (e) => {
      navigator.clipboard.writeText(data.source_text || "");
      e.currentTarget.textContent = "Copiado";
    });
    return;
  }

  const segs = (data.segments || [])
    .map((s) => `
      <div class="seg">
        <span class="seg-time">${fmt(s.start)}</span>
        <span class="seg-text">${escapeHtml(s.text)}</span>
      </div>
    `)
    .join("");
  target.innerHTML = `
    <div class="transcript-actions">
      <button class="t-btn" data-copy="text">Copiar texto</button>
      <span class="estimate">${escapeHtml(data.language || "auto")} · ${fmt(data.duration)}</span>
    </div>
    ${segs || `<p class="empty-state">Sin segmentos.</p>`}
  `;
  target.querySelector('[data-copy="text"]').addEventListener("click", (e) => {
    navigator.clipboard.writeText(data.text || "");
    e.currentTarget.textContent = "Copiado";
  });
}

/* ----------------- Inicio ----------------- */
refreshJobs();

// Polling adaptativo: rápido cuando hay trabajos activos, lento en reposo.
function hasActiveJobs() {
  return state.jobs.some((j) => j.status === "queued" || j.status === "running");
}
let _pollTimer = null;
function schedulePoll() {
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    await refreshJobs();
    schedulePoll();
  }, hasActiveJobs() ? 1500 : 5000);
}
schedulePoll();
