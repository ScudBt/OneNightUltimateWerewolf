"use strict";

const ROLE_EMOJI = {
  werewolf: "🐺", minion: "😈", seer: "🔮", robber: "🦝",
  troublemaker: "🤹", drunk: "🍺", insomniac: "😴", villager: "🧑‍🌾",
};

const $ = (id) => document.getElementById(id);

const state = {
  ws: null,
  humanSeat: -1,
  players: [],        // [{seat, name, avatar, is_human}]
  rounds: 3,
  cards: {},          // seat -> speaker card element
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Illustrated card art with a CSS/emoji fallback when the PNG is missing.
function makeArt(role, sizeClass) {
  const wrap = el("div", "art" + (sizeClass ? " " + sizeClass : ""));
  const img = document.createElement("img");
  img.alt = role;
  img.src = `/static/assets/roles/${role}.png`;
  img.onerror = () => {
    wrap.textContent = ROLE_EMOJI[role] || "🃏";
  };
  wrap.appendChild(img);
  return wrap;
}

function nameFor(seat) {
  const p = state.players.find((x) => x.seat === seat);
  return p ? p.name : `Player ${seat}`;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function typeInto(target, text) {
  target.classList.add("caret");
  for (let i = 0; i < text.length; i++) {
    target.textContent += text[i];
    if (i % 2 === 0) await sleep(12);
  }
  target.classList.remove("caret");
}

function setBanner(text) {
  $("phase-text").textContent = text;
}

function setNarration(text) {
  $("narration").textContent = text;
}

// ---------------------------------------------------------------------------
// Rendering: roster, transcript, input bar
// ---------------------------------------------------------------------------

function renderRoster() {
  const ul = $("roster");
  ul.innerHTML = "";
  for (const p of state.players) {
    const li = el("li");
    li.dataset.seat = p.seat;
    if (p.is_human) li.classList.add("is-you");
    li.appendChild(el("span", "avatar", p.avatar));
    li.appendChild(el("span", "pname", p.name));
    li.appendChild(el("span", "seat-tag", `P${p.seat}`));
    li.appendChild(el("span", "chip status-chip", p.is_human ? "you" : ""));
    ul.appendChild(li);
  }
}

function setRosterChip(seat, text, cls) {
  const li = $("roster").querySelector(`li[data-seat="${seat}"]`);
  if (!li) return;
  const chip = li.querySelector(".status-chip");
  chip.textContent = text;
  chip.className = "chip status-chip" + (cls ? " " + cls : "");
}

function buildTranscriptCards() {
  const box = $("transcript");
  box.innerHTML = "";
  state.cards = {};
  for (const p of state.players) {
    const card = el("div", "speaker-card" + (p.is_human ? " is-you" : ""));
    card.dataset.seat = p.seat;
    const head = el("div", "speaker-head");
    head.appendChild(el("span", "avatar", p.avatar));
    head.appendChild(el("span", "name", p.name));
    head.appendChild(el("span", "seat-tag", `P${p.seat}`));
    head.appendChild(el("span", "typing", ""));
    card.appendChild(head);
    card.appendChild(el("div", "lines"));
    box.appendChild(card);
    state.cards[p.seat] = card;
  }
}

function setActiveSpeaker(seat) {
  for (const c of Object.values(state.cards)) {
    c.classList.remove("active");
    c.querySelector(".typing").textContent = "";
  }
  if (seat != null && state.cards[seat]) {
    const card = state.cards[seat];
    card.classList.add("active");
    card.querySelector(".typing").textContent = "typing…";
  }
}

async function addStatement(seat, roundNum, text) {
  const card = state.cards[seat];
  if (!card) return;
  const lines = card.querySelector(".lines");
  const line = el("div", "line");
  line.appendChild(el("span", "rtag", `R${roundNum}`));
  const body = el("span", "body");
  line.appendChild(body);
  lines.appendChild(line);
  await typeInto(body, text);
  card.classList.remove("active");
  card.querySelector(".typing").textContent = "";
}

function clearInputBar() {
  const bar = $("input-bar");
  bar.innerHTML = "";
  bar.classList.add("hidden");
}

function showInputBar(buildFn) {
  const bar = $("input-bar");
  bar.innerHTML = "";
  bar.classList.remove("hidden");
  buildFn(bar);
}

// ---------------------------------------------------------------------------
// Input prompts
// ---------------------------------------------------------------------------

function promptNight(options) {
  showInputBar((bar) => {
    bar.appendChild(el("div", "prompt", "Your night action:"));
    options.forEach((opt, i) => {
      const btn = el("button", "option-btn", `${i + 1}. ${opt}`);
      btn.onclick = () => {
        send({ type: "night_choice", index: i });
        clearInputBar();
      };
      bar.appendChild(btn);
    });
  });
}

function promptSpeak() {
  showInputBar((bar) => {
    bar.appendChild(el("div", "prompt", "Your turn — say something to the table:"));
    const input = el("input");
    input.type = "text";
    input.placeholder = "Make your case…";
    const sendStatement = () => {
      const text = input.value.trim();
      send({ type: "statement", text: text });
      clearInputBar();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendStatement();
    });
    const btn = el("button", "send", "Send");
    btn.onclick = sendStatement;
    bar.appendChild(input);
    bar.appendChild(btn);
    input.focus();
  });
}

function promptVote(targets) {
  showInputBar((bar) => {
    bar.appendChild(el("div", "prompt", "Vote to eliminate:"));
    const reason = el("input", "vote-reason-input");
    reason.type = "text";
    reason.placeholder = "Why? (optional — recorded for analysis)";
    bar.appendChild(reason);
    targets.forEach((seat) => {
      const btn = el("button", "target-btn", `${nameFor(seat)} (P${seat})`);
      btn.onclick = () => {
        send({ type: "vote", seat: seat, reason: reason.value.trim() });
        clearInputBar();
      };
      bar.appendChild(btn);
    });
  });
}

// ---------------------------------------------------------------------------
// Event handling
// ---------------------------------------------------------------------------

const handlers = {
  game_start(m) {
    state.humanSeat = m.human_seat;
    state.players = m.players;
    state.rounds = m.rounds;
    $("start-screen").classList.add("hidden");
    $("game-screen").classList.remove("hidden");
    $("seed-tag").textContent = `seed ${m.seed} · ${m.provider}`;
    setBanner("Night falls…");
    renderRoster();
    buildTranscriptCards();
    const deck = $("deck-list");
    deck.innerHTML = "";
    m.roles_in_deck.forEach((r) => {
      const label = r.count > 1 ? `${r.role} ×${r.count}` : r.role;
      deck.appendChild(el("span", "deck-chip", `${ROLE_EMOJI[r.role] || ""} ${label}`));
    });
  },

  your_role(m) {
    const card = $("you-card");
    card.innerHTML = "";
    card.appendChild(makeArt(m.role));
    card.appendChild(el("div", "role-name", m.role.toUpperCase()));
    card.appendChild(el("div", "role-intro", m.intro));
    card.querySelector(".role-intro").style.cssText =
      "font-size:12px;color:var(--muted);text-align:center;margin-top:6px";
  },

  night_wake(m) {
    setBanner("Night");
    setNarration(`The ${m.label} wakes…`);
  },

  night_sleep(m) {
    setNarration(`The ${m.role.toUpperCase()} goes back to sleep.`);
  },

  night_result(m) {
    setNarration("Morning arrives. Everyone opens their eyes.");
    const ul = $("you-observations");
    ul.innerHTML = "";
    if (!m.observations.length) {
      ul.appendChild(el("li", "empty", "No night observations."));
    } else {
      m.observations.forEach((o) => ul.appendChild(el("li", "", o)));
    }
  },

  round_start(m) {
    setBanner(`Discussion · Round ${m.round} of ${m.total}`);
    setNarration("");
    state.players.forEach((p) => setRosterChip(p.seat, p.is_human ? "you" : ""));
  },

  speaker_thinking(m) {
    setActiveSpeaker(m.seat);
    setRosterChip(m.seat, "…", "thinking");
    if (m.seat === state.humanSeat) promptSpeak();
  },

  async statement(m) {
    await addStatement(m.seat, m.round, m.text);
    setRosterChip(m.seat, "spoke", "spoke");
  },

  round_summary(m) {
    const box = $("transcript");
    box.appendChild(el("div", "round-summary", `Round ${m.round}: ${m.text}`));
  },

  night_prompt(m) {
    setNarration("Your night action.");
    promptNight(m.options);
  },

  vote_prompt(m) {
    setBanner("Voting");
    setNarration("Cast your vote. All votes reveal at once.");
    promptVote(m.targets);
  },

  votes_revealed(m) {
    setNarration("");
    m.votes.forEach((v) => {
      setRosterChip(v.voter, `→ P${v.target}`, "spoke");
    });
  },

  invalid_input(m) {
    setNarration(m.message);
  },

  reveal(m) {
    renderReveal(m);
  },

  god_summary(m) {
    $("reveal-god").textContent = m.text;
    $("god-panel").classList.remove("hidden");
  },

  human_reaction(m) {
    // Server echo of the saved reaction; the card was already updated locally.
  },

  error(m) {
    // Surface on whichever screen is currently visible.
    if (!$("start-screen").classList.contains("hidden")) {
      $("start-status").textContent = "Error: " + m.message;
    } else {
      setNarration("Error: " + m.message);
    }
  },
};

// The human's own reaction: an editable line on their reveal card. Sent once,
// then frozen into a static reaction so the record matches what was logged.
function buildReactionInput(seat) {
  const wrap = el("div", "reaction-edit");
  const input = el("input", "reaction-input");
  input.type = "text";
  input.placeholder = "Your reaction? (optional)";
  const submit = (text) => {
    send({ type: "reaction", text: text });
    wrap.replaceWith(text ? el("div", "reaction", `“${text}”`) : el("div", "reaction muted", "—"));
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit(input.value.trim());
  });
  const sendBtn = el("button", "reaction-btn", "Save");
  sendBtn.onclick = () => submit(input.value.trim());
  const skipBtn = el("button", "reaction-btn skip", "Skip");
  skipBtn.onclick = () => submit("");
  wrap.appendChild(input);
  wrap.appendChild(sendBtn);
  wrap.appendChild(skipBtn);
  return wrap;
}

