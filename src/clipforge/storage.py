"""Centralized local workspace directory management for ClipForge.

Organizes all local artifacts cleanly under `workspace/` (or CLIPFORGE_DATA_DIR):
  - logs/             clipforge.log + errors.jsonl (machine-readable for agents)
  - live_recordings/  long 1h-2h+ live stream sessions & full_recording.mp4
  - live_clips/       instant manual & auto-detected live clips
  - rendered_clips/   9:16 vertical batches (lote-XXXX/)
  - sources/          imported videos & yt-dlp downloads
"""

from dataclasses import dataclass
import os
from pathlib import Path

from clipforge.config import ROOT_DIR


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    logs_dir: Path
    live_recordings_dir: Path
    live_clips_dir: Path
    rendered_clips_dir: Path
    sources_dir: Path
    youtube_ledger: Path

    def ensure(self) -> 'WorkspacePaths':
        for folder in (
            self.root,
            self.logs_dir,
            self.live_recordings_dir,
            self.live_clips_dir,
            self.rendered_clips_dir,
            self.sources_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        return self

    def to_dict(self) -> dict[str, str]:
        return {
            'root': str(self.root),
            'logs_dir': str(self.logs_dir),
            'live_recordings_dir': str(self.live_recordings_dir),
            'live_clips_dir': str(self.live_clips_dir),
            'rendered_clips_dir': str(self.rendered_clips_dir),
            'sources_dir': str(self.sources_dir),
            'log_file': str(self.logs_dir / 'clipforge.log'),
            'errors_file': str(self.logs_dir / 'errors.jsonl'),
        }


def resolve_workspace(base_dir: Path | None = None) -> WorkspacePaths:
    if base_dir is not None:
        root = base_dir.resolve()
    else:
        env_dir = os.environ.get('CLIPFORGE_DATA_DIR', '').strip()
        root = Path(env_dir).expanduser().resolve() if env_dir else (ROOT_DIR / 'workspace').resolve()

    paths = WorkspacePaths(
        root=root,
        logs_dir=root / 'logs',
        live_recordings_dir=root / 'live_recordings',
        live_clips_dir=root / 'live_clips',
        rendered_clips_dir=root / 'rendered_clips',
        sources_dir=root / 'sources',
        youtube_ledger=root / 'youtube.sqlite3',
    )
    return paths.ensure()
