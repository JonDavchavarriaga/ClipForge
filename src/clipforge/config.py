import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT_DIR / '.agents'
MEMORY_DIR = AGENTS_DIR / 'memory'
HANDOFFS_DIR = AGENTS_DIR / 'handoffs'
CHANNEL_PROFILES_PATH = MEMORY_DIR / 'channel-profiles.json'
GAMING_GLOSSARY_PATH = MEMORY_DIR / 'glossary-gaming.md'
DEFAULT_OUTPUTS_DIR = Path('salidas')
DEFAULT_SOURCES_DIR = Path('fuentes')


def load_channel_profiles(path: Path | None = None) -> dict:
    """Load configured channel profiles from .agents/memory/channel-profiles.json."""
    target = path or CHANNEL_PROFILES_PATH
    if not target.is_file():
        return {'channels': {}}
    return json.loads(target.read_text(encoding='utf-8'))


def get_channel_profile(channel_key: str, path: Path | None = None) -> dict:
    """Return a single channel profile by key or raise ValueError."""
    data = load_channel_profiles(path)
    channels = data.get('channels', {})
    if channel_key not in channels:
        available = ', '.join(sorted(channels)) or 'ninguno'
        raise ValueError(f'Perfil de canal "{channel_key}" no encontrado. Disponibles: {available}.')
    return channels[channel_key]
