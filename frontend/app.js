// Backend API Configuration:
// When frontend is hosted on Cloudflare Pages (*.pages.dev) or custom domain, route API calls to Render.
let customBackend = "";
try {
  customBackend = window.localStorage ? (window.localStorage.getItem("RENDER_BACKEND_URL") || "") : "";
} catch (e) {
  console.warn("Storage access warning:", e);
}

const DEFAULT_RENDER_BACKEND = "https://lactern.onrender.com";
const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
const isRender = window.location.hostname.endsWith("onrender.com");
const API_BASE = (isRender || (isLocal && (window.location.port === "8000" || window.location.port === "10000")))
  ? ""
  : (customBackend || DEFAULT_RENDER_BACKEND);

// State
let player = null;
let isPlayerReady = false;
let currentVideoId = "0wp5Z3z4bjM";
let currentChunks = [];
let conversationHistory = [];
let wordTimer = null;
let progressTimer = null;
let currentChunkIndex = -1;
let ingestTimerInterval = null;
let ingestStartTime = 0;

// Dynamic DOM getter
const $ = (id) => document.getElementById(id);

// Formatting helper: seconds -> mm:ss
function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

// 1. YouTube IFrame API
window.onYouTubeIframeAPIReady = function () {
  try {
    const playerVars = {
      playsinline: 1,
      modestbranding: 1,
      rel: 0,
      enablejsapi: 1,
    };
    if (window.location.origin && window.location.origin !== "null" && !window.location.origin.startsWith("file:")) {
      playerVars.origin = window.location.origin;
    }

    player = new YT.Player("yt-player", {
      height: "100%",
      width: "100%",
      videoId: currentVideoId || "0wp5Z3z4bjM",
      playerVars: playerVars,
      events: {
        onReady: onPlayerReady,
        onStateChange: onPlayerStateChange,
        onError: onPlayerError,
      },
    });
  } catch (err) {
    console.warn("YouTube player init error:", err);
  }
};

function onPlayerReady(event) {
  isPlayerReady = true;
  const pStatus = $("player-status");
  if (pStatus) pStatus.textContent = "Player ready";
  updateClock();
  if (progressTimer) clearInterval(progressTimer);
  progressTimer = setInterval(updatePlaybackState, 400);
}

function onPlayerStateChange(event) {
  updateClock();
  const pContainer = $("player");
  if (!pContainer) return;
  if (event.data === YT.PlayerState.PLAYING) {
    pContainer.classList.add("playing");
  } else if (event.data === YT.PlayerState.PAUSED || event.data === YT.PlayerState.ENDED) {
    pContainer.classList.remove("playing");
  }
}

function onPlayerError(event) {
  console.warn("YouTube Player error:", event.data);
  // 101/150: Embed disabled by owner; 2/100: Invalid video ID; 5: HTML5 error
  if (event.data === 101 || event.data === 150 || event.data === 2 || event.data === 5 || event.data === 100) {
    showFallback(0);
  }
}

function showFallback(seekSec) {
  if (!currentVideoId) return;
  const fallbackLink = $("fallback-link");
  const playerFallback = $("player-fallback");
  if (fallbackLink) {
    fallbackLink.href = `https://www.youtube.com/watch?v=${currentVideoId}&t=${Math.floor(seekSec)}s`;
  }
  if (playerFallback) playerFallback.classList.remove("hidden");
}

function hideFallback() {
  const playerFallback = $("player-fallback");
  if (playerFallback) playerFallback.classList.add("hidden");
}

window.handlePlayClick = function () {
  const pContainer = $("player");
  if (player && player.playVideo) {
    try {
      player.playVideo();
      if (pContainer) pContainer.classList.add("playing");
    } catch (e) {
      showFallback(0);
    }
  } else {
    showFallback(0);
  }
};

// 2. Playback Tracker & Karaoke Captions
function updateClock() {
  if (!player || !player.getDuration) return;
  try {
    const cur = player.getCurrentTime ? player.getCurrentTime() : 0;
    const dur = player.getDuration() || 0;
    const clockEl = $("clock");
    const fillEl = $("fill");
    if (clockEl) clockEl.textContent = `${fmt(cur)} / ${fmt(dur)}`;
    if (fillEl && dur > 0) {
      fillEl.style.width = `${Math.min(100, (cur / dur) * 100)}%`;
    }
  } catch (e) {}
}

