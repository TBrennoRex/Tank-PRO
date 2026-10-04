"""Windows.Devices.Midi transport for the KPT PRO updater.

Uses the Windows Runtime MIDI API instead of WinMM.  The enumeration code is
written to tolerate the different PyWinRT overloads shipped across releases.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:
    from winrt.system import Array
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.devices.midi import MidiInPort, MidiOutPort, MidiSystemExclusiveMessage
    from winrt.windows.security.cryptography import CryptographicBuffer
except ImportError as exc:
    raise RuntimeError(
        "Falta o backend Windows.Devices.Midi. Execute:\n"
        "  py -3 -m pip install -r requirements.txt\n"
        "e abra o updater novamente."
    ) from exc


@dataclass
class Port:
    name: str
    direction: str
    device_id: str
    index: int

    def __str__(self) -> str:
        return f"{self.index}: {self.name}"


def _selector(kind):
    """Get the MIDI AQS selector, tolerating projection quirks."""
    fn = getattr(kind, "get_device_selector", None)
    if fn is None:
        fn = getattr(kind, "get_device_selector_aqs_filter", None)
    if fn is None:
        raise RuntimeError(f"A classe {kind!r} não expõe get_device_selector().")
    return fn()


async def _find(kind, diagnostic: Optional[Callable[[str], None]] = None):
    selector = _selector(kind)

    # PyWinRT 3.x exposes WinRT overloads as distinct Python method names.
    # DeviceInformation.FindAllAsync(String) is projected as
    # find_all_async_aqs_filter(), while the 2-argument overload is projected
    # as find_all_async_aqs_filter_and_additional_properties().  Calling the
    # generic find_all_async() is not reliable across projections.
    candidates = []
    explicit_one = getattr(DeviceInformation, "find_all_async_aqs_filter", None)
    explicit_two = getattr(
        DeviceInformation,
        "find_all_async_aqs_filter_and_additional_properties",
        None,
    )
    generic = getattr(DeviceInformation, "find_all_async", None)

    if explicit_two is not None:
        candidates.append((
            "aqs+properties",
            lambda: explicit_two(selector, Array(str, [])),
        ))
    if explicit_one is not None:
        candidates.append(("aqs", lambda: explicit_one(selector)))
    if generic is not None:
        candidates.append(("generic+properties", lambda: generic(selector, Array(str, []))))
        candidates.append(("generic", lambda: generic(selector)))

    errors = []
    for label, call in candidates:
        try:
            result = call()
            if hasattr(result, "__await__"):
                result = await result
            if diagnostic:
                diagnostic(f"WinRT enumeração: assinatura {label} OK ({len(result)} itens)")
            return result
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "Falha enumerando MIDI via Windows.Devices.Enumeration:\n"
        + ("\n".join(errors) if errors else "nenhuma assinatura disponível")
    )


async def _list_ports(diagnostic: Optional[Callable[[str], None]] = None):
    ins = await _find(MidiInPort, diagnostic)
    outs = await _find(MidiOutPort, diagnostic)
    ports = []
    for i, d in enumerate(outs):
        ports.append(Port(d.name or "", "OUT", d.id, i))
    for i, d in enumerate(ins):
        ports.append(Port(d.name or "", "IN", d.id, i))
    return ports


def list_ports(diagnostic: Optional[Callable[[str], None]] = None):
    return asyncio.run(_list_ports(diagnostic))


class MidiLink:
    """Synchronous facade over a dedicated asyncio/WinRT MIDI thread."""

    def __init__(self, out_port: Port | str, in_port: Port | str):
        self.out_id = out_port.device_id if isinstance(out_port, Port) else str(out_port)
        self.in_id = in_port.device_id if isinstance(in_port, Port) else str(in_port)
        self.q: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._thread = threading.Thread(target=self._thread_main, name="KPT-WinRT-MIDI", daemon=True)
        self._loop = None
        self._thread.start()
        if not self._ready.wait(10):
            raise TimeoutError("Windows.Devices.Midi não abriu as portas em 10 s")
        if self._startup_error:
            raise RuntimeError(f"Falha abrindo Windows.Devices.Midi: {type(self._startup_error).__name__}: {self._startup_error}")

    def _thread_main(self):
        try:
            asyncio.run(self._async_main())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    async def _async_main(self):
        self._loop = asyncio.get_running_loop()
        in_port = None
        out_port = None
        token = None
        try:
            in_port = await MidiInPort.from_id_async(self.in_id)
            out_port = await MidiOutPort.from_id_async(self.out_id)
            if in_port is None or out_port is None:
                raise RuntimeError("MidiInPort/MidiOutPort retornou None")

            def on_message(sender, args):
                try:
                    raw = CryptographicBuffer.copy_to_byte_array(args.message.raw_data)
                    if raw:
                        self.q.put(bytes(raw))
                except Exception as exc:
                    self.q.put(f"__ERROR__{type(exc).__name__}: {exc}".encode("utf-8", "replace"))

            token = in_port.add_message_received(on_message)
            self._in_port = in_port
            self._out_port = out_port
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
        finally:
            try:
                if in_port is not None and token is not None:
                    in_port.remove_message_received(token)
            except Exception:
                pass
            for p in (in_port, out_port):
                try:
                    if p is not None:
                        p.close()
                except Exception:
                    pass

    async def _send_async(self, data: bytes):
        # PyWinRT 3.x projects CreateFromByteArray as a buffer-view
        # conversion. Passing list[int] raises ``bytes-like object required``
        # before anything reaches the MIDI device; keep the SysEx payload as
        # a real contiguous bytes object instead.
        payload = bytes(data)
        buf = CryptographicBuffer.create_from_byte_array(payload)
        msg = MidiSystemExclusiveMessage(buf)
        self._out_port.send_message(msg)

    def send(self, data: bytes):
        if self._loop is None:
            raise RuntimeError("MIDI loop não está ativo")
        fut = asyncio.run_coroutine_threadsafe(self._send_async(data), self._loop)
        fut.result(timeout=10)

    def recv(self, timeout=1.0):
        try:
            item = self.q.get(timeout=timeout)
        except queue.Empty:
            return None
        if item.startswith(b"__ERROR__"):
            raise RuntimeError(item.decode("utf-8", "replace"))
        return item

    def drain(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def close(self):
        self._stop.set()
        self._thread.join(timeout=3)
        self._loop = None
