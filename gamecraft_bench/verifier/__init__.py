"""Replay demo traces, score the recordings, and emit a reward in [0, 1].

Top-level pieces:
- ``replay``: drives Godot under Xvfb and records xdotool input to mp4.
- ``web_replay``: drives a local Web game with Playwright under Xvfb and
  records through the same ffmpeg pipeline.
- ``judges``: pluggable multimodal judge backends. Selected via
  ``GAMECRAFT_BENCH_JUDGE`` (see ``gamecraft_bench.config``).
- ``score``: glues a rubric, a list of demos, and a judge into a single
  per-task score.
- ``cli``: command-line entry point.
"""
