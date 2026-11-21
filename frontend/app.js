// State
let player = null;
let isPlayerReady = false;
let currentVideoId = null;
let currentChunks = [];
let conversationHistory = [];

const statusBanner = document.getElementById("status-banner");
const statusMessage = document.getElementById("status-message");
const playerStatusDot = document.getElementById("player-status-dot");
const playerStatusText = document.getElementById("player-status-text");
const currentTimeDisplay = document.getElementById("current-time");
const totalDurationDisplay = document.getElementById("total-duration");
const playerFallback = document.getElementById("player-fallback");
const fallbackLink = document.getElementById("fallback-link");
const transcriptList = document.getElementById("transcript-list");
const chunkCounter = document.getElementById("chunk-counter");

const videoUrlInput = document.getElementById("video-url-input");
const ingestBtn = document.getElementById("ingest-btn");
const toggleManualTranscriptBtn = document.getElementById("toggle-manual-transcript-btn");
const manualTranscriptDrawer = document.getElementById("manual-transcript-drawer");
const closeDrawerBtn = document.getElementById("close-drawer-btn");
const manualTranscriptInput = document.getElementById("manual-transcript-input");
const questionInput = document.getElementById("question-input");
const sendBtn = document.getElementById("send-btn");
const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");

if (toggleManualTranscriptBtn) {
  toggleManualTranscriptBtn.addEventListener("click", () => {
    manualTranscriptDrawer.classList.toggle("hidden");
    if (!manualTranscriptDrawer.classList.contains("hidden")) {
      manualTranscriptInput.focus();
    }
  });
}

if (closeDrawerBtn) {
  closeDrawerBtn.addEventListener("click", () => {
    manualTranscriptDrawer.classList.add("hidden");
  });
}

