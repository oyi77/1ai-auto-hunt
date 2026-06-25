"""Deepfake / AI Media — voice cloning and virtual influencer factory.

VoiceCloner trains RVC models on source audio then synthesises new speech.
AIInfluencer generates consistent character images via Stable Diffusion +
LoRA and schedules posts through 1ai-social.

Key classes:
    VoiceCloner   — RVC voice cloning pipeline
    AIInfluencer  — SD+LoRA character generation + social posting
"""

from __future__ import annotations

from src.hunts.media.voice import VoiceCloner
from src.hunts.media.influencer import AIInfluencer

__all__ = ["VoiceCloner", "AIInfluencer"]
