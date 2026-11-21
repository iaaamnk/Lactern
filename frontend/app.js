// State
let player = null;
let isPlayerReady = false;
let currentVideoId = "0wp5Z3z4bjM";
let currentChunks = [];
let conversationHistory = [];
let wordTimer = null;
let progressTimer = null;
let currentChunkIndex = -1;

// DOM Elements
const $ = (id) => document.getElementById(id);
const urlInput = $("url");
const ingestBtn = $("ingest");
const toggleDrawerBtn = $("toggle-drawer");
const drawer = $("drawer");
const closeDrawerBtn = $("close-drawer");
const manualTextInput = $("manual-text");
const statusEl = $("status");
const playerContainer = $("player");
const playBtn = $("play");
const captionEl = $("caption");
const playerFallback = $("player-fallback");
const fallbackLink = $("fallback-link");
const trackEl = $("track");
const fillEl = $("fill");
const playerStatus = $("player-status");
const clockEl = $("clock");
const chunkCountEl = $("chunk-count");
const listEl = $("list");
const msgsEl = $("msgs");
const sugEl = $("sug");
const chatForm = $("form");
const questionInput = $("q");
const sendBtn = $("send-btn");

// Progress Bar DOM Elements
const ingestProgress = $("ingest-progress");
const progressStageText = $("progress-stage-text");
const progressTimer = $("progress-timer");
const progressBarFill = $("progress-bar-fill");
const pstep1 = $("pstep-1");
const pstep2 = $("pstep-2");
const pstep3 = $("pstep-3");
const progressHint = $("progress-hint");

