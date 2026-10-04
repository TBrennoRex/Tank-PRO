"""Native WinMM MIDI transport used by the original M-UPGRADE path.

This module intentionally uses the Windows Multimedia MIDI API directly,
without python-rtmidi, so it remains usable on CPython 3.14.  The port pair
matches the original log: midiOut port 1 (USB-Midi) and midiIn port 0.
"""
from __future__ import annotations

import ctypes
import queue
import time
from dataclasses import dataclass

if ctypes.sizeof(ctypes.c_void_p) == 8:
    DWORD_PTR = ctypes.c_uint64
else:
    DWORD_PTR = ctypes.c_uint32

UINT = ctypes.c_uint32
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
HANDLE = ctypes.c_void_p
CALLBACK_FUNCTION = 0x00030000
MIM_LONGDATA = 0x3C3
MHDR_DONE = 0x00000001


class MidiInCaps(ctypes.Structure):
    _fields_ = [
        ("wMid", WORD), ("wPid", WORD), ("vDriverVersion", DWORD),
        ("szPname", ctypes.c_char * 32), ("dwSupport", DWORD),
    ]


class MidiOutCaps(ctypes.Structure):
    _fields_ = [
        ("wMid", WORD), ("wPid", WORD), ("vDriverVersion", DWORD),
        ("szPname", ctypes.c_char * 32), ("wTechnology", WORD),
        ("wVoices", WORD), ("wNotes", WORD), ("wChannelMask", WORD),
        ("dwSupport", DWORD),
    ]


class MidiHdr(ctypes.Structure):
    pass


MidiHdr._fields_ = [
    ("lpData", ctypes.POINTER(ctypes.c_char)),
    ("dwBufferLength", DWORD),
    ("dwBytesRecorded", DWORD),
    ("dwUser", DWORD_PTR),
    ("dwFlags", DWORD),
    ("lpNext", ctypes.POINTER(MidiHdr)),
    ("reserved", DWORD_PTR),
    ("dwOffset", DWORD),
    ("dwReserved", DWORD_PTR * 8),
]


@dataclass
class Port:
    name: str
    direction: str
    device_id: str
    index: int

    def __str__(self) -> str:
        return f"{self.index}: {self.name}"


def _load_winmm():
    if ctypes.sizeof(ctypes.c_void_p) == 0:
        raise RuntimeError("Windows Multimedia MIDI indisponível")
    dll = ctypes.WinDLL("winmm")
    dll.midiInGetNumDevs.restype = UINT
    dll.midiOutGetNumDevs.restype = UINT
    dll.midiInGetDevCapsA.argtypes = [UINT, ctypes.POINTER(MidiInCaps), UINT]
    dll.midiInGetDevCapsA.restype = UINT
    dll.midiOutGetDevCapsA.argtypes = [UINT, ctypes.POINTER(MidiOutCaps), UINT]
    dll.midiOutGetDevCapsA.restype = UINT
    return dll


