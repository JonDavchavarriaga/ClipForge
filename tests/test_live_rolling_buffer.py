import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from clipforge.pipeline import live
from clipforge.pipeline.live import LiveRecorder


class RollingBufferTest(unittest.TestCase):
    def _run_monitor(self, save_full: bool, total_segments: int = 250):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            buffer_dir = root / 'session' / '_buffer'
            buffer_dir.mkdir(parents=True)
            for i in range(total_segments):
                (buffer_dir / f'seg_{i:06d}.ts').write_bytes(b'x')
            recorder = LiveRecorder(root, segment_seconds=5, buffer_minutes=15)
            recorder._buffer_dir = buffer_dir
            recorder._save_full_recording = save_full
            with mock.patch.object(live, 'measure_segment_db', return_value=-30.0):
                thread = threading.Thread(target=recorder._monitor_loop, daemon=True)
                thread.start()
                time.sleep(3.5)
                recorder._stop_event.set()
                thread.join(timeout=5)
            remaining = sorted(p.name for p in buffer_dir.glob('seg_*.ts'))
            return remaining

    def test_default_discards_segments_older_than_15_minutes(self):
        self.assertFalse(LiveRecorder(Path(tempfile.gettempdir()))._save_full_recording)
        remaining = self._run_monitor(save_full=False)
        keep = (15 * 60) // 5 + 2  # 182 segmentos de 5 s ≈ 15 min
        self.assertEqual(len(remaining), keep)
        self.assertEqual(remaining[-1], 'seg_000249.ts')  # lo más nuevo se conserva
        self.assertEqual(remaining[0], f'seg_{250 - keep:06d}.ts')  # lo más viejo se borró

    def test_full_recording_keeps_everything(self):
        self.assertEqual(len(self._run_monitor(save_full=True)), 250)


if __name__ == '__main__':
    unittest.main()
