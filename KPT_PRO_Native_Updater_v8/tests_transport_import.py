"""Offline transport regression tests; WinRT import is skipped off-Windows."""
import ast
from pathlib import Path

source = Path('winrt_midi.py').read_text()

# PyWinRT 3.x requires a bytes-like buffer for
# CryptographicBuffer.create_from_byte_array(). Keep this regression check
# executable on Linux/macOS, where the WinRT module itself is unavailable.
assert 'create_from_byte_array(payload)' in source
assert 'payload = bytes(data)' in source
assert 'create_from_byte_array(list(data))' not in source

ast.parse(source)
print('transport source parse: OK')
print('transport SysEx buffer regression: OK')
