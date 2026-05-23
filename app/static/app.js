// app.js — lógica del cliente de Atlas Audio

const $ = (sel) => document.querySelector(sel);

/* ----------------- Tabs ----------------- */
const tabs = document.querySelectorAll(".tab");
const panels = {
  read: $("#panel-read"),
  listen: $("#panel-listen"),
  transcribe: $("#panel-transcribe"),
};
function showTab(name) {
  tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
  Object.entries(panels).forEach(([k, el]) =>
    el.classList.toggle("is-active", k === name)
  );
}
tabs.forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

/* ----------------- Documento -> texto ----------------- */
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
const textInput = $("#text-input");
const estimate = $("#estimate");

$("#browse-btn").addEventListener("click", () => fileInput.click());
dropzone.addEventListener("click", (e) => {
  if (e.target.id !== "browse-btn") fileInput.click();
});
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) handleDocument(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) handleDocument(fileInput.files[0]);
});

async function handleDocument(file) {
  estimate.textContent = "Extrayendo texto…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/document", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || "Error");
    const data = await res.json();
    textInput.value = data.text;
    updateEstimate();
  } catch (err) {
    estimate.textContent = "Error: " + err.message;
  }
}

/* ----------------- Estimación (debounce) ----------------- */
let estTimer;
textInput.addEventListener("input", () => {
  clearTimeout(estTimer);
  estTimer = setTimeout(updateEstimate, 500);
});
async function updateEstimate() {
  const text = textInput.value.trim();
  if (!text) {
    estimate.textContent = "— caracteres · — fragmentos";
    return;
  }
  estimate.textContent = `${text.length.toLocaleString()} caracteres · calculando…`;
  try {
    const res = await fetch("/api/tts/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    estimate.textContent = `${data.chars.toLocaleString()} caracteres · ${data.fragments} fragmentos`;
  } catch {
    estimate.textContent = `${text.length.toLocaleString()} caracteres`;
  }
}

/* ----------------- Generar TTS ----------------- */
const generateBtn = $("#generate-btn");
const progress = $("#progress");
const progressText = $("#progress-text");
const audio = $("#audio");

generateBtn.addEventListener("click", async () => {
  const text = textInput.value.trim();
  if (!text) {
    alert("Pega un texto o sube un documento primero.");
    return;
  }
  generateBtn.disabled = true;
  progress.hidden = false;
  progressText.textContent = "Sintetizando con Kokoro… (textos largos tardan un poco)";

  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: $("#voice-select").value,
        speed: 1.0, // prosodia natural; la velocidad de escucha es del reproductor
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Error en síntesis");
    const blob = await res.blob();
    loadAudio(URL.createObjectURL(blob));
    showTab("listen");
  } catch (err) {
    alert("Error: " + err.message);
  } finally {
    generateBtn.disabled = false;
    progress.hidden = true;
  }
});

/* ----------------- Reproductor ----------------- */
const playerEmpty = $("#player-empty");
const playerBody = $("#player-body");
const playBtn = $("#play-btn");
const seek = $("#seek");
const tCur = $("#t-cur");
const tDur = $("#t-dur");
const npDisc = $("#np-disc");
const downloadBtn = $("#download-btn");

function loadAudio(url) {
  audio.src = url;
  downloadBtn.href = url;
  playerEmpty.hidden = true;
  playerBody.hidden = false;
  audio.load();
}

