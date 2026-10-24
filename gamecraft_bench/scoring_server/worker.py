from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..verifier.judges import MultimodalJudge
from .evidence import build_demo_artifacts
from .settings import Settings
from .store import EvaluationStore


class EvaluationWorker:
    def __init__(
        self,
        settings: Settings,
        store: EvaluationStore,
        judge: MultimodalJudge | None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.judge = judge
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        for evaluation_id in self.store.recover():
            self.queue.put_nowait(evaluation_id)
        self._task = asyncio.create_task(self._run(), name="evaluation-worker")

    def enqueue(self, evaluation_id: str) -> None:
        self.queue.put_nowait(evaluation_id)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    async def _run(self) -> None:
        while True:
            evaluation_id = await self.queue.get()
            try:
                self.store.mark_running(evaluation_id)
                await asyncio.to_thread(self._score, evaluation_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.mark_finished(
                    evaluation_id,
                    error=f"evaluation failed ({type(exc).__name__})",
                )
            else:
                self.store.mark_finished(evaluation_id)
            finally:
                self.queue.task_done()

    def _score(self, evaluation_id: str) -> None:
        from ..verifier.score import score_evidence

        root = self.settings.data_dir / evaluation_id
        output_dir = root / "output"
        shutil.rmtree(output_dir, ignore_errors=True)
        score_evidence(
            rubric_path=root / "rubric.json",
            output_dir=output_dir,
            demos=build_demo_artifacts(root),
            build_ok=self._build_ok(root),
            build_log="",
            judge=self.judge,
        )

    @staticmethod
    def _build_ok(root: Path) -> bool:
        return (root / "build_ok").read_text(encoding="ascii") == "true"
