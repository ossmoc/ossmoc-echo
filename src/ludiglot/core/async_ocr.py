"""Bounded, latest-only fallback OCR. No matcher/UI state on this worker."""
from __future__ import annotations

import threading
import time


class LatestOnlyOcr:
    def __init__(self, backend):
        self.backend = backend
        self._condition = threading.Condition()
        self._pending = self._active = self._completed = None
        self._epoch = 0
        self._closed = False
        self._thread = None
        self._wakeup = lambda: None
        self.submitted_count = 0
        self.last_completed_ms = 0.0

    def set_wakeup(self, callback):
        with self._condition:
            self._wakeup = callback

    @property
    def pending(self):
        with self._condition:
            return self._active is not None or self._pending is not None

    def prepare_runtime(self):
        return self.backend.prepare_runtime()

    def recognize(self, image):
        # Explicit preview only; serialize with background native calls.
        return self.backend.recognize(image)

    def poll_or_submit(self, image, token):
        with self._condition:
            if self._closed:
                return []
            if self._completed and self._completed[:2] == (self._epoch, token):
                self.last_completed_ms = self._completed[3]
                return self._completed[2]
            if any(job is not None and job[:2] == (self._epoch, token)
                   for job in (self._active, self._pending)):
                return None
            self._pending = (self._epoch, token, image.copy())
            self.submitted_count += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='Echo-Fallback-OCR', daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return None

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                job, self._pending = self._pending, None
                self._active = job
            started = time.perf_counter()
            try:
                boxes = self.backend.recognize(job[2])
            except Exception:
                boxes = []
            elapsed = 1000 * (time.perf_counter() - started)
            wakeup = None
            with self._condition:
                self._active = None
                if not self._closed and job[0] == self._epoch and (
                        self._pending is None or self._pending[:2] == job[:2]):
                    self._completed = (job[0], job[1], boxes, elapsed)
                    wakeup = self._wakeup
            if wakeup:
                try:
                    wakeup()
                except Exception:
                    pass

    def cancel(self):
        with self._condition:
            self._epoch += 1
            self._pending = self._completed = None

    def close(self):
        with self._condition:
            self._closed = True
            self._epoch += 1
            self._pending = self._completed = None
            self._condition.notify_all()
        close = getattr(self.backend, 'close', None)
        if close:
            close()