function updatePlaybackState() {
  if (!isPlayerReady || !player || !player.getCurrentTime) return;
  try {
    const cur = player.getCurrentTime();
    updateClock();
    if (currentChunks.length === 0) return;

    let activeIdx = -1;
    for (let i = 0; i < currentChunks.length; i++) {
      if (cur >= currentChunks[i].start_sec && cur <= currentChunks[i].end_sec) {
        activeIdx = i;
        break;
      }
    }

    if (activeIdx !== -1 && activeIdx !== currentChunkIndex) {
      currentChunkIndex = activeIdx;
      highlightChunk(activeIdx, false);
      showKaraokeCaption(activeIdx);
    }
  } catch (e) {}
}

function showKaraokeCaption(idx) {
  const captionEl = $("caption");
  if (!captionEl || !currentChunks[idx]) return;
  clearInterval(wordTimer);
  const text = currentChunks[idx].text;
  const words = text.split(" ");
  captionEl.innerHTML = `<div>${words.map((w) => `<span>${escapeHtml(w)}</span>`).join(" ")}</div>`;
  const spans = [...captionEl.querySelectorAll("span")];
  let k = 0;
  const tick = () => {
    if (k > 0 && spans[k - 1]) spans[k - 1].classList.remove("hot");
    if (k < spans.length) {
      spans[k].classList.add("on", "hot");
      k++;
    } else {
      clearInterval(wordTimer);
    }
  };
  tick();
  const wordInterval = Math.max(120, Math.min(320, Math.floor(4000 / (words.length || 1))));
  wordTimer = setInterval(tick, wordInterval);
}

