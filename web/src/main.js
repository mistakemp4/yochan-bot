import { DiscordSDK } from "@discord/embedded-app-sdk";

// sides: N=1 E=2 S=4 W=8
const DIRS = [[1, 0, -1], [2, 1, 0], [4, 0, 1], [8, -1, 0]];
const rot = m => ((m << 1) | (m >> 3)) & 15;
const ANIM = { turn: 100, steps: 3, water: 160, foam: 120 };
const COL = { sand: "#e8cf9a", sandDark: "#d9bb80", lip: "#f4e2b8", hole: "#8c6a3c", dug: "#b48c56", water: "#3aa3d6", waterHi: "#7fd0f2", sea: "#3c9fd1", foam: "#f4fbff", focus: "#e0457b" };
const T = 16, S = 6, ROUND_MS = 60000;

const $ = id => document.getElementById(id);
const cv = $("board"), msg = t => ($("msg").textContent = t);
const AVATAR = new Image();
AVATAR.src = "/assets/yochan.jpg";

/* ---------- talking to the server ---------- */
let accessToken = "dev";
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}
let sdk = null;
async function connect() {
  const cfg = await (await fetch("/api/config")).json();
  // outside Discord (no frame_id) only dev mode works
  if (!new URLSearchParams(location.search).has("frame_id")) {
    if (!cfg.dev) throw new Error("Open this from Discord.");
    return;
  }
  sdk = new DiscordSDK(cfg.client_id);
  await sdk.ready();
  const { code } = await sdk.commands.authorize({ client_id: cfg.client_id, response_type: "code", state: "", prompt: "none", scope: ["identify"] });
  accessToken = (await api("/api/token", { code })).access_token;
  const auth = await sdk.commands.authenticate({ access_token: accessToken });
  if (!auth) throw new Error("Discord login failed");
}

