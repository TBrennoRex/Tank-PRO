"""Offline transport regression tests; Windows imports are skipped off-Windows."""
import ast
from pathlib import Path

winrt_source = Path('winrt_midi.py').read_text()
winmm_source = Path('winmm_midi.py').read_text()

# PyWinRT 3.x requires a bytes-like buffer for
# CryptographicBuffer.create_from_byte_array(). Keep this regression check
# executable on Linux/macOS, where the WinRT module itself is unavailable.
assert 'create_from_byte_array(payload)' in winrt_source
assert 'payload = bytes(data)' in winrt_source
assert 'create_from_byte_array(list(data))' not in winrt_source
assert 'midiOutLongMsg' in winmm_source
assert 'midiInAddBuffer' in winmm_source
assert 'MIM_LONGDATA' in winmm_source

ast.parse(winrt_source)
ast.parse(winmm_source)
print('transport source parse: OK')
print('WinMM SysEx transport regression: OK')
print('transport SysEx buffer regression: OK')