function highlightChunk(idx, scrollIntoView = true) {
  document.querySelectorAll(".chunk").forEach((el, i) => {
    if (i === idx) {
      el.classList.add("on");
      if (scrollIntoView) {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } else {
      el.classList.remove("on");
    }
  });
}

window.seekToTimestamp = function (seekSec, originalStartSec) {
  const playerFallback = $("player-fallback");
  const pContainer = $("player");
  const statusEl = $("status");
  if (playerFallback && !playerFallback.classList.contains("hidden")) {
    showFallback(seekSec);
    const watchUrl = `https://www.youtube.com/watch?v=${currentVideoId}&t=${Math.floor(seekSec)}s`;
    window.open(watchUrl, "_blank");
    return;
  }

  if (!isPlayerReady || !player) {
    if (statusEl) statusEl.innerHTML = `<b>●</b> Waiting for player to initialize…`;
    return;
  }

  try {
    player.seekTo(seekSec, true);
    player.playVideo();
    if (pContainer) pContainer.classList.add("playing");

    const idx = currentChunks.findIndex(
      (c) => Math.abs(c.start_sec - originalStartSec) < 1.5 || (seekSec >= c.start_sec && seekSec <= c.end_sec)
    );
    if (idx !== -1) {
      currentChunkIndex = idx;
      highlightChunk(idx, true);
      showKaraokeCaption(idx);
    }
  } catch (err) {
    console.warn("seekTo error:", err);
    showFallback(seekSec);
  }
};

// 3. Ingestion Flow & Progress Animation
window.startIngestProgress = function () {
  if (ingestTimerInterval) clearInterval(ingestTimerInterval);
  ingestStartTime = Date.now();

  const ingestProgress = $("ingest-progress");
  const progressHint = $("progress-hint");
  const progressBarFill = $("progress-bar-fill");
  const progressTimer = $("progress-timer");
  const progressStageText = $("progress-stage-text");
  const pstep1 = $("pstep-1");
  const pstep2 = $("pstep-2");
  const pstep3 = $("pstep-3");

  if (ingestProgress) ingestProgress.classList.remove("hidden");
  if (progressHint) progressHint.classList.add("hidden");
  if (progressBarFill) progressBarFill.style.width = "12%";
  if (progressTimer) progressTimer.textContent = "00:00";
  if (progressStageText) progressStageText.textContent = "Connecting & extracting captions…";

  if (pstep1) pstep1.className = "p-step active";
  if (pstep2) pstep2.className = "p-step";
  if (pstep3) pstep3.className = "p-step";

  ingestTimerInterval = setInterval(() => {
    const elapsedSec = Math.floor((Date.now() - ingestStartTime) / 1000);
    if (progressTimer) progressTimer.textContent = fmt(elapsedSec);

    if (elapsedSec < 5) {
      if (progressBarFill) progressBarFill.style.width = `${Math.min(45, 12 + elapsedSec * 7)}%`;
    } else if (elapsedSec >= 5 && elapsedSec < 10) {
      if (progressStageText) progressStageText.textContent = "Extracting captions & subtitles…";
      if (progressBarFill) progressBarFill.style.width = `${Math.min(65, 45 + (elapsedSec - 5) * 4)}%`;
    } else if (elapsedSec >= 10 && elapsedSec < 15) {
      if (pstep1) pstep1.className = "p-step done";
      if (pstep2) pstep2.className = "p-step active";
      if (progressStageText) progressStageText.textContent = "Segmenting transcript into timestamped chunks…";
      if (progressBarFill) progressBarFill.style.width = `${Math.min(85, 65 + (elapsedSec - 10) * 4)}%`;
    } else if (elapsedSec >= 15) {
      if (pstep2) pstep2.className = "p-step done";
      if (pstep3) pstep3.className = "p-step active";
      if (progressStageText) progressStageText.textContent = "Computing semantic embeddings & indexing…";
      if (progressBarFill) progressBarFill.style.width = `${Math.min(94, 85 + (elapsedSec - 15) * 1.5)}%`;
    }

    if (elapsedSec >= 18 && progressHint) {
      progressHint.classList.remove("hidden");
    }
  }, 250);
};

window.stopIngestProgress = function (success, chunkCount = 0) {
  if (ingestTimerInterval) {
    clearInterval(ingestTimerInterval);
    ingestTimerInterval = null;
  }

  const ingestProgress = $("ingest-progress");
  const progressBarFill = $("progress-bar-fill");
  const progressStageText = $("progress-stage-text");
  const pstep1 = $("pstep-1");
  const pstep2 = $("pstep-2");
  const pstep3 = $("pstep-3");

  if (success) {
    const totalSec = ((Date.now() - ingestStartTime) / 1000).toFixed(1);
    if (progressBarFill) progressBarFill.style.width = "100%";
    if (pstep1) pstep1.className = "p-step done";
    if (pstep2) pstep2.className = "p-step done";
    if (pstep3) pstep3.className = "p-step done";
    if (progressStageText) {
      progressStageText.innerHTML = `✓ Indexed <b>${chunkCount} chunks</b> in ${totalSec}s!`;
    }
    setTimeout(() => {
      if (ingestProgress) ingestProgress.classList.add("hidden");
    }, 1500);
  } else {
    if (ingestProgress) ingestProgress.classList.add("hidden");
  }
};

window.setIngestLoading = function (isLoading) {
  const ingestBtn = $("ingest");
  if (ingestBtn) {
    ingestBtn.disabled = isLoading;
    ingestBtn.textContent = isLoading ? "Indexing…" : "Ingest & index";
  }
};

window.ingestVideo = async function () {
  const urlInput = $("url");
  const drawer = $("drawer");
  const manualTextInput = $("manual-text");
  const statusEl = $("status");
  const questionInput = $("q");
  const sendBtn = $("send-btn");

  if (!urlInput) return;
  let url = urlInput.value.trim().replace(/^<|>$/g, "").trim();
  if (!url) {
    if (statusEl) {
      statusEl.className = "status error";
      statusEl.innerHTML = `<b>●</b> Please paste a YouTube link first!`;
    }
    return;
  }

  const isDrawerOpen = drawer && !drawer.classList.contains("hidden");
  const manualText = (isDrawerOpen && manualTextInput) ? manualTextInput.value.trim() : "";

  setIngestLoading(true);
  startIngestProgress();
  if (statusEl) {
    statusEl.className = "status";
    statusEl.innerHTML = `<b>●</b> Indexing video transcript…`;
  }

  try {
    const payload = { url };
    if (manualText) payload.transcript_text = manualText;

    const resp = await fetch(`${API_BASE}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: `Server error (${resp.status}). If waking up, please retry in a few seconds.` }));
      throw new Error(err.detail || `Server returned status ${resp.status}`);
    }

    const data = await resp.json();
    currentVideoId = data.video_id;

    if (statusEl) {
      statusEl.className = "status";
      statusEl.innerHTML = `<b>●</b> Video indexed successfully — ${data.chunk_count} chunks ${data.cached ? "[Cached]" : ""}`;
    }
    if (drawer) drawer.classList.add("hidden");
    if (manualTextInput) manualTextInput.value = "";

    stopIngestProgress(true, data.chunk_count);

    hideFallback();
    if (player && player.loadVideoById) {
      player.loadVideoById(currentVideoId);
    } else if (player && player.cueVideoById) {
      player.cueVideoById(currentVideoId);
    }

    await loadChunks(currentVideoId);

    if (questionInput) questionInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    if (questionInput) questionInput.focus();

    updateSuggestions(currentVideoId);
  } catch (err) {
    stopIngestProgress(false);
    let msg = err.message || "Ingestion request failed.";
    if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
      msg = "Render backend is waking up (takes ~30-45s on free tier after inactivity). Please wait a few seconds and click Ingest again!";
    }
    if (statusEl) {
      statusEl.className = "status error";
      statusEl.innerHTML = `<b>●</b> ${escapeHtml(msg)}`;
    }
    if (drawer) drawer.classList.remove("hidden");
    if (manualTextInput) manualTextInput.focus();
  } finally {
    setIngestLoading(false);
  }
};

window.loadChunks = async function (videoId) {
  try {
    const resp = await fetch(`${API_BASE}/api/chunks/${videoId}`);
    if (!resp.ok) return;
    currentChunks = await resp.json();
    renderTranscript();
  } catch (err) {
    console.error("Failed to load chunks:", err);
  }
};

function renderTranscript() {
  const chunkCountEl = $("chunk-count");
  const listEl = $("list");
  const trackEl = $("track");
  if (!listEl) return;

  if (chunkCountEl) chunkCountEl.textContent = `${currentChunks.length} chunks`;
  listEl.innerHTML = "";

  if (trackEl) {
    trackEl.querySelectorAll("i").forEach((el) => el.remove());
  }

  if (!currentChunks.length) {
    listEl.innerHTML = `<div style="padding: 24px 0; color: var(--mute); font-size: 14px;">No transcript chunks available.</div>`;
    return;
  }

  const dur = (player && player.getDuration && player.getDuration()) || currentChunks[currentChunks.length - 1].end_sec || 300;

  currentChunks.forEach((c, i) => {
    const item = document.createElement("div");
    item.className = "chunk";
    item.dataset.i = i;
    item.innerHTML = `<div class="t">${c.formatted_start}</div><p>${escapeHtml(c.text)}</p>`;
    item.onclick = () => {
      currentChunkIndex = i;
      highlightChunk(i, false);
      seekToTimestamp(Math.max(0, c.start_sec - 2.5), c.start_sec);
    };
    listEl.appendChild(item);

    if (trackEl && dur > 0) {
      const notch = document.createElement("i");
      notch.style.left = `${Math.min(100, (c.start_sec / dur) * 100)}%`;
      trackEl.appendChild(notch);
    }
  });

  if (currentChunks.length > 0) {
    showKaraokeCaption(0);
  }
}

// 4. Drawer & Demo Handlers
window.toggleDrawer = function () {
  const drawer = $("drawer");
  const manualTextInput = $("manual-text");
  if (!drawer) return;
  drawer.classList.toggle("hidden");
  if (!drawer.classList.contains("hidden") && manualTextInput) {
    manualTextInput.focus();
  }
};

window.closeDrawer = function () {
  const drawer = $("drawer");
  if (drawer) drawer.classList.add("hidden");
};

window.loadDemo = function (id) {
  const urlInput = $("url");
  if (urlInput && id) {
    urlInput.value = `https://www.youtube.com/watch?v=${id}`;
    ingestVideo();
  }
};

// 5. Chat Engine
window.addMsg = function (cls, html) {
  const msgsEl = $("msgs");
  if (!msgsEl) return null;
  const m = document.createElement("div");
  m.className = "m " + cls;
  m.innerHTML = html;
  msgsEl.appendChild(m);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return m;
};

window.askQuestion = async function (q) {
  if (!q) return;
  const questionInput = $("q");
  const urlInput = $("url");
  if (!currentVideoId && urlInput && urlInput.value) {
    const match = urlInput.value.match(/(?:v=|\/)([a-zA-Z0-9_-]{11})/);
    currentVideoId = match ? match[1] : "0wp5Z3z4bjM";
  }

  addMsg("u", escapeHtml(q));
  const loading = addMsg("b", '<div class="dots"><i></i><i></i><i></i></div>');

  try {
    const resp = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: currentVideoId,
        question: q,
        history: conversationHistory,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: `Backend returned error ${resp.status}. If waking up, please retry in a moment.` }));
      throw new Error(err.detail || `Server returned error ${resp.status}`);
    }

    const data = await resp.json();
    if (loading) loading.remove();

    let citationsHtml = "";
    if (data.citations && data.citations.length > 0) {
      citationsHtml = `
        <div class="citations-wrap">
          ${data.citations
            .map(
              (c) =>
                `<button type="button" class="ts" title="${escapeHtml(c.snippet_preview)}" onclick="seekToTimestamp(${c.seek_sec}, ${c.start_sec})">${c.label} (${c.formatted_start})</button>`
            )
            .join("")}
        </div>
      `;
    }

    addMsg("b", `<small>According to the video</small>${escapeHtml(data.answer)}${citationsHtml}`);
    conversationHistory.push({ question: q, answer: data.answer });
  } catch (err) {
    if (loading) loading.remove();
    let msg = err.message || "Error generating answer.";
    if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
      msg = "Render backend is waking up (takes ~30s on free tier after sleep). Please retry in a moment!";
    }
    addMsg("b", `<small>Notice</small><span style="color:#ef4444;">${escapeHtml(msg)}</span>`);
  }
};

