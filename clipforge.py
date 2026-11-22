"""Root compatibility wrapper so `python clipforge.py ...` works without editable install."""

from pathlib import Path
import sys

_SRC = str(Path(__file__).resolve().parent / 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from clipforge.cli import approve, main, manual_ranges, read_run  # noqa: E402, F401

if __name__ == '__main__':
    main()
