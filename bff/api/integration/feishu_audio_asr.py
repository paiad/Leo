from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from typing import Any

from app.logger import logger
from bff.utils.env import get_env


class LocalAsrEngine:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._model_key: tuple[str, str, str] | None = None

    def _repair_faster_whisper_hf_snapshot_if_needed(self, model_name: str) -> None:
        """
        Repair HuggingFace cache snapshot files if they were created as 0 bytes.

        On some Windows setups, HuggingFace cache "snapshot" files can end up as
        empty files while the actual data exists in the cache `blobs/` directory.
        `ctranslate2` then fails to load the model with:
        "File model.bin is incomplete ...".

        This function is best-effort and only runs for known "size" names (e.g. small).
        """
        if not model_name or any(sep in model_name for sep in ("/", "\\", ":")):
            return

        # faster-whisper maps size names to Systran repos, e.g. "small" -> "Systran/faster-whisper-small"
        hub_cache = os.getenv("HUGGINGFACE_HUB_CACHE") or ""
        if not hub_cache:
            hf_home = os.getenv("HF_HOME") or ""
            if hf_home:
                hub_cache = os.path.join(hf_home, "hub")
        if not hub_cache:
            return

        from pathlib import Path
        import shutil

        repo_dir = Path(hub_cache) / f"models--Systran--faster-whisper-{model_name}"
        blobs_dir = repo_dir / "blobs"
        snapshots_dir = repo_dir / "snapshots"
        if not blobs_dir.exists() or not snapshots_dir.exists():
            return

        try:
            blobs = [p for p in blobs_dir.iterdir() if p.is_file() and p.stat().st_size > 0]
        except Exception:
            return
        if not blobs:
            return

        def _read_head(path: Path, n: int = 32) -> bytes:
            try:
                with path.open("rb") as fp:
                    return fp.read(n)
            except Exception:
                return b""

        # Heuristics based on typical faster-whisper repo contents.
        model_blob = max(blobs, key=lambda p: p.stat().st_size)
        json_blobs = [p for p in blobs if _read_head(p, 1) == b"{"]
        small_json_blob = None
        large_json_blob = None
        if json_blobs:
            json_blobs_sorted = sorted(json_blobs, key=lambda p: p.stat().st_size)
            small_json_blob = json_blobs_sorted[0]
            large_json_blob = json_blobs_sorted[-1] if len(json_blobs_sorted) > 1 else None

        # pick a text blob for vocabulary (not JSON, reasonably sized)
        text_candidates = [p for p in blobs if p not in {model_blob, small_json_blob, large_json_blob}]
        vocab_blob = None
        for p in sorted(text_candidates, key=lambda x: x.stat().st_size, reverse=True):
            head = _read_head(p, 2)
            if head and head != b"\x00\x00" and head != b"\x06\x00":
                vocab_blob = p
                break

        did_repair = False
        for snapshot in snapshots_dir.iterdir():
            if not snapshot.is_dir():
                continue

            targets: dict[str, Path | None] = {
                "model.bin": model_blob,
                "config.json": small_json_blob,
                "tokenizer.json": large_json_blob,
                "vocabulary.txt": vocab_blob,
            }

            for filename, source in targets.items():
                if source is None:
                    continue
                dest = snapshot / filename
                try:
                    if dest.exists() and dest.is_file() and dest.stat().st_size > 0:
                        continue
                    # Ensure destination directory exists and write full contents.
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(str(source), str(dest))
                    did_repair = True
                except Exception:
                    # best-effort; do not fail ASR just because repair failed
                    continue

        if did_repair:
            logger.warning(
                "Detected broken HuggingFace snapshot files for faster-whisper; "
                f"repaired from cache blobs: repo_dir={repo_dir}"
            )

    def _apply_hf_cache_env(self) -> None:
        """
        Ensure faster-whisper downloads/reads models from a stable cache directory.

        This avoids cases where a partial download in the default user cache leaves a
        corrupted/0-byte `model.bin`, which then breaks ASR with:
        "File model.bin is incomplete ...".

        Priority:
        1) FEISHU_AUDIO_ASR_HF_CACHE_DIR (force)
        2) BFF_MCP_TOOL_HF_CACHE_DIR
        3) RAG_HF_CACHE_DIR
        """
        from pathlib import Path

        forced_cache_dir = os.getenv("FEISHU_AUDIO_ASR_HF_CACHE_DIR")
        cache_dir = (
            forced_cache_dir
            or os.getenv("BFF_MCP_TOOL_HF_CACHE_DIR")
            or os.getenv("RAG_HF_CACHE_DIR")
        )
        if not cache_dir:
            return

        base = Path(str(cache_dir)).expanduser()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

        force = bool(forced_cache_dir)
        if force or not os.getenv("HF_HOME"):
            os.environ["HF_HOME"] = str(base)
        if force or not os.getenv("HUGGINGFACE_HUB_CACHE"):
            os.environ["HUGGINGFACE_HUB_CACHE"] = str(base / "hub")
        if force or not os.getenv("TRANSFORMERS_CACHE"):
            os.environ["TRANSFORMERS_CACHE"] = str(base / "transformers")
        if force or not os.getenv("SENTENCE_TRANSFORMERS_HOME"):
            os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(base / "sentence_transformers")

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        preferred_device = str(get_env("FEISHU_AUDIO_ASR_DEVICE", "cuda") or "cuda").strip().lower()
        model_name = str(get_env("FEISHU_AUDIO_ASR_MODEL", "small") or "small").strip()
        language = str(get_env("FEISHU_AUDIO_ASR_LANGUAGE", "zh") or "zh").strip()

        if preferred_device not in {"cuda", "cpu"}:
            preferred_device = "cuda"
        device_order = [preferred_device]
        if preferred_device != "cpu":
            device_order.append("cpu")

        last_error: Exception | None = None
        for device in device_order:
            compute_type = self._resolve_compute_type(device)
            try:
                return await asyncio.to_thread(
                    self._transcribe_sync,
                    audio_bytes,
                    model_name,
                    device,
                    compute_type,
                    language,
                )
            except Exception as exc:  # pragma: no cover - only hit when runtime env is broken
                last_error = exc
                logger.warning(
                    "Feishu audio ASR transcribe failed on device fallback path: "
                    f"device={device}, compute_type={compute_type}, error={exc}"
                )

        if last_error:
            raise last_error
        return ""

    def _resolve_compute_type(self, device: str) -> str:
        if device == "cuda":
            default = "int8_float16"
            return str(get_env("FEISHU_AUDIO_ASR_COMPUTE_TYPE_CUDA", default) or default).strip()
        default = "int8"
        return str(get_env("FEISHU_AUDIO_ASR_COMPUTE_TYPE_CPU", default) or default).strip()

    def _get_model(self, model_name: str, device: str, compute_type: str) -> Any:
        model_key = (model_name, device, compute_type)
        if self._model is not None and self._model_key == model_key:
            return self._model

        self._apply_hf_cache_env()
        self._repair_faster_whisper_hf_snapshot_if_needed(model_name)

        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(
            model_size_or_path=model_name,
            device=device,
            compute_type=compute_type,
        )
        self._model = model
        self._model_key = model_key
        return model

    def _transcribe_sync(
        self,
        audio_bytes: bytes,
        model_name: str,
        device: str,
        compute_type: str,
        language: str,
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="feishu-audio-asr-") as tmpdir:
            input_path = os.path.join(tmpdir, "input_audio")
            wav_path = os.path.join(tmpdir, "input_audio.wav")
            with open(input_path, "wb") as fp:
                fp.write(audio_bytes)

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    input_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    wav_path,
                ],
                check=True,
                capture_output=True,
            )
            model = self._get_model(
                model_name=model_name,
                device=device,
                compute_type=compute_type,
            )
            segments, _ = model.transcribe(
                wav_path,
                language=language,
                vad_filter=True,
                beam_size=1,
            )
            text = "".join(segment.text for segment in segments).strip()
            return text
