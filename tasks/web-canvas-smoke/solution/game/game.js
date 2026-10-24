"use strict";

const WIDTH = 1280;
const HEIGHT = 720;
const STEP_SECONDS = 1 / 30;
const SPEED = 240;
const PLAYER_RADIUS = 18;
const SHARD_RADIUS = 14;

const canvas = document.getElementById("game");
const context = canvas.getContext("2d");
const startButton = document.getElementById("start-button");
const keys = new Set();
const player = { x: 160, y: 500, facingX: 1, facingY: 0 };
const shards = [
  { x: 320, y: 500 },
  { x: 480, y: 500 },
  { x: 720, y: 500 }
];
const collected = new Set();
const controlledKeys = new Set([
  "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
  "KeyA", "KeyD", "KeyW", "KeyS"
]);

let mode = "title";
let previousTime = null;
let accumulator = 0;

function startGame() {
  mode = "playing";
  player.x = 160;
  player.y = 500;
  player.facingX = 1;
  player.facingY = 0;
  collected.clear();
  keys.clear();
  startButton.hidden = true;
}

function update(dt) {
  if (mode !== "playing") {
    return;
  }

  let dx = 0;
  let dy = 0;
  if (keys.has("ArrowLeft") || keys.has("KeyA")) dx -= 1;
  if (keys.has("ArrowRight") || keys.has("KeyD")) dx += 1;
  if (keys.has("ArrowUp") || keys.has("KeyW")) dy -= 1;
  if (keys.has("ArrowDown") || keys.has("KeyS")) dy += 1;

  if (dx !== 0 || dy !== 0) {
    const length = Math.hypot(dx, dy);
    dx /= length;
    dy /= length;
    player.x = Math.max(48, Math.min(1232, player.x + dx * SPEED * dt));
    player.y = Math.max(160, Math.min(672, player.y + dy * SPEED * dt));
    player.facingX = dx;
    player.facingY = dy;
  }

  shards.forEach((shard, index) => {
    if (collected.has(index)) return;
    const distance = Math.hypot(player.x - shard.x, player.y - shard.y);
    if (distance <= PLAYER_RADIUS + SHARD_RADIUS) {
      collected.add(index);
    }
  });
}

function roundedRect(x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
}

function drawBackground() {
  const gradient = context.createLinearGradient(0, 0, WIDTH, HEIGHT);
  gradient.addColorStop(0, "#101a38");
  gradient.addColorStop(0.55, "#090f24");
  gradient.addColorStop(1, "#050816");
  context.fillStyle = gradient;
  context.fillRect(0, 0, WIDTH, HEIGHT);

  context.strokeStyle = "rgba(108, 142, 205, 0.09)";
  context.lineWidth = 1;
  for (let x = 0; x <= WIDTH; x += 40) {
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, HEIGHT);
    context.stroke();
  }
  for (let y = 0; y <= HEIGHT; y += 40) {
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(WIDTH, y);
    context.stroke();
  }
}

function drawTitle() {
  context.textAlign = "center";
  context.fillStyle = "#8fffe9";
  context.font = "700 18px system-ui, sans-serif";
  context.letterSpacing = "5px";
  context.fillText("NIGHT SHIFT ARCADE", WIDTH / 2, 150);
  context.letterSpacing = "0px";

  context.shadowColor = "rgba(64, 231, 214, 0.4)";
  context.shadowBlur = 28;
  context.fillStyle = "#f4f7ff";
  context.font = "800 72px system-ui, sans-serif";
  context.fillText("CANVAS COURIER", WIDTH / 2, 255);
  context.shadowBlur = 0;

  context.fillStyle = "#aebbd8";
  context.font = "400 25px system-ui, sans-serif";
  context.fillText("A tiny keyboard-powered pickup run", WIDTH / 2, 310);

  roundedRect(395, 525, 490, 78, 16);
  context.fillStyle = "rgba(18, 30, 57, 0.9)";
  context.fill();
  context.strokeStyle = "#33466f";
  context.lineWidth = 2;
  context.stroke();
  context.fillStyle = "#e6ecfb";
  context.font = "600 20px system-ui, sans-serif";
  context.fillText("MOVE  Arrow keys / A D W S", WIDTH / 2, 558);
  context.fillStyle = "#8192b7";
  context.font = "400 16px system-ui, sans-serif";
  context.fillText("Collect every light shard. Stop by releasing the key.", WIDTH / 2, 585);

  context.fillStyle = "#69d9c6";
  context.font = "700 15px ui-monospace, monospace";
  context.fillText("●  READY FOR INPUT", WIDTH / 2, 660);
  context.textAlign = "left";
}