// 1. YouTube IFrame API Initialization
function onYouTubeIframeAPIReady() {
  console.log("YouTube IFrame API loaded.");
  player = new YT.Player("player", {
    height: "100%",
    width: "100%",
    videoId: "jNQXAC9IVRw", // Default placeholder
    playerVars: {
      playsinline: 1,
      modestbranding: 1,
      rel: 0,
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
  playerStatusDot.classList.add("ready");
  playerStatusText.textContent = "Player Ready";
  updatePlaybackTimer();
  updateChipStates();
  setInterval(trackPlaybackProgress, 500);
}

function onPlayerStateChange(event) {
  updatePlaybackTimer();
}

function onPlayerError(event) {
  console.warn("YouTube Player error:", event.data);
  // Errors 101 or 150 mean embedding was disabled by video owner
  if (event.data === 101 || event.data === 150) {
    showFallbackLink(0);
  }
}

function showFallbackLink(seconds) {
  if (!currentVideoId) return;
  const watchUrl = `https://www.youtube.com/watch?v=${currentVideoId}&t=${Math.floor(seconds)}s`;
  fallbackLink.href = watchUrl;
  playerFallback.classList.remove("hidden");
}

function hideFallbackLink() {
  playerFallback.classList.add("hidden");
}

function formatSeconds(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function updatePlaybackTimer() {
  if (player && player.getDuration) {
    const dur = player.getDuration();
    if (dur > 0) {
      totalDurationDisplay.textContent = formatSeconds(dur);
    }
  }
}

function trackPlaybackProgress() {
  if (!isPlayerReady || !player || !player.getCurrentTime) return;
  try {
    const curr = player.getCurrentTime();
    currentTimeDisplay.textContent = formatSeconds(curr);

    // Highlight current active chunk in transcript
    if (currentChunks.length > 0) {
      currentChunks.forEach((c) => {
        const el = document.getElementById(`chunk-card-${c.chunk_id}`);
        if (el) {
          if (curr >= c.start_sec && curr <= c.end_sec) {
            el.classList.add("active");
          } else {
            el.classList.remove("active");
          }
        }
      });
    }
  } catch (e) {
    // Player might be switching videos
  }
}

function updateChipStates() {
  const chips = document.querySelectorAll(".timestamp-chip");
  chips.forEach((chip) => {
    if (isPlayerReady) {
      chip.classList.remove("disabled");
    } else {
      chip.classList.add("disabled");
    }
  });
}

// 2. Clickable Timestamp Jump Function (Phase 7 Done Condition)
window.seekToTimestamp = function (seekSec, originalStartSec) {
  if (!isPlayerReady || !player) {
    alert("Player is still loading. Please wait a moment.");
    return;
  }

  hideFallbackLink();
  try {
    // Safeguard: seek with 2-3s lead-in
    player.seekTo(seekSec, true);
    player.playVideo();

    // Scroll active transcript item into view
    const matchingChunk = currentChunks.find((c) => Math.abs(c.start_sec - originalStartSec) < 1.0);
    if (matchingChunk) {
      const card = document.getElementById(`chunk-card-${matchingChunk.chunk_id}`);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  } catch (err) {
    console.error("seekTo failed:", err);
    showFallbackLink(seekSec);
  }
};

// 3. Ingestion Flow
ingestBtn.addEventListener("click", async () => {
  const url = videoUrlInput.value.trim();
  if (!url) return;

  const transcriptText = manualTranscriptInput ? manualTranscriptInput.value.trim() : "";

  setIngesting(true);
  showStatus("Ingesting video captions and building time-aware chunks...", false);

  try {
    const payload = { url };
    if (transcriptText) {
      payload.transcript_text = transcriptText;
    }

    const resp = await fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Ingestion failed.");
    }

    const data = await resp.json();
    currentVideoId = data.video_id;

    showStatus(`Video indexed successfully (${data.chunk_count} chunks). ${data.cached ? "[Cached]" : ""}`, false);
    if (manualTranscriptDrawer) {
      manualTranscriptDrawer.classList.add("hidden");
    }

    // Load video in YouTube player
    if (player && player.cueVideoById) {
      hideFallbackLink();
      player.cueVideoById(currentVideoId);
    }

    // Fetch chunks for transcript viewer
    await loadTranscriptChunks(currentVideoId);

    // Enable chat inputs
    questionInput.disabled = false;
    sendBtn.disabled = false;
    questionInput.focus();
  } catch (err) {
    showStatus(`Error: ${err.message}`, true);
    // If rate-limited or Whisper failed, reveal manual transcript drawer automatically
    if (manualTranscriptDrawer) {
      manualTranscriptDrawer.classList.remove("hidden");
      if (manualTranscriptInput) {
        manualTranscriptInput.focus();
      }
    }
  } finally {
    setIngesting(false);
  }
});

async function loadTranscriptChunks(videoId) {
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
  chunkCounter.textContent = `${currentChunks.length} chunks`;
  if (!currentChunks.length) {
    transcriptList.innerHTML = `<p class="empty-state">No chunks available.</p>`;
    return;
  }

  transcriptList.innerHTML = currentChunks
    .map(
      (c) => `
    <div id="chunk-card-${c.chunk_id}" class="transcript-card" onclick="seekToTimestamp(${Math.max(0, c.start_sec - 2.5)}, ${c.start_sec})">
      <div class="chunk-meta">
        <span class="chunk-time-tag">${c.chunk_id} • ${c.formatted_start} - ${c.formatted_end}</span>
      </div>
      <p class="chunk-text">${c.text}</p>
    </div>
  `
    )
    .join("");
}

// 4. Q&A Ask Flow
chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = questionInput.value.trim();
  if (!q || !currentVideoId) return;

  questionInput.value = "";
  appendUserMessage(q);

  const loadingMsgId = appendAssistantLoading();

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
    replaceLoadingWithAnswer(loadingMsgId, data.answer, data.citations);

    conversationHistory.push({ question: q, answer: data.answer });
  } catch (err) {
    replaceLoadingWithError(loadingMsgId, err.message);
  }
});

function appendUserMessage(text) {
  const msg = document.createElement("div");
  msg.className = "message user-msg";
  msg.innerHTML = `<div class="msg-bubble"><p>${escapeHtml(text)}</p></div>`;
  chatMessages.appendChild(msg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendAssistantLoading() {
  const id = `loading-${Date.now()}`;
  const msg = document.createElement("div");
  msg.id = id;
  msg.className = "message assistant-msg";
  msg.innerHTML = `<div class="msg-bubble"><p>Searching chunks and generating grounded answer...</p></div>`;
  chatMessages.appendChild(msg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return id;
}

function replaceLoadingWithAnswer(loadingId, answerText, citations) {
  const loadingEl = document.getElementById(loadingId);
  if (!loadingEl) return;

  let citationsHtml = "";
  if (citations && citations.length > 0) {
    citationsHtml = `
      <div class="citation-container">
        ${citations
          .map(
            (c) => `
          <button 
            type="button"
            class="timestamp-chip ${isPlayerReady ? "" : "disabled"}" 
            title="${escapeHtml(c.snippet_preview)}"
            onclick="seekToTimestamp(${c.seek_sec}, ${c.start_sec})"
          >
            ${c.label}
          </button>
        `
          )
          .join("")}
      </div>
    `;
  }

  loadingEl.innerHTML = `
    <div class="msg-bubble">
      <p>${escapeHtml(answerText)}</p>
      ${citationsHtml}
    </div>
  `;
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function replaceLoadingWithError(loadingId, errMsg) {
  const loadingEl = document.getElementById(loadingId);
  if (!loadingEl) return;
  loadingEl.innerHTML = `<div class="msg-bubble"><p style="color: #f87171;">Error: ${escapeHtml(errMsg)}</p></div>`;
}

function showStatus(msg, isError) {
  statusBanner.classList.remove("hidden");
  statusBanner.className = isError ? "status-banner error" : "status-banner";
  statusMessage.textContent = msg;
}

function setIngesting(loading) {
  ingestBtn.disabled = loading;
  const text = ingestBtn.querySelector(".btn-text");
  if (text) text.textContent = loading ? "Indexing..." : "Ingest & Index";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
