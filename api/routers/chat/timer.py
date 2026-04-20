"""效能計時器：記錄對話編排各步驟的耗時，供 debug panel 顯示。"""
import time


# ════════════════════════════════════════════════════════════
# SECTION: StepTimer
# ════════════════════════════════════════════════════════════

class StepTimer:
    """記錄每個步驟的耗時，供效能分析使用。"""
    def __init__(self):
        self._steps: list[dict] = []
        self._wall_start = time.perf_counter()

    def step(self, name: str):
        """回傳一個 context manager，自動記錄該步驟的耗時。"""
        return _TimedStep(self, name)

    def summary(self) -> dict:
        total = time.perf_counter() - self._wall_start
        return {
            "total_ms": round(total * 1000, 1),
            "steps": self._steps,
        }


class _TimedStep:
    def __init__(self, timer: StepTimer, name: str):
        self._timer = timer
        self._name = name
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed = time.perf_counter() - self._start
        self._timer._steps.append({
            "name": self._name,
            "ms": round(elapsed * 1000, 1),
        })