def list_ports(diagnostic=None):
    mm = _load_winmm()
    result = []
    for i in range(mm.midiOutGetNumDevs()):
        caps = MidiOutCaps()
        if mm.midiOutGetDevCapsA(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            name = bytes(caps.szPname).split(b"\0", 1)[0].decode("mbcs", "replace")
            result.append(Port(name, "OUT", str(i), i))
    for i in range(mm.midiInGetNumDevs()):
        caps = MidiInCaps()
        if mm.midiInGetDevCapsA(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            name = bytes(caps.szPname).split(b"\0", 1)[0].decode("mbcs", "replace")
            result.append(Port(name, "IN", str(i), i))
    return result


class MidiLink:
    def __init__(self, out_port: Port | str, in_port: Port | str):
        self.mm = _load_winmm()
        self.out_index = int(out_port.device_id if isinstance(out_port, Port) else out_port)
        self.in_index = int(in_port.device_id if isinstance(in_port, Port) else in_port)
        self.q: queue.Queue[bytes] = queue.Queue()
        self._in_handle = HANDLE()
        self._out_handle = HANDLE()
        self._in_buffer = ctypes.create_string_buffer(4096)
        self._in_header = MidiHdr()
        self._callback = ctypes.WINFUNCTYPE(None, HANDLE, UINT, DWORD_PTR, DWORD_PTR, DWORD_PTR)(self._on_midi)
        self._configure_api()
        self._open()

    def _configure_api(self):
        mm = self.mm
        mm.midiOutOpen.argtypes = [ctypes.POINTER(HANDLE), UINT, DWORD_PTR, DWORD_PTR, DWORD]
        mm.midiOutOpen.restype = UINT
        mm.midiOutClose.argtypes = [HANDLE]
        mm.midiOutClose.restype = UINT
        mm.midiOutPrepareHeader.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiOutPrepareHeader.restype = UINT
        mm.midiOutUnprepareHeader.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiOutUnprepareHeader.restype = UINT
        mm.midiOutLongMsg.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiOutLongMsg.restype = UINT
        mm.midiInOpen.argtypes = [ctypes.POINTER(HANDLE), UINT, DWORD_PTR, DWORD_PTR, DWORD]
        mm.midiInOpen.restype = UINT
        mm.midiInPrepareHeader.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiInPrepareHeader.restype = UINT
        mm.midiInUnprepareHeader.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiInUnprepareHeader.restype = UINT
        mm.midiInAddBuffer.argtypes = [HANDLE, ctypes.POINTER(MidiHdr), UINT]
        mm.midiInAddBuffer.restype = UINT
        mm.midiInStart.argtypes = [HANDLE]
        mm.midiInStart.restype = UINT
        mm.midiInStop.argtypes = [HANDLE]
        mm.midiInStop.restype = UINT
        mm.midiInReset.argtypes = [HANDLE]
        mm.midiInReset.restype = UINT
        mm.midiInClose.argtypes = [HANDLE]
        mm.midiInClose.restype = UINT

    def _check(self, code, operation):
        if code:
            raise OSError(f"{operation} falhou (WinMM {code})")

    def _open(self):
        self._check(self.mm.midiOutOpen(ctypes.byref(self._out_handle), self.out_index, 0, 0, 0), "midiOutOpen")
        try:
            callback_address = ctypes.cast(self._callback, ctypes.c_void_p).value
            self._check(self.mm.midiInOpen(ctypes.byref(self._in_handle), self.in_index,
                                            callback_address, 0,
                                            CALLBACK_FUNCTION), "midiInOpen")
            self._in_header.lpData = ctypes.cast(self._in_buffer, ctypes.POINTER(ctypes.c_char))
            self._in_header.dwBufferLength = ctypes.sizeof(self._in_buffer)
            self._check(self.mm.midiInPrepareHeader(self._in_handle, ctypes.byref(self._in_header), ctypes.sizeof(self._in_header)), "midiInPrepareHeader")
            self._check(self.mm.midiInAddBuffer(self._in_handle, ctypes.byref(self._in_header), ctypes.sizeof(self._in_header)), "midiInAddBuffer")
            self._check(self.mm.midiInStart(self._in_handle), "midiInStart")
        except Exception:
            self.close()
            raise

    def _on_midi(self, _handle, message, _instance, param1, _param2):
        if message != MIM_LONGDATA:
            return
        hdr = ctypes.cast(param1, ctypes.POINTER(MidiHdr)).contents
        n = min(int(hdr.dwBytesRecorded), int(hdr.dwBufferLength))
        if n:
            self.q.put(bytes(ctypes.string_at(hdr.lpData, n)))
        hdr.dwBytesRecorded = 0
        try:
            self.mm.midiInAddBuffer(self._in_handle, ctypes.byref(self._in_header), ctypes.sizeof(self._in_header))
        except Exception:
            pass

    def send(self, data: bytes):
        payload = bytes(data)
        storage = ctypes.create_string_buffer(payload, len(payload))
        hdr = MidiHdr()
        hdr.lpData = ctypes.cast(storage, ctypes.POINTER(ctypes.c_char))
        hdr.dwBufferLength = len(payload)
        self._check(self.mm.midiOutPrepareHeader(self._out_handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), "midiOutPrepareHeader")
        try:
            self._check(self.mm.midiOutLongMsg(self._out_handle, ctypes.byref(hdr), ctypes.sizeof(hdr)), "midiOutLongMsg")
            deadline = time.monotonic() + 5
            while not (hdr.dwFlags & MHDR_DONE):
                if time.monotonic() >= deadline:
                    raise TimeoutError("midiOutLongMsg não concluiu em 5 s")
                time.sleep(0.005)
        finally:
            self.mm.midiOutUnprepareHeader(self._out_handle, ctypes.byref(hdr), ctypes.sizeof(hdr))

    def recv(self, timeout=1.0):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def close(self):
        if getattr(self, "_in_handle", None):
            try: self.mm.midiInStop(self._in_handle); self.mm.midiInReset(self._in_handle)
            except Exception: pass
            try: self.mm.midiInUnprepareHeader(self._in_handle, ctypes.byref(self._in_header), ctypes.sizeof(self._in_header))
            except Exception: pass
            try: self.mm.midiInClose(self._in_handle)
            except Exception: pass
            self._in_handle = HANDLE()
        if getattr(self, "_out_handle", None):
            try: self.mm.midiOutClose(self._out_handle)
            except Exception: pass
            self._out_handle = HANDLE()
