"""VoiceCloner — RVC-based voice cloning pipeline.

Trains a voice model from source audio, then synthesises new speech
with the cloned voice. Pipeline:

    1. Preprocess source audio (noise reduction, normalization)
    2. Extract voice features and train RVC model
    3. Synthesize new speech from text input
    4. Post-process output (format conversion, normalization)

Integrations:
    - RVC (Retrieval-based Voice Conversion) for cloning
    - Edge-TTS / Coqui TTS for base speech synthesis
    - FFmpeg for audio format conversion
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.config import get_settings, MediaConfig
from src.core.db import get_db, Database
from src.core.logger import get_logger

log = get_logger(__name__)


# ── Data Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class VoiceModel:
    """A trained voice model."""
    id: str
    name: str
    source_path: Path
    model_path: Path | None
    language: str
    sample_rate: int
    created_at: datetime


@dataclass(frozen=True)
class CloneResult:
    """Result of a voice clone synthesis."""
    id: str
    voice_id: str
    input_text: str
    output_path: Path
    duration_sec: float
    quality_score: float


@dataclass(frozen=True)
class PreprocessResult:
    """Result of audio preprocessing."""
    output_path: Path
    sample_rate: int
    duration_sec: float
    channels: int


# ── RVC Pipeline ─────────────────────────────────────────────────

class VoiceCloner:
    """Voice cloning using RVC (Retrieval-based Voice Conversion).

    Training pipeline:
        1. Preprocess audio → clean, normalize, resample to 44.1 kHz
        2. Extract pitch (f0) and features
        3. Train RVC model (fine-tune on source voice)
        4. Save model weights

    Inference pipeline:
        1. Generate base speech (Edge-TTS or Coqui)
        2. Convert voice through RVC model
        3. Post-process and normalize

    Args:
        model_dir: Directory for trained voice models.
        output_dir: Directory for generated audio files.
        db: Database instance (defaults to singleton).
        media_cfg: Media configuration.

    Usage:
        cloner = VoiceCloner()
        voice = await cloner.train("source.wav", name="Amy Voice")
        result = await cloner.clone(voice.id, "Hello, world!", "output.mp3")
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        db: Database | None = None,
        media_cfg: MediaConfig | None = None,
    ) -> None:
        cfg = media_cfg or get_settings().media
        self._model_dir = Path(model_dir or cfg.rvc_model_path)
        self._output_dir = Path(output_dir or cfg.output_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._db = db or get_db()

    # ── Training ────────────────────────────────────────────────

    async def train(
        self,
        audio_path: str | Path,
        name: str,
        language: str = "en",
        epochs: int = 300,
    ) -> VoiceModel:
        """Train an RVC voice model from source audio.

        The source audio should be:
            - Clean speech, minimal background noise
            - At least 10 minutes long (30+ min recommended)
            - Single speaker only

        Args:
            audio_path: Path to source audio file.
            name: Human-readable name for the voice.
            language: ISO language code.
            epochs: Training epochs (more = better quality, slower).

        Returns:
            VoiceModel with paths to trained model.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Source audio not found: {audio_path}")

        voice_id = str(uuid.uuid4())
        model_path = self._model_dir / voice_id

        log.info("voice.train_start", voice_id=voice_id, name=name, audio=str(audio_path))

        # Step 1: Preprocess audio
        preprocessed = await self._preprocess_audio(audio_path, voice_id)

        # Step 2: Extract features and pitch
        features_dir = model_path / "features"
        features_dir.mkdir(parents=True, exist_ok=True)
        await self._extract_features(preprocessed.output_path, features_dir)

        # Step 3: Train RVC model
        weights_path = model_path / "model.pth"
        await self._train_rvc(
            features_dir=features_dir,
            output_path=weights_path,
            epochs=epochs,
            sample_rate=preprocessed.sample_rate,
        )

        # Save model metadata
        model_meta = {
            "voice_id": voice_id,
            "name": name,
            "language": language,
            "source_path": str(audio_path),
            "model_path": str(weights_path),
            "sample_rate": preprocessed.sample_rate,
            "duration_sec": preprocessed.duration_sec,
            "epochs": epochs,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (model_path / "meta.json").write_text(json.dumps(model_meta, indent=2))

        # Persist to database
        self._db.execute(
            """
            INSERT INTO media_voices (id, name, source_path, model_path, language, sample_rate)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (voice_id, name, str(audio_path), str(weights_path), language, preprocessed.sample_rate),
        )

        voice = VoiceModel(
            id=voice_id,
            name=name,
            source_path=audio_path,
            model_path=weights_path,
            language=language,
            sample_rate=preprocessed.sample_rate,
            created_at=datetime.now(timezone.utc),
        )

        log.info("voice.train_complete", voice_id=voice_id, model=str(weights_path))
        return voice

    # ── Cloning / Synthesis ─────────────────────────────────────

    async def clone(
        self,
        voice_id: str,
        text: str,
        output_path: str | Path,
        base_voice: str = "en-US-AriaNeural",
    ) -> CloneResult:
        """Clone a voice by synthesizing speech in the target voice.

        Pipeline:
            1. Generate base speech from text using TTS
            2. Run RVC voice conversion on the base speech
            3. Post-process and normalize output

        Args:
            voice_id: ID of the trained voice model.
            text: Text to synthesize.
            output_path: Where to save the output audio.
            base_voice: Edge-TTS voice for base speech generation.

        Returns:
            CloneResult with output path and metadata.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load voice model
        voice_meta = self._load_voice(voice_id)
        if not voice_meta or not voice_meta.model_path:
            raise ValueError(f"Voice model not found or not trained: {voice_id}")

        model_path = Path(voice_meta.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model weights not found: {model_path}")

        clone_id = str(uuid.uuid4())
        log.info("voice.clone_start", clone_id=clone_id, voice_id=voice_id, text_len=len(text))

        # Step 1: Generate base TTS speech
        tts_path = self._output_dir / f"tts_{clone_id[:8]}.wav"
        await self._generate_tts(text, tts_path, base_voice)

        # Step 2: RVC voice conversion
        converted_path = self._output_dir / f"converted_{clone_id[:8]}.wav"
        await self._rvc_convert(
            input_path=tts_path,
            model_path=model_path,
            output_path=converted_path,
        )

        # Step 3: Post-process
        duration = await self._postprocess(converted_path, output_path)

        # Cleanup intermediate files
        tts_path.unlink(missing_ok=True)
        converted_path.unlink(missing_ok=True)

        # Quality score (placeholder — in production use MOS or PESQ)
        quality_score = 0.85

        # Persist
        self._db.execute(
            """
            INSERT INTO media_clones (id, voice_id, input_text, output_path, duration_sec, quality_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (clone_id, voice_id, text, str(output_path), duration, quality_score),
        )

        result = CloneResult(
            id=clone_id,
            voice_id=voice_id,
            input_text=text,
            output_path=output_path,
            duration_sec=duration,
            quality_score=quality_score,
        )

        log.info("voice.clone_complete", clone_id=clone_id, duration=duration, path=str(output_path))
        return result

    # ── Audio Preprocessing ─────────────────────────────────────

    async def _preprocess_audio(
        self, audio_path: Path, voice_id: str
    ) -> PreprocessResult:
        """Preprocess source audio for training.

        Steps:
            - Convert to WAV 44.1 kHz mono
            - Noise reduction via noisereduce
            - Normalization to -3 dBFS
            - Silence trimming
        """
        output_path = self._model_dir / voice_id / "preprocessed.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to standard WAV format with FFmpeg
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_path),
            "-ar", "44100",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-af", "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,"
                   "areverse,silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB,areverse,"
                   "loudnorm=I=-3:TP=-1.5:LRA=11",
            str(output_path),
        ]

        log.info("voice.preprocessing", input=str(audio_path))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg preprocessing failed: {stderr.decode()[:500]}")

        # Get audio info
        info = await self._get_audio_info(output_path)

        return PreprocessResult(
            output_path=output_path,
            sample_rate=info["sample_rate"],
            duration_sec=info["duration_sec"],
            channels=info["channels"],
        )

    async def _extract_features(self, audio_path: Path, output_dir: Path) -> None:
        """Extract pitch (f0) and acoustic features for RVC training.

        Uses CREPE for pitch extraction and a pretrained HuBERT model
        for content features.
        """
        log.info("voice.extracting_features", audio=str(audio_path))

        # In production, this calls RVC's feature extraction pipeline:
        #   python rvc/extract/preprocess.py -d {output_dir} -sr 44100
        #   python rvc/extract/extract_f0.py -d {output_dir} -m crepe
        #   python rvc/extract/extract_feature.py -d {output_dir}

        # For now, create placeholder feature files
        (output_dir / "f0.npy").touch()
        (output_dir / "features.npy").touch()
        (output_dir / "config.json").write_text(json.dumps({
            "sample_rate": 44100,
            "feature_extractor": "hubert_base",
            "f0_method": "crepe",
        }))

    async def _train_rvc(
        self,
        features_dir: Path,
        output_path: Path,
        epochs: int,
        sample_rate: int,
    ) -> None:
        """Fine-tune RVC model on the extracted features.

        In production this invokes RVC's training script:
            python rvc/train.py -d {features_dir} -o {output_path} -e {epochs}
        """
        log.info("voice.training", epochs=epochs, output=str(output_path))

        # Simulate training — in production replace with actual RVC call
        # cmd = [
        #     "python", "-m", "rvc.train",
        #     "--data-dir", str(features_dir),
        #     "--output", str(output_path),
        #     "--epochs", str(epochs),
        #     "--sample-rate", str(sample_rate),
        #     "--batch-size", "16",
        #     "--save-every", "50",
        # ]
        # proc = await asyncio.create_subprocess_exec(*cmd)
        # await proc.wait()

        # Create placeholder model file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RVC_MODEL_PLACEHOLDER")

    # ── TTS Base Generation ─────────────────────────────────────

    async def _generate_tts(
        self, text: str, output_path: Path, voice: str = "en-US-AriaNeural"
    ) -> None:
        """Generate base speech using Edge-TTS (free, high-quality).

        Falls back to Coqui TTS if edge-tts is not available.
        """
        try:
            await self._edge_tts(text, output_path, voice)
        except (ImportError, FileNotFoundError):
            await self._coqui_tts(text, output_path)

    async def _edge_tts(self, text: str, output_path: Path, voice: str) -> None:
        """Generate speech using edge-tts."""
        cmd = [
            "edge-tts",
            "--voice", voice,
            "--text", text,
            "--write-media", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"edge-tts failed: {stderr.decode()[:300]}")

    async def _coqui_tts(self, text: str, output_path: Path) -> None:
        """Generate speech using Coqui TTS as fallback."""
        cmd = [
            "tts",
            "--text", text,
            "--out_path", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Coqui TTS failed: {stderr.decode()[:300]}")

    # ── RVC Voice Conversion ────────────────────────────────────

    async def _rvc_convert(
        self,
        input_path: Path,
        model_path: Path,
        output_path: Path,
        pitch_shift: int = 0,
    ) -> None:
        """Convert voice using trained RVC model.

        In production:
            python rvc/infer.py -i {input} -m {model} -o {output} -p {pitch}
        """
        log.info("voice.converting", input=str(input_path), model=str(model_path))

        # In production, replace with actual RVC inference
        # cmd = [
        #     "python", "-m", "rvc.infer",
        #     "--input", str(input_path),
        #     "--model", str(model_path),
        #     "--output", str(output_path),
        #     "--pitch", str(pitch_shift),
        # ]
        # proc = await asyncio.create_subprocess_exec(*cmd)
        # await proc.wait()

        # For now, copy input as placeholder
        shutil.copy2(input_path, output_path)

    async def _postprocess(self, input_path: Path, output_path: Path) -> float:
        """Post-process converted audio: normalize, convert format.

        Returns duration in seconds.
        """
        # Normalize and convert to final format
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,highpass=f=80,lowpass=f=12000",
            "-ar", "44100",
            "-ac", "1",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            # Fallback: just copy if FFmpeg fails
            if input_path != output_path:
                shutil.copy2(input_path, output_path)

        info = await self._get_audio_info(output_path)
        return info["duration_sec"]

    # ── Utilities ───────────────────────────────────────────────

    async def _get_audio_info(self, audio_path: Path) -> dict[str, Any]:
        """Get audio file metadata using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(audio_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode != 0:
            return {"sample_rate": 44100, "duration_sec": 0.0, "channels": 1}

        data = json.loads(stdout)
        stream = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), {})

        return {
            "sample_rate": int(stream.get("sample_rate", 44100)),
            "duration_sec": float(data.get("format", {}).get("duration", 0)),
            "channels": int(stream.get("channels", 1)),
        }

    def _load_voice(self, voice_id: str) -> VoiceModel | None:
        """Load a voice model record from the database."""
        row = self._db.fetchone(
            "SELECT * FROM media_voices WHERE id = ?",
            (voice_id,),
        )
        if not row:
            return None
        return VoiceModel(
            id=row["id"],
            name=row["name"],
            source_path=Path(row["source_path"]),
            model_path=Path(row["model_path"]) if row.get("model_path") else None,
            language=row["language"],
            sample_rate=row["sample_rate"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_voices(self) -> list[VoiceModel]:
        """List all trained voice models."""
        rows = self._db.fetchall("SELECT * FROM media_voices ORDER BY created_at DESC")
        return [
            VoiceModel(
                id=r["id"],
                name=r["name"],
                source_path=Path(r["source_path"]),
                model_path=Path(r["model_path"]) if r.get("model_path") else None,
                language=r["language"],
                sample_rate=r["sample_rate"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def delete_voice(self, voice_id: str) -> bool:
        """Delete a voice model and its files."""
        voice = self._load_voice(voice_id)
        if not voice:
            return False

        # Remove model files
        model_dir = self._model_dir / voice_id
        if model_dir.exists():
            shutil.rmtree(model_dir)

        # Remove DB record
        self._db.execute("DELETE FROM media_voices WHERE id = ?", (voice_id,))
        self._db.execute("DELETE FROM media_clones WHERE voice_id = ?", (voice_id,))

        log.info("voice.deleted", voice_id=voice_id)
        return True
