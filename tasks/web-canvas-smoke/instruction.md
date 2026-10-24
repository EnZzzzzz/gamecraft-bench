# Canvas Courier

Build **Canvas Courier**, a small but complete interactive static Web game at
`/workspace/game/`. The game must run directly in a browser without npm, a build
step, external packages, remote assets, web fonts, or CDN dependencies.

## Game requirements

- Use a fixed **1280x720** layout with no scrollbars and clear, intentional
  visuals. Canvas, DOM, or a combination is fine.
- A normal launch opens on a styled title screen with the game name, keyboard
  instructions, and a clearly labeled **Start** button. Clicking Start enters
  the arena.
- Move a clearly visible player with the arrow keys and **A/D** (you may also
  support W/S). Holding a key moves the player; releasing it stops movement.
  Do not substitute automatic movement or a prerecorded animation.
- Place visible collectible light shards in the arena. Touching one with the
  player removes it and updates a visible collected counter. Include a visible
  gameplay state such as `DELIVERY IN PROGRESS` or `ROUTE COMPLETE`.
- The game must be deterministic. Use no randomness, or use a fixed seed. Do
  not start gameplay or move the player without input.

The project must contain at least `index.html`; local JavaScript and CSS files
are welcome. Put the finished artifact directly in `/workspace/game`, not in a
nested source directory.

## Launch and readiness

The evaluator starts the game with:

```bash
python3 -m http.server {port} --bind 127.0.0.1 --directory /workspace/game
```

The base URL is `http://127.0.0.1:{port}`. After the canvas, scripts, styles,
input handlers, and every other local resource are initialized, set:

```js
window.__GAME_READY__ = true;
```

The evaluator waits for that exact flag. Make no external network requests.

## Playground scenario

Support this URL:

```text
http://127.0.0.1:{port}/?scenario=playground
```

`playground` skips the title screen and enters the same deterministic arena
immediately, with the player and all collectibles in fixed positions. It must
accept keyboard input immediately and must not auto-play. A normal launch
without this query parameter must still show the title screen.

## Input demo traces

Ship exactly two demos under `/workspace/game/demo_outputs/`:

1. A normal-launch demo with no `scenario`: show the title, click Start, then
   hold and release a movement key.
2. A demo with `"scenario": "playground"`: move the player and collect at
   least one shard.

Make each recording about **4-6 seconds** at 30fps (`duration_frames` 120-180).
The evaluator launches a fresh page for each trace and replays the events as
real mouse and keyboard input. Coordinates are CSS pixels in the 1280x720
viewport. Use key holds long enough for the motion and collection to be visible.

```json
{
  "scenario": "playground",
  "duration_frames": 150,
  "events": [
    {"frame": 30, "type": "key_down", "keycode": "D"},
    {"frame": 105, "type": "key_up", "keycode": "D"},
    {"frame": 140, "type": "wait"}
  ]
}
```

For a normal launch, omit `scenario` and click the actual Start button first:

```json
{"frame": 30, "type": "mouse_click", "button": "left", "x": 640, "y": 442}
```

Events are ordered by integer `frame`. Supported event types are `mouse_click`,
`mouse_down`, `mouse_up`, `mouse_move`, `key_press`, `key_down`, `key_up`, and
`wait`. Keycodes are `A`-`Z`, `0`-`9`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER`,
`SPACE`, `ESCAPE`, `TAB`, `BACKSPACE`, `DELETE`, `SHIFT`, `CTRL`, and `ALT`.
A `wait` event is only a timing anchor. Up to 10 traces and 20 seconds per trace
are supported, but this task requires only the two traces above.

## Screenshots

Start the HTTP server yourself before using the screenshot helper; the helper
does not start or stop services. For example, run the launch command with port
`8000` in another shell or in the background, then run:

```bash
/workspace/tools/screenshot_web.sh \
  --url http://127.0.0.1:8000 \
  --out /workspace/title.png \
  --frames 60

/workspace/tools/screenshot_web.sh \
  --url http://127.0.0.1:8000 \
  --scenario playground \
  --out /workspace/playground.png \
  --frames 60
```

The helper uses a 1280x720 viewport at device pixel ratio 1, waits for
`window.__GAME_READY__ === true`, then waits `frames / 30` seconds before taking
the screenshot. `--width` and `--height` can override the viewport if needed.
Use screenshots to inspect the real title and gameplay rather than relying only
on source code.