window.handleChatSubmit = function (e) {
  if (e && e.preventDefault) e.preventDefault();
  const questionInput = $("q");
  if (!questionInput) return;
  const q = questionInput.value.trim();
  if (q) {
    questionInput.value = "";
    askQuestion(q);
  }
};

const SUGGESTIONS_MAP = {
  "0wp5Z3z4bjM": ["What dishes are cooked in this video?", "How is the smoked lamb neck curry made?", "What makes the avocado bhatura puff up?"],
  "aircAruvnKk": ["What is a neural network?", "How are the neuron weights and biases calculated?", "What does the sigmoid activation function do?"],
  "M7lc1UVf-VE": ["What does this video explain about the YouTube API?", "How do you embed a YouTube player with JavaScript?", "What player state events are supported?"],
  "jNQXAC9IVRw": ["What is cool about elephants?", "Where was the video filmed?", "What does the speaker say about trunks?"],
};

window.updateSuggestions = function (vid) {
  const sugEl = $("sug");
  if (!sugEl) return;
  sugEl.innerHTML = "";
  const suggestions = SUGGESTIONS_MAP[vid] || [
    "What is the main topic?",
    "Summarize the key points",
    "Where is the conclusion?",
  ];
  suggestions.forEach((text) => {
    const a = document.createElement("a");
    a.textContent = text;
    a.onclick = () => askQuestion(text);
    sugEl.appendChild(a);
  });
};