// Formatting helper: seconds -> mm:ss
function fmt(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

// 1. YouTube IFrame API
function onYouTubeIframeAPIReady() {
  player = new YT.Player("yt-player", {
    height: "100%",
    width: "100%",
    videoId: "0wp5Z3z4bjM", // Default embeddable starter video
    playerVars: {
      playsinline: 1,
      modestbranding: 1,
      rel: 0,
      enablejsapi: 1,
      origin: window.location.origin,
    },
    events: {
      onReady: onPlayerReady,
      onStateChange: onPlayerStateChange,
      onError: onPlayerError,
    },
  });
}

function onPlayerReady(event) {
  isPlayerReady = true;
  playerStatus.textContent = "Player ready";
  updateClock();
  clearInterval(progressTimer);
  progressTimer = setInterval(updatePlaybackState, 400);

  // Trigger default initial video ingestion in background
  if (!currentVideoId && urlInput.value) {
    ingestVideo();
  }
}

function onPlayerStateChange(event) {
  updateClock();
  if (event.data === YT.PlayerState.PLAYING) {
    playerContainer.classList.add("playing");
  } else if (event.data === YT.PlayerState.PAUSED || event.data === YT.PlayerState.ENDED) {
    playerContainer.classList.remove("playing");
  }
}

function onPlayerError(event) {
  console.warn("YouTube Player event error:", event.data);
  // Errors 101 or 150: embedding disabled by video owner
  if (event.data === 101 || event.data === 150) {
    showFallback(0);
  }
}

function showFallback(seekSec) {
  if (!currentVideoId) return;
  const watchUrl = `https://www.youtube.com/watch?v=${currentVideoId}&t=${Math.floor(seekSec)}s`;
  fallbackLink.href = watchUrl;
  playerFallback.classList.remove("hidden");
}

function hideFallback() {
  playerFallback.classList.add("hidden");
}

// Play button click overlay
playBtn.onclick = () => {
  if (player && player.playVideo) {
    player.playVideo();
    playerContainer.classList.add("playing");
  }
};

// 2. Playback Tracker & Karaoke Captions
function updateClock() {
  if (!player || !player.getDuration) return;
  try {
    const cur = player.getCurrentTime ? player.getCurrentTime() : 0;
    const dur = player.getDuration() || 0;
    clockEl.textContent = `${fmt(cur)} / ${fmt(dur)}`;
    if (dur > 0) {
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

    // Detect active chunk
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
  if (!currentChunks[idx]) return;
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

// 3. Seeking & Timeline
window.seekToTimestamp = function (seekSec, originalStartSec) {
  // If video owner disabled third-party embedding (Error 150)
  if (!playerFallback.classList.contains("hidden")) {
    showFallback(seekSec);
    const watchUrl = `https://www.youtube.com/watch?v=${currentVideoId}&t=${Math.floor(seekSec)}s`;
    window.open(watchUrl, "_blank");
    return;
  }

  if (!isPlayerReady || !player) {
    statusEl.innerHTML = `<b>●</b> Waiting for player to initialize…`;
    return;
  }

  try {
    player.seekTo(seekSec, true);
    player.playVideo();
    playerContainer.classList.add("playing");

    // Match chunk
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

// Timeline track click to seek
trackEl.onclick = (e) => {
  if (!player || !player.getDuration) return;
  const dur = player.getDuration();
  if (!dur || dur <= 0) return;
  const rect = trackEl.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const targetSec = ratio * dur;
  seekToTimestamp(targetSec, targetSec);
};

// 4. Ingestion Flow & Progress Animation
let ingestTimerInterval = null;
let ingestStartTime = 0;

function startIngestProgress() {
  if (ingestTimerInterval) clearInterval(ingestTimerInterval);
  ingestStartTime = Date.now();
  
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

    // Dynamic stage progression
    if (elapsedSec >= 1 && elapsedSec < 3) {
      if (progressBarFill) progressBarFill.style.width = `${Math.min(45, 15 + elapsedSec * 10)}%`;
    } else if (elapsedSec >= 3 && elapsedSec < 6) {
      if (pstep1) pstep1.className = "p-step done";
      if (pstep2) pstep2.className = "p-step active";
      if (progressStageText) progressStageText.textContent = "Segmenting transcript into timestamped chunks…";
      if (progressBarFill) progressBarFill.style.width = `${Math.min(75, 45 + (elapsedSec - 3) * 10)}%`;
    } else if (elapsedSec >= 6) {
      if (pstep2) pstep2.className = "p-step done";
      if (pstep3) pstep3.className = "p-step active";
      if (progressStageText) progressStageText.textContent = "Computing semantic embeddings & indexing…";
      if (progressBarFill) progressBarFill.style.width = `${Math.min(94, 75 + (elapsedSec - 6) * 3)}%`;
    }

    // If waiting longer than 10 seconds, reveal helpful fallback hint
    if (elapsedSec >= 10 && progressHint) {
      progressHint.classList.remove("hidden");
    }
  }, 250);
}

function stopIngestProgress(success, chunkCount = 0) {
  if (ingestTimerInterval) {
    clearInterval(ingestTimerInterval);
    ingestTimerInterval = null;
  }
  
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
}

async function ingestVideo() {
  const url = urlInput.value.trim();
  if (!url) return;

  const manualText = manualTextInput ? manualTextInput.value.trim() : "";
  setIngestLoading(true);
  startIngestProgress();
  statusEl.className = "status";
  statusEl.innerHTML = `<b>●</b> Indexing video transcript…`;

  try {
    const payload = { url };
    if (manualText) payload.transcript_text = manualText;

    const resp = await fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "Ingestion failed." }));
      throw new Error(err.detail || "Ingestion failed.");
    }

    const data = await resp.json();
    currentVideoId = data.video_id;

    statusEl.className = "status";
    statusEl.innerHTML = `<b>●</b> Video indexed successfully — ${data.chunk_count} chunks ${data.cached ? "[Cached]" : ""}`;
    drawer.classList.add("hidden");

    stopIngestProgress(true, data.chunk_count);

    // Load video in YouTube player
    hideFallback();
    if (player && player.loadVideoById) {
      player.loadVideoById(currentVideoId);
    } else if (player && player.cueVideoById) {
      player.cueVideoById(currentVideoId);
    }

    // Load transcript chunks
    await loadChunks(currentVideoId);

    // Enable chat inputs
    questionInput.disabled = false;
    sendBtn.disabled = false;
    questionInput.focus();

    // Populate suggested questions
    updateSuggestions(currentVideoId);
  } catch (err) {
    stopIngestProgress(false);
    statusEl.className = "status error";
    statusEl.innerHTML = `<b>●</b> ${escapeHtml(err.message)}`;
    drawer.classList.remove("hidden");
    if (manualTextInput) manualTextInput.focus();
  } finally {
    setIngestLoading(false);
  }
}

