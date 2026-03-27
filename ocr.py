"""
Tako Reader — OCR subsystem.
Manages the long-lived manga-ocr subprocess, Qt worker threads,
and Japanese text segmentation via fugashi.
"""

import sys
import json

from PyQt6.QtCore import QThread, pyqtSignal, QRect
from PyQt6.QtGui import QImage


# ─── Embedded child-process script ──────────────────────────────────────────
# Runs in a separate Python process.  Loads the model once, then loops
# reading one JSON request per line on stdin and writing one JSON result.

_OCR_PROCESS_SCRIPT = """
import sys, json, base64, io

def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "cpu"
    try:
        import manga_ocr
        from PIL import Image as PILImage
        model = manga_ocr.MangaOcr(force_cpu=(device == "cpu"))
        print(json.dumps({"ready": True}), flush=True)
    except Exception:
        import traceback
        print(json.dumps({"ready": False, "error": traceback.format_exc()}), flush=True)
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            img_bytes = base64.b64decode(data["image_b64"])
            pil_img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
            text = model(pil_img)
            print(json.dumps({"ok": True, "text": text}), flush=True)
        except Exception:
            import traceback
            print(json.dumps({"ok": False, "error": traceback.format_exc()}), flush=True)

main()
"""


# ─── OCR Process Manager ────────────────────────────────────────────────────

class OCRProcessManager:
    """
    Singleton-per-device that owns a long-lived OCR subprocess.
    The process loads manga_ocr once, then handles unlimited requests.
    """
    _instances: dict = {}

    @classmethod
    def get(cls, device: str) -> "OCRProcessManager":
        if device not in cls._instances:
            cls._instances[device] = cls(device)
        return cls._instances[device]

    @classmethod
    def shutdown_all(cls):
        for mgr in cls._instances.values():
            mgr._stop()
        cls._instances.clear()

    def __init__(self, device: str):
        self.device  = device
        self._proc   = None
        self._ready  = False
        self._error  = None

    def _start(self):
        """Launch the worker process and wait for its ready signal."""
        import subprocess
        self._proc = subprocess.Popen(
            [sys.executable, "-c", _OCR_PROCESS_SCRIPT, self.device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready_line = self._proc.stdout.readline().strip()
        if ready_line:
            data = json.loads(ready_line)
            if data.get("ready"):
                self._ready = True
            else:
                self._error = data.get("error", "Unknown startup error")
                self._stop()
        else:
            stderr = self._proc.stderr.read()
            self._error = stderr or "No ready signal from OCR process"
            self._stop()

    def _stop(self):
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc  = None
            self._ready = False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def run_ocr(self, img_b64: str) -> dict:
        """Send an image and return the result dict. Blocks until done."""
        if not self.is_alive():
            self._ready = False
            self._error = None
            self._start()
        if not self._ready:
            return {"ok": False, "error": self._error or "OCR process failed to start"}
        try:
            payload = json.dumps({"image_b64": img_b64}) + "\n"
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            result_line = self._proc.stdout.readline().strip()
            if not result_line:
                stderr = self._proc.stderr.read()
                self._stop()
                return {"ok": False, "error": stderr or "No response from OCR process"}
            return json.loads(result_line)
        except Exception:
            import traceback
            self._stop()
            return {"ok": False, "error": traceback.format_exc()}


# ─── Qt worker threads ──────────────────────────────────────────────────────

class OCRWorker(QThread):
    """Qt thread that calls OCRProcessManager so the UI never blocks."""
    result_ready   = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, image: QImage, rect: QRect, device: str = "cpu"):
        super().__init__()
        self.image  = image
        self.rect   = rect
        self.device = device

    def run(self):
        try:
            import base64, io
            import numpy as np
            from PIL import Image as PILImage

            cropped = self.image.copy(self.rect)
            w, h = cropped.width(), cropped.height()
            ptr  = cropped.bits()
            ptr.setsize(h * w * 4)
            arr  = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
            pil  = PILImage.fromarray(arr[:, :, :3])
            buf  = io.BytesIO()
            pil.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            mgr    = OCRProcessManager.get(self.device)
            result = mgr.run_ocr(img_b64)

            if result.get("ok"):
                self.result_ready.emit(result["text"])
            else:
                self.error_occurred.emit("OCR error:\n" + result.get('error', 'unknown'))
        except Exception:
            import traceback
            self.error_occurred.emit(traceback.format_exc())


class OCRWarmupWorker(QThread):
    """
    Starts the OCR subprocess in the background at app launch so the model
    is already loaded by the time the user makes their first OCR request.
    """
    ready  = pyqtSignal(str)   # emits device name on success
    failed = pyqtSignal(str)   # emits error message on failure

    def __init__(self, device: str):
        super().__init__()
        self.device = device

    def run(self):
        mgr = OCRProcessManager.get(self.device)
        if not mgr.is_alive():
            mgr._start()
        if mgr._ready:
            self.ready.emit(self.device)
        else:
            self.failed.emit(mgr._error or "OCR warmup failed")


# ─── Segmentation ───────────────────────────────────────────────────────────

def segment_japanese(text: str) -> list[str]:
    """
    Tokenise Japanese text into a list of surface forms using fugashi.
    Falls back to returning the whole string as one token if unavailable.
    """
    try:
        import fugashi
        tagger = fugashi.Tagger()
        return [w.surface for w in tagger(text) if w.surface.strip()]
    except Exception:
        return [text]