function drawShard(shard, index) {
  if (collected.has(index)) return;

  const glow = context.createRadialGradient(shard.x, shard.y, 2, shard.x, shard.y, 42);
  glow.addColorStop(0, "rgba(255, 230, 112, 0.48)");
  glow.addColorStop(1, "rgba(255, 230, 112, 0)");
  context.fillStyle = glow;
  context.beginPath();
  context.arc(shard.x, shard.y, 42, 0, Math.PI * 2);
  context.fill();

  context.beginPath();
  for (let point = 0; point < 10; point += 1) {
    const angle = -Math.PI / 2 + point * Math.PI / 5;
    const radius = point % 2 === 0 ? 16 : 7;
    const x = shard.x + Math.cos(angle) * radius;
    const y = shard.y + Math.sin(angle) * radius;
    if (point === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.closePath();
  context.fillStyle = "#ffe370";
  context.shadowColor = "#ffd75a";
  context.shadowBlur = 16;
  context.fill();
  context.shadowBlur = 0;
  context.strokeStyle = "#fff7c2";
  context.lineWidth = 2;
  context.stroke();
}

function drawPlayer() {
  context.save();
  context.translate(player.x, player.y);
  context.rotate(Math.atan2(player.facingY, player.facingX));

  context.fillStyle = "rgba(59, 225, 211, 0.15)";
  context.beginPath();
  context.arc(0, 0, 34, 0, Math.PI * 2);
  context.fill();

  context.fillStyle = "#41e5d1";
  context.strokeStyle = "#c7fff6";
  context.lineWidth = 3;
  context.beginPath();
  context.moveTo(24, 0);
  context.lineTo(-14, -17);
  context.lineTo(-7, 0);
  context.lineTo(-14, 17);
  context.closePath();
  context.fill();
  context.stroke();

  context.fillStyle = "#102b42";
  context.beginPath();
  context.arc(3, 0, 6, 0, Math.PI * 2);
  context.fill();
  context.restore();
}

function drawGame() {
  roundedRect(28, 24, 1224, 78, 18);
  context.fillStyle = "rgba(10, 18, 39, 0.94)";
  context.fill();
  context.strokeStyle = "#2a3a61";
  context.lineWidth = 2;
  context.stroke();

  context.fillStyle = "#f2f6ff";
  context.font = "800 25px system-ui, sans-serif";
  context.fillText("CANVAS COURIER", 56, 62);
  context.fillStyle = collected.size === shards.length ? "#ffe370" : "#74ead5";
  context.font = "700 15px ui-monospace, monospace";
  const status = collected.size === shards.length ? "ROUTE COMPLETE" : "DELIVERY IN PROGRESS";
  context.fillText(status, 56, 86);

  context.textAlign = "right";
  context.fillStyle = "#ffe370";
  context.font = "800 27px system-ui, sans-serif";
  context.fillText(`LIGHT SHARDS  ${collected.size} / ${shards.length}`, 1220, 62);
  context.fillStyle = "#9aadd2";
  context.font = "500 16px system-ui, sans-serif";
  context.fillText(`X ${Math.round(player.x)}  Y ${Math.round(player.y)}  •  240 px/s`, 1220, 87);
  context.textAlign = "left";

  roundedRect(48, 132, 1184, 540, 24);
  context.strokeStyle = "#2a3d64";
  context.lineWidth = 2;
  context.stroke();

  context.fillStyle = "#8294b9";
  context.font = "600 16px system-ui, sans-serif";
  context.fillText("PICKUP LANE", 78, 178);
  context.fillStyle = "#536789";
  context.font = "400 14px system-ui, sans-serif";
  context.fillText("Move into a glowing shard to collect it", 78, 202);

  context.strokeStyle = "rgba(75, 225, 205, 0.15)";
  context.lineWidth = 3;
  context.setLineDash([12, 14]);
  context.beginPath();
  context.moveTo(120, 500);
  context.lineTo(800, 500);
  context.stroke();
  context.setLineDash([]);

  shards.forEach(drawShard);
  drawPlayer();

  if (collected.size === shards.length) {
    roundedRect(425, 230, 430, 78, 16);
    context.fillStyle = "rgba(21, 37, 54, 0.96)";
    context.fill();
    context.strokeStyle = "#ffe370";
    context.stroke();
    context.textAlign = "center";
    context.fillStyle = "#fff3b0";
    context.font = "800 25px system-ui, sans-serif";
    context.fillText("ALL SHARDS COLLECTED!", WIDTH / 2, 278);
    context.textAlign = "left";
  }

  context.fillStyle = "#7e91b8";
  context.font = "500 15px system-ui, sans-serif";
  context.fillText("Arrow keys / A D W S to move  •  Release to stop", 56, 704);
}

function draw() {
  drawBackground();
  if (mode === "title") drawTitle();
  else drawGame();
}

function frame(timestamp) {
  if (previousTime === null) previousTime = timestamp;
  const elapsed = Math.min((timestamp - previousTime) / 1000, 0.25);
  previousTime = timestamp;
  accumulator += elapsed;
  while (accumulator >= STEP_SECONDS) {
    update(STEP_SECONDS);
    accumulator -= STEP_SECONDS;
  }
  draw();
  window.requestAnimationFrame(frame);
}

document.addEventListener("keydown", (event) => {
  if (!controlledKeys.has(event.code)) return;
  event.preventDefault();
  keys.add(event.code);
});

document.addEventListener("keyup", (event) => {
  if (!controlledKeys.has(event.code)) return;
  event.preventDefault();
  keys.delete(event.code);
});

window.addEventListener("blur", () => keys.clear());
startButton.addEventListener("click", startGame);

if (new URLSearchParams(window.location.search).get("scenario") === "playground") {
  startGame();
}

draw();
window.requestAnimationFrame(frame);
window.__GAME_READY__ = true;