async function loadChunks(videoId) {
  try {
    const resp = await fetch(`/api/chunks/${videoId}`);
    if (!resp.ok) return;
    currentChunks = await resp.json();
    renderTranscript();
  } catch (err) {
    console.error("Failed to load chunks:", err);
  }
}

function renderTranscript() {
  chunkCountEl.textContent = `${currentChunks.length} chunks`;
  listEl.innerHTML = "";
  
  // Clear existing notches on track
  trackEl.querySelectorAll("i").forEach((el) => el.remove());

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

    // Track notch
    if (dur > 0) {
      const notch = document.createElement("i");
      notch.style.left = `${Math.min(100, (c.start_sec / dur) * 100)}%`;
      trackEl.appendChild(notch);
    }
  });

  // Display initial caption
  if (currentChunks.length > 0) {
    showKaraokeCaption(0);
  }
}

function setIngestLoading(isLoading) {
  ingestBtn.disabled = isLoading;
  ingestBtn.textContent = isLoading ? "Indexing…" : "Ingest & index";
}

// 5. Chat Flow
function addMsg(cls, html) {
  const m = document.createElement("div");
  m.className = "m " + cls;
  m.innerHTML = html;
  msgsEl.appendChild(m);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return m;
}

// Initial welcome message
addMsg("b", "<small>Assistant</small>Ask me anything — I’ll take you to the exact second.");

async function askQuestion(q) {
  if (!q) return;

  if (!currentVideoId) {
    const match = urlInput.value.match(/(?:v=|\/)([0-9A-Za-z_-]{11})/);
    currentVideoId = match ? match[1] : "LxCjscf2PjA";
  }

  addMsg("u", escapeHtml(q));
  const loading = addMsg("b", '<div class="dots"><i></i><i></i><i></i></div>');

  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: currentVideoId,
        question: q,
        history: conversationHistory,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Error generating answer.");
    }

    const data = await resp.json();
    loading.remove();

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
    loading.remove();
    addMsg("b", `<small>Error</small><span style="color:#ef4444;">${escapeHtml(err.message)}</span>`);
  }
}

chatForm.onsubmit = (e) => {
  e.preventDefault();
  const q = questionInput.value.trim();
  if (q) {
    questionInput.value = "";
    askQuestion(q);
  }
};

// 6. Suggestions Engine
const SUGGESTIONS_MAP = {
  "0wp5Z3z4bjM": ["What dishes are cooked in this video?", "How is the smoked lamb neck curry made?", "What makes the avocado bhatura puff up?"],
  GvrIe41E7xA: ["Why does Sheldon refuse to answer the phone?", "What game are Leonard and Sheldon playing?", "Why is Sheldon angry at Tam?"],
  LxCjscf2PjA: ["How does Gordon prepare the eggs?", "What is the secret with the butter?", "When should you season the eggs?"],
  jNQXAC9IVRw: ["What is cool about elephants?", "Where was the video filmed?", "What does the speaker say about trunks?"],
};

function updateSuggestions(vid) {
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
}

// 7. Demo Chips & Drawer Handlers
document.querySelectorAll(".demos a").forEach((a) => {
  a.onclick = () => {
    const id = a.dataset.id;
    if (id) {
      urlInput.value = `https://www.youtube.com/watch?v=${id}`;
      ingestVideo();
    }
  };
});

toggleDrawerBtn.onclick = () => {
  drawer.classList.toggle("hidden");
  if (!drawer.classList.contains("hidden")) {
    manualTextInput.focus();
  }
};

closeDrawerBtn.onclick = () => {
  drawer.classList.add("hidden");
};

ingestBtn.onclick = () => ingestVideo();

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

// Immediate boot on DOM ready so page is never empty or bland
window.addEventListener("DOMContentLoaded", () => {
  currentVideoId = "0wp5Z3z4bjM";
  updateSuggestions("0wp5Z3z4bjM");
  loadChunks("0wp5Z3z4bjM");
});