// 6. Initialization & Global Wire-up
function init() {
  const playBtn = $("play");
  const trackEl = $("track");
  const chatForm = $("form");
  const urlInput = $("url");
  const toggleDrawerBtn = $("toggle-drawer");
  const closeDrawerBtn = $("close-drawer");
  const ingestBtn = $("ingest");

  if (playBtn) playBtn.onclick = handlePlayClick;
  if (toggleDrawerBtn) toggleDrawerBtn.onclick = toggleDrawer;
  if (closeDrawerBtn) closeDrawerBtn.onclick = closeDrawer;
  if (ingestBtn) ingestBtn.onclick = ingestVideo;

  if (urlInput) {
    urlInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        ingestVideo();
      }
    });
  }

  if (trackEl) {
    trackEl.onclick = (e) => {
      if (!player || !player.getDuration) return;
      const dur = player.getDuration();
      if (!dur || dur <= 0) return;
      const rect = trackEl.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const targetSec = ratio * dur;
      seekToTimestamp(targetSec, targetSec);
    };
  }

  if (chatForm) {
    chatForm.onsubmit = handleChatSubmit;
  }

  document.querySelectorAll(".demos a").forEach((a) => {
    a.onclick = () => {
      const id = a.dataset.id;
      if (id) loadDemo(id);
    };
  });

  currentVideoId = "0wp5Z3z4bjM";
  updateSuggestions("0wp5Z3z4bjM");
  loadChunks("0wp5Z3z4bjM");

  if (API_BASE) {
    fetch(`${API_BASE}/health`)
      .then((r) => r.json())
      .then((d) => console.log("Connected to backend:", d))
      .catch(() => {
        console.log("Render backend is waking up (takes ~30s on free tier)...");
      });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
window.addEventListener("load", init);
