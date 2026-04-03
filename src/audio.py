"""
Tako Reader — audio playback and TTS fetching.
Supports Forvo API (user-provided key) with Google Translate TTS fallback.
"""

import json
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import quote as url_quote

from PyQt6.QtCore import QThread, pyqtSignal, QUrl, QSettings
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


# ─── Audio fetching ──────────────────────────────────────────────────────────

def fetch_audio_forvo(word: str, api_key: str) -> bytes | None:
    """Fetch pronunciation audio from Forvo API. Returns mp3 bytes or None."""
    if not api_key:
        return None
    try:
        url = (
            f"https://apifree.forvo.com/key/{api_key}"
            f"/format/json/action/word-pronunciations"
            f"/word/{url_quote(word)}/language/ja"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TakoReader/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = data.get("items", [])
        if not items:
            return None

        # Prefer the highest-rated pronunciation
        best = max(items, key=lambda x: x.get("num_positive_votes", 0))
        audio_url = best.get("pathmp3") or best.get("pathogg")
        if not audio_url:
            return None

        req2 = urllib.request.Request(audio_url, headers={"User-Agent": "TakoReader/1.0"})
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            return resp2.read()
    except Exception:
        return None


def fetch_audio_google_tts(word: str) -> bytes | None:
    """Fetch pronunciation from Google Translate TTS. Returns mp3 bytes or None."""
    try:
        url = (
            f"https://translate.google.com/translate_tts"
            f"?ie=UTF-8&tl=ja&client=tw-ob&q={url_quote(word)}"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            if len(data) < 100:
                return None  # too small to be real audio
            return data
    except Exception:
        return None


def fetch_audio(word: str, app_settings: QSettings) -> bytes | None:
    """
    Fetch audio for a word. Tries Forvo first (if API key configured),
    falls back to Google Translate TTS.
    """
    forvo_key = app_settings.value("dict/forvo_key", "").strip()
    if forvo_key:
        audio = fetch_audio_forvo(word, forvo_key)
        if audio:
            return audio
    return fetch_audio_google_tts(word)


# ─── Background worker ──────────────────────────────────────────────────────

class AudioFetchWorker(QThread):
    """Fetch audio on a background thread."""
    finished = pyqtSignal(str, bytes)  # (word, mp3_bytes) — bytes is empty on failure
    failed   = pyqtSignal(str, str)    # (word, error_msg)

    def __init__(self, word: str, app_settings: QSettings):
        super().__init__()
        self.word = word
        self.app_settings = app_settings

    def run(self):
        try:
            data = fetch_audio(self.word, self.app_settings)
            if data:
                self.finished.emit(self.word, data)
            else:
                self.failed.emit(self.word, "No audio found")
        except Exception as e:
            self.failed.emit(self.word, str(e))


# ─── Audio player singleton ─────────────────────────────────────────────────

class AudioPlayer:
    """Simple audio player using Qt's QMediaPlayer. Singleton."""
    _instance = None

    @classmethod
    def get(cls) -> "AudioPlayer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._temp_file: str | None = None

    def play_bytes(self, data: bytes):
        """Play mp3 data from bytes."""
        # Write to a temp file — QMediaPlayer needs a file URL
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(data)
        tmp.close()
        self._temp_file = tmp.name

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(tmp.name))
        self._audio_output.setVolume(1.0)
        self._player.play()

    def stop(self):
        self._player.stop()