function renderReveal(m) {
  $("game-screen").classList.add("hidden");
  $("reveal-screen").classList.remove("hidden");
  $("god-panel").classList.add("hidden");
  $("reveal-god").textContent = "";

  const out = $("reveal-outcome");
  out.textContent = m.human_won ? "You won! " + m.outcome_label : "You lost — " + m.outcome_label;
  out.className = m.human_won ? "win" : "lose";

  const deadNames = m.players.filter((p) => p.died).map((p) => p.name);
  $("reveal-reason").textContent = deadNames.length
    ? `Eliminated: ${deadNames.join(", ")}.`
    : "Nobody was eliminated.";

  const box = $("reveal-cards");
  box.innerHTML = "";
  for (const p of m.players) {
    const isWinner = m.winners.includes(p.seat);
    const card = el("div", "reveal-card" + (p.died ? " died" : "") + (isWinner ? " winner" : ""));
    card.appendChild(makeArt(p.final_role));
    card.appendChild(el("div", "who", p.is_human ? `${p.name} (P${p.seat})` : `${p.name} · P${p.seat}`));
    card.appendChild(el("div", "final", p.final_role.toUpperCase()));
    if (p.final_role !== p.dealt_role) {
      card.appendChild(el("div", "changed", `dealt ${p.dealt_role}`));
    }
    if (p.died) card.appendChild(el("div", "badge dead", "✗ dead"));
    else if (isWinner) card.appendChild(el("div", "badge win", "win"));
    if (p.is_human) {
      card.appendChild(buildReactionInput(p.seat));
    } else if (p.reaction) {
      card.appendChild(el("div", "reaction", `“${p.reaction}”`));
    }
    box.appendChild(card);
  }

  // Vote breakdown: who voted whom, with each voter's stated reason.
  const vbox = $("reveal-votes");
  vbox.innerHTML = "";
  for (const v of (m.votes || [])) {
    const row = el("div", "vote-row");
    const head = el("div", "vote-head");
    const voter = v.is_human ? `You (P${v.voter})` : `${v.voter_name} · P${v.voter}`;
    head.appendChild(el("span", "v-from", `${v.voter_avatar} ${voter}`));
    head.appendChild(el("span", "v-arrow", "→"));
    head.appendChild(el("span", "v-to", `${v.target_avatar} ${v.target_name} · P${v.target}`));
    row.appendChild(head);
    if (v.reason) row.appendChild(el("div", "vote-reason", `“${v.reason}”`));
    vbox.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// WebSocket plumbing
// ---------------------------------------------------------------------------

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

function connectAndStart(opts) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => send(Object.assign({ type: "start_game" }, opts));
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    const h = handlers[msg.type];
    if (h) await h(msg);
  };
  ws.onclose = () => {
    $("start-status").textContent = "Connection closed.";
  };
  ws.onerror = () => {
    $("start-status").textContent = "Connection error.";
  };
}

$("start-btn").onclick = () => {
  const players = parseInt($("player-count").value, 10);
  const provider = $("provider").value;
  const seedRaw = $("seed").value.trim();
  const modelRaw = $("model").value.trim();
  const opts = {
    players,
    provider,
    summaries: $("summaries").checked,
  };
  if (seedRaw !== "") opts.seed = parseInt(seedRaw, 10);
  if (modelRaw !== "") opts.model = modelRaw;
  $("start-status").textContent = "Dealing…";
  connectAndStart(opts);
};

$("play-again").onclick = () => {
  if (state.ws) state.ws.close();
  $("reveal-screen").classList.add("hidden");
  $("start-screen").classList.remove("hidden");
  $("start-status").textContent = "";
};