/* ---------- drawing (16px tiles, scaled 6x with no smoothing) ---------- */
function geom(p) {
  const PADL = Math.round(T * .8), PADR = Math.round(T * 1.7), PADY = Math.round(T * .25);
  return { PADL, PADR, PADY, W: PADL + p.w * T + PADR, H: p.h * T + PADY * 2 };
}
let sandCv = null;
function sand(W, H) {
  if (sandCv && sandCv.width === W) return sandCv;
  sandCv = document.createElement("canvas"); sandCv.width = W; sandCv.height = H;
  const c = sandCv.getContext("2d");
  c.fillStyle = COL.sand; c.fillRect(0, 0, W, H);
  let seed = 7; const r = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < W * H / 7; i++) { c.fillStyle = r() < .6 ? COL.sandDark : COL.lip; c.fillRect(Math.floor(r() * W), Math.floor(r() * H), 1, 1); }
  return sandCv;
}
const tiles = {};
function tile(m, wet, ripple) {
  const key = `${m}|${wet}|${ripple}`;
  if (tiles[key]) return tiles[key];
  const t = document.createElement("canvas"); t.width = t.height = 16;
  const c = t.getContext("2d");
  const rect = (x, y, w, h, col) => { c.fillStyle = col; c.fillRect(x, y, w, h); };
  // lip 8px, hole 6px, inside 4px; inside drops 1px on sideways arms for a shadow
  for (const [w, col, sk] of [[8, COL.lip, 0], [6, COL.hole, 0], [4, wet ? COL.water : COL.dug, 1]]) {
    const a = 8 - w / 2;
    rect(a, a + sk, w, w, col);
    if (m & 1) rect(a, 0, w, 8 + sk, col);
    if (m & 4) rect(a, 8, w, 8, col);
    if (m & 8) rect(0, a + sk, 8, w, col);
    if (m & 2) rect(8, a + sk, 8, w, col);
  }
  if (wet) {
    c.fillStyle = COL.waterHi;
    for (const [x, y] of ripple ? [[7, 9], [9, 7]] : [[6, 8], [9, 10]]) c.fillRect(x, y, 1, 1);
    if (m & 2) c.fillRect(ripple ? 12 : 13, 9, 1, 1);
    if (m & 8) c.fillRect(ripple ? 3 : 2, 9, 1, 1);
    if (m & 1) c.fillRect(7, ripple ? 3 : 2, 1, 1);
    if (m & 4) c.fillRect(8, ripple ? 13 : 12, 1, 1);
  }
  return (tiles[key] = t);
}
const lo = document.createElement("canvas");
function draw(p, st, o) {
  const { PADL, PADY, W, H } = geom(p);
  lo.width = W; lo.height = H;
  const c = lo.getContext("2d");
  c.imageSmoothingEnabled = false;
  const px1 = (x, y, col) => { c.fillStyle = col; c.fillRect(x, y, 1, 1); };
  const rect = (x, y, w, h, col) => { c.fillStyle = col; c.fillRect(x, y, w, h); };
  const disc = (x0, y0, r, col) => { for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) if (x * x + y * y <= r * r + r * .8) px1(x0 + x, y0 + y, col); };
  const f = o.frame, wet = st.wet || new Set();
  c.drawImage(sand(W, H), 0, 0);
  const boardR = PADL + p.w * T, inY = PADY + p.r0 * T + 8, outY = PADY + p.r1 * T + 8;
  for (let y = 0; y < H; y++) { const e = 4 + (Math.floor(y / 3) % 2); rect(0, y, e, 1, COL.sea); px1(e, y, COL.foam); }
  if (o.hover && !st.locked) { const [x, y] = o.hover; rect(PADL + x * T, PADY + y * T, T, T, "rgba(255,255,255,.25)"); }
  // pool + feeding channel, layered so no rim crosses the join
  const pr = 10, pcx = boardR + 3 + pr + 1, pcy = Math.max(pr + 2, Math.min(H - pr - 3, outY));
  const poolCol = st.won ? COL.water : COL.dug;
  disc(pcx, pcy, pr + 1, COL.lip); rect(boardR, outY - 4, pcx - boardR, 8, COL.lip);
  disc(pcx, pcy, pr, COL.hole); rect(boardR, outY - 3, pcx - boardR, 6, COL.hole);
  disc(pcx, pcy + 1, pr - 1, poolCol); rect(boardR, outY - 1, pcx - boardR, 4, poolCol);
  // inlet rows start at that row's foam pixel so sea and channel touch
  for (let y = inY - 4; y < inY + 4; y++) {
    const x0 = 5 + (Math.floor(y / 3) % 2);
    rect(x0, y, PADL - x0, 1, y >= inY - 1 && y <= inY + 2 ? COL.water : y >= inY - 3 ? COL.hole : COL.lip);
  }
  for (let y = 0; y < p.h; y++) for (let x = 0; x < p.w; x++) {
    const k = x + "," + y, t = tile(p.grid[y][x], wet.has(k), f % 2), X = PADL + x * T, Y = PADY + y * T;
    c.save(); c.beginPath(); c.rect(X, Y, T, T); c.clip();
    const a = o.anim[k];
    if (a !== undefined) { const q = ANIM.steps ? Math.ceil(a * ANIM.steps) / ANIM.steps : a; c.translate(X + 8, Y + 8); c.rotate(-q * Math.PI / 2); c.drawImage(t, -8, -8); }
    else c.drawImage(t, X, Y);
    c.restore();
  }
  // foam on the open sides of the newest wet tile
  if (st.front) {
    const [x, y] = st.front, m = p.grid[y][x];
    for (const [bit, dx, dy] of DIRS) {
      if (!(m & bit) || wet.has((x + dx) + "," + (y + dy))) continue;
      const d = 5 + (f % 2), bx = PADL + x * T + 8 + dx * d, by = PADY + y * T + 9 + dy * d;
      const shape = f % 2 ? [[0, 0], [1, -1], [-1, 1], [0, 2]] : [[0, -1], [1, 1], [-1, 0], [1, 0]];
      for (const [a, b] of shape) px1(bx + (dy ? a : b), by + (dy ? b : a), COL.foam);
    }
  }
  if (st.splash) for (let i = 0; i < 8; i++) {
    const a = i / 8 * Math.PI * 2 + (f % 2) * .4;
    px1(Math.round(pcx + Math.cos(a) * (pr - 2)), Math.round(pcy + 1 + Math.sin(a) * (pr - 2)), COL.foam);
  }
  if (o.focus) { const [x, y] = o.focus; c.strokeStyle = COL.focus; c.lineWidth = 1; c.strokeRect(PADL + x * T + .5, PADY + y * T + .5, T - 1, T - 1); }
  if (cv.width !== W * S) { cv.width = W * S; cv.height = H * S; }
  const out = cv.getContext("2d");
  out.imageSmoothingEnabled = false;
  out.drawImage(lo, 0, 0, W * S, H * S);
  // her photo stays smooth on top
  if (AVATAR.complete && AVATAR.naturalWidth) {
    const r = 7.5 * S, ax = (pcx + .5) * S, ay = (pcy + .5) * S;
    out.save(); out.imageSmoothingEnabled = true; out.beginPath(); out.arc(ax, ay, r, 0, Math.PI * 2); out.clip();
    if (!st.won) out.filter = "grayscale(0.7) sepia(0.3) brightness(0.85)";
    out.drawImage(AVATAR, ax - r, ay - r, r * 2, r * 2); out.restore();
  }
}