function fmt(sec) {
  if (!isFinite(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

playBtn.addEventListener("click", () => {
  if (audio.paused) audio.play();
  else audio.pause();
});
audio.addEventListener("play", () => {
  playBtn.textContent = "⏸";
  npDisc.classList.add("spin");
});
audio.addEventListener("pause", () => {
  playBtn.textContent = "▶";
  npDisc.classList.remove("spin");
});
audio.addEventListener("loadedmetadata", () => {
  tDur.textContent = fmt(audio.duration);
});
audio.addEventListener("timeupdate", () => {
  if (audio.duration) {
    seek.value = (audio.currentTime / audio.duration) * 1000;
    tCur.textContent = fmt(audio.currentTime);
  }
});
seek.addEventListener("input", () => {
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
});
$("#back-btn").addEventListener("click", () => (audio.currentTime = Math.max(0, audio.currentTime - 15)));
$("#fwd-btn").addEventListener("click", () => (audio.currentTime = Math.min(audio.duration, audio.currentTime + 15)));

/* --------- Control de velocidad 0.5x – 3x (preserva el tono) --------- */
const speedSlider = $("#speed-slider");
const speedValue = $("#speed-value");
const presets = document.querySelectorAll(".speed-presets button");

function applySpeed(rate) {
  rate = Math.min(3, Math.max(0.5, parseFloat(rate)));
  audio.playbackRate = rate;
  // preservesPitch: el navegador mantiene el tono natural de la voz al acelerar
  audio.preservesPitch = true;
  audio.mozPreservesPitch = true;
  audio.webkitPreservesPitch = true;
  speedSlider.value = rate;
  speedValue.textContent = rate.toFixed(1) + "x";
  presets.forEach((b) =>
    b.classList.toggle("active", parseFloat(b.dataset.speed) === rate)
  );
}
speedSlider.addEventListener("input", () => applySpeed(speedSlider.value));
presets.forEach((b) =>
  b.addEventListener("click", () => applySpeed(b.dataset.speed))
);
applySpeed(1); // estado inicial

// Atajos de teclado: espacio = play/pausa, flechas = velocidad
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  if (playerBody.hidden) return;
  if (e.code === "Space") { e.preventDefault(); playBtn.click(); }
  if (e.code === "ArrowUp") { e.preventDefault(); applySpeed(audio.playbackRate + 0.1); }
  if (e.code === "ArrowDown") { e.preventDefault(); applySpeed(audio.playbackRate - 0.1); }
});

/* ----------------- Transcripción (STT) ----------------- */
const dropzoneAudio = $("#dropzone-audio");
const audioInput = $("#audio-input");
const transcribeBtn = $("#transcribe-btn");
const sttProgress = $("#stt-progress");
const transcript = $("#transcript");
let selectedAudio = null;

$("#browse-audio-btn").addEventListener("click", () => audioInput.click());
dropzoneAudio.addEventListener("click", (e) => {
  if (e.target.id !== "browse-audio-btn") audioInput.click();
});
["dragover", "dragenter"].forEach((ev) =>
  dropzoneAudio.addEventListener(ev, (e) => { e.preventDefault(); dropzoneAudio.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  dropzoneAudio.addEventListener(ev, (e) => { e.preventDefault(); dropzoneAudio.classList.remove("drag"); })
);
dropzoneAudio.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) selectAudio(e.dataTransfer.files[0]);
});
audioInput.addEventListener("change", () => {
  if (audioInput.files.length) selectAudio(audioInput.files[0]);
});
function selectAudio(file) {
  selectedAudio = file;
  transcribeBtn.disabled = false;
  dropzoneAudio.querySelector("p").innerHTML = `<strong>${file.name}</strong> listo`;
}

transcribeBtn.addEventListener("click", async () => {
  if (!selectedAudio) return;
  transcribeBtn.disabled = true;
  sttProgress.hidden = false;
  transcript.hidden = true;

  const fd = new FormData();
  fd.append("file", selectedAudio);
  fd.append("language", "es");
  try {
    const res = await fetch("/api/stt", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || "Error");
    renderTranscript(await res.json());
  } catch (err) {
    transcript.hidden = false;
    transcript.innerHTML = `<p style="color:var(--danger)">Error: ${err.message}</p>`;
  } finally {
    transcribeBtn.disabled = false;
    sttProgress.hidden = true;
  }
});

function renderTranscript(data) {
  transcript.hidden = false;
  const segs = (data.segments || [])
    .map(
      (s) =>
        `<div class="seg"><span class="seg-time">${fmt(s.start)}</span><span class="seg-text">${escapeHtml(s.text)}</span></div>`
    )
    .join("");
  transcript.innerHTML = `
    <div class="transcript-actions">
      <button class="t-btn" id="copy-txt">Copiar texto</button>
      <span class="estimate">${data.language} · ${fmt(data.duration)}</span>
    </div>${segs}`;
  $("#copy-txt").addEventListener("click", () => {
    navigator.clipboard.writeText(data.text);
    $("#copy-txt").textContent = "¡Copiado!";
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
