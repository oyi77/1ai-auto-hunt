"""Streaming Farm — Spotify/Apple Music royalty generator.

Uses phonefarm device templates to play tracks at scale while staying
under platform radar. Each account streams ≤200 tracks/day with random
selection and ≥31 s per play to count as a royalty-qualifying stream.

Key classes:
    StreamingFarm  — orchestrates account pool × playlist rotation
    PlaylistManager — CRUD for playlists and daily schedule generation
"""

from __future__ import annotations

from src.hunts.stream.farm import StreamingFarm
from src.hunts.stream.playlist import PlaylistManager

__all__ = ["StreamingFarm", "PlaylistManager"]