/* ---------- one round ---------- */
let game = null;
function play(p) {
  const anim = {};
  const g = { p, st: {}, locked: false, hover: null, focus: [0, p.r0], frame: 0, timers: [], raf: 0, deadline: Date.now() + ROUND_MS };
  game = g;
  const paint = () => draw(p, { ...g.st, locked: g.locked }, { hover: g.hover, focus: document.activeElement === cv ? g.focus : null, anim, frame: g.frame });
  const later = (fn, ms) => g.timers.push(setTimeout(fn, ms));
  const every = (fn, ms) => g.timers.push(setInterval(fn, ms));
  const stop = () => { g.timers.forEach(t => { clearTimeout(t); clearInterval(t); }); g.timers = []; };
  g.stop = stop;
  const tick = t0 => {
    g.raf = requestAnimationFrame(() => {
      const t = performance.now(); let live = false;
      for (const k in anim) { anim[k] = Math.max(0, anim[k] - (t - t0) / ANIM.turn); if (anim[k] > 0) live = true; else delete anim[k]; }
      paint(); if (live) tick(t);
    });
  };
  const turn = ([x, y]) => {
    if (g.locked) return;
    p.grid[y][x] = rot(p.grid[y][x]); anim[x + "," + y] = 1;
    cancelAnimationFrame(g.raf); tick(performance.now());
  };
  const cellAt = e => {
    const { PADL, PADY, W } = geom(p), r = cv.getBoundingClientRect(), sc = W / r.width;
    const x = Math.floor(((e.clientX - r.left) * sc - PADL) / T), y = Math.floor(((e.clientY - r.top) * sc - PADY) / T);
    return x >= 0 && x < p.w && y >= 0 && y < p.h ? [x, y] : null;
  };
  cv.onclick = e => { const c = cellAt(e); if (c) { g.focus = c; turn(c); } };
  cv.onmousemove = e => { const c = cellAt(e); cv.style.cursor = c && !g.locked ? "pointer" : "default"; if (String(c) !== String(g.hover)) { g.hover = c; paint(); } };
  cv.onmouseleave = () => { g.hover = null; paint(); };
  cv.onfocus = cv.onblur = paint;
  cv.onkeydown = e => {
    let [x, y] = g.focus;
    const mv = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (mv) { g.focus = [Math.min(p.w - 1, Math.max(0, x + mv[0])), Math.min(p.h - 1, Math.max(0, y + mv[1]))]; paint(); e.preventDefault(); }
    else if (e.key === " " || e.key === "Enter") { turn(g.focus); e.preventDefault(); }
  };
  $("go").disabled = false; $("go").onclick = release;
  $("gain").textContent = "";
  msg("Click a pipe to turn it. Get the water from the sea to her pool.");
  paint();
  every(() => {
    const left = Math.max(0, g.deadline - Date.now());
    $("time").textContent = `${Math.ceil(left / 1000)}s`;
    $("bar").style.width = `${left / ROUND_MS * 100}%`;
    if (!left) release();
  }, 250);

  async function release() {
    if (g.locked) return;
    g.locked = true; $("go").disabled = true; stop();
    msg("Water's coming…");
    let res;
    try { res = await api("/api/result", { id: p.id, grid: p.grid }); }
    catch { msg("Couldn't reach Yo-chan. Try a new pool."); return; }
    // tile by tile with foam at the front, the order comes from the server
    const shown = new Set();
    every(() => { g.frame++; paint(); }, ANIM.foam);
    res.order.forEach(([x, y], i) => later(() => { shown.add(x + "," + y); g.st = { wet: shown, front: [x, y] }; paint(); }, (i + 1) * ANIM.water));
    later(() => {
      stop();
      g.st = { wet: shown, won: res.won, splash: res.won }; paint();
      if (res.won) { every(() => { g.frame++; paint(); }, ANIM.foam + 20); later(() => { stop(); g.st = { wet: shown, won: true }; paint(); }, 1700); }
      msg(res.won ? `Splash! ${res.tiles} tiles of water.` : "It leaked. Try a new pool?");
      if (res.gain) $("gain").textContent = `+${Math.round(res.gain)}% enrichment`;
    }, (res.order.length + 1) * ANIM.water);
  }
}

async function newPool() {
  game?.stop?.(); cancelAnimationFrame(game?.raf);
  $("go").disabled = true;
  msg("Digging a new pool…");
  try {
    const p = await api("/api/new", { guild_id: sdk?.guildId ?? null, channel_id: sdk?.channelId ?? null });
    play(p);
  } catch { msg("Couldn't reach Yo-chan. Close and open the game again."); }
}
$("fresh").onclick = newPool;

connect().then(() => { $("fresh").disabled = false; return newPool(); }).catch(e => msg(e.message || "Couldn't connect."));
