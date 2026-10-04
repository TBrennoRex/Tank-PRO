"""KPT PRO OTA protocol implementation.

No M-UPGRADE dependency.
The wire format is the JieLi/M-VAVE USB-MIDI SysEx request/response format
validated from the reverse-engineered update path.
"""
from __future__ import annotations
import hashlib, re
from dataclasses import dataclass
from pathlib import Path

HS_QUERY = bytes.fromhex('F0 00 32 45 00 00 00 40 7F F7')
UPGRADE_CMD = bytes.fromhex('F0 22 24 35 7F F7')
DONE_VERIFY = 0xE0000000
DONE_UPGRADE = 0xF0000000
MAX_CHUNK = 512
KPT_FLASH_SIZE = 0x0F8000
SUPPORTED_KPT_VERSIONS = frozenset({9, 14})


def pack7(data: bytes) -> bytes:
    out = bytearray(); acc = 0; nbits = 0
    for b in data:
        acc |= b << nbits
        nbits += 8
        while nbits >= 7:
            out.append(acc & 0x7f)
            acc >>= 7; nbits -= 7
    if nbits:
        out.append(acc & 0x7f)
    return bytes(out)


def unpack7(data: bytes) -> bytes:
    out = bytearray(); acc = 0; nbits = 0
    for b in data:
        acc |= b << nbits
        nbits += 7
        while nbits >= 8:
            out.append(acc & 0xff)
            acc >>= 8; nbits -= 8
    return bytes(out)


def checksum8(data: bytes) -> int:
    return (-sum(data)) & 0xff


def build_response(addr: int, data: bytes, flash_type: int = 0) -> bytes:
    if len(data) > MAX_CHUNK:
        raise ValueError('requested block exceeds 512 bytes')
    body = (b'\x00\x59\x30' + (len(data)+8).to_bytes(3,'little') +
            bytes([flash_type & 0xff]) + addr.to_bytes(4,'little') +
            len(data).to_bytes(3,'little') + data)
    return b'\xf0' + pack7(body + bytes([checksum8(body[6:])])) + b'\xf7'


def build_success(addr: int) -> bytes:
    return build_response(addr, b'success\x00')


def parse_request(pkt: bytes):
    if len(pkt) < 4 or pkt[0] != 0xf0 or pkt[-1] != 0xf7:
        return None
    d = unpack7(pkt[1:-1])
    if len(d) != 15 or d[:3] != b'\x00\x59\x30':
        return None
    if int.from_bytes(d[3:6],'little') != 8:
        return None
    if d[14] != checksum8(d[6:14]):
        return None
    return d[6], int.from_bytes(d[7:11],'little'), int.from_bytes(d[11:14],'little')


def parse_identity(pkt: bytes):
    """Parse the stock JieLi/M-VAVE type-0x11 identity reply.

    The wire packet itself begins with F0 00 32 45 58 and contains a
    marker-stripped decoded body after MIDI 7-bit unpacking.  Some firmware
    versions store the model/version plainly (KPTPRO_014), while others use
    the legacy encoded 20-byte version field that the vendor updater decodes
    by adding ASCII '0'.
    """
    if not pkt or len(pkt) < 4 or pkt[0] != 0xF0 or pkt[-1] != 0xF7:
        return None
    if not pkt.startswith(b"\xF0\x00\x32\x45\x58"):
        # Be slightly more tolerant: accept only packets which unpack to the
        # expected type-0x11 identity header.
        try:
            decoded = unpack7(pkt[1:-1])
        except Exception:
            return None
        if len(decoded) < 6 or decoded[:3] != b"\x00\x59\x11":
            return None
    try:
        decoded = unpack7(pkt[1:-1])
    except Exception:
        return None
    if len(decoded) != 34 or decoded[:3] != b"\x00\x59\x11":
        return None
    body_len = int.from_bytes(decoded[3:6], 'little')
    if body_len != 27:
        return None
    # The checksum is over flash-type + address/length/payload in this frame;
    # for the identity packet it is simply decoded[6:-1].
    expected = checksum8(decoded[6:-1])
    if decoded[-1] != expected:
        return None

    payload = decoded[6:-1]
    # Plain form: MODEL_VERSION\0 padding.
    m = re.fullmatch(rb"([^_\x00]+)_([0-9]{1,4})\x00*", payload)
    if m:
        try:
            model = m.group(1).decode('ascii')
            return {'model': model, 'version': int(m.group(2)), 'raw': decoded,
                    'ota': model.lower().startswith('ota-')}
        except UnicodeDecodeError:
            return None

    # Legacy encoded form used by the vendor updater: bytes 14..33 encode the
    # decimal suffix as digit values and are restored by adding ASCII '0'.
    plain = decoded[6:31]
    sep = plain.find(b'_')
    if sep < 0:
        return None
    try:
        model = plain[:sep].decode('ascii')
    except UnicodeDecodeError:
        return None
    encoded = bytes((b + ord('0')) & 0xFF for b in decoded[14:34])
    m2 = re.match(rb'[0-9]+', encoded[sep + 1:])
    if not m2:
        return None
    return {'model': model, 'version': int(m2.group(0)), 'raw': decoded,
            'ota': model.lower().startswith('ota-')}

def fwsc_logical(raw: bytes) -> bytes:
    if len(raw) < 20*0x30:
        raise ValueError('arquivo FWSC pequeno demais')
    return b''.join(raw[i*0x30:i*0x30+0x2f] for i in range(20)) + raw[20*0x30:]


@dataclass
class FirmwareInfo:
    path: str
    version: int|None
    raw_size: int
    logical_size: int
    raw_sha256: str
    logical_sha256: str


def fwsc_product_from_markers(raw: bytes) -> str|None:
    """Decode the product/version identity embedded in the 20 FWSC markers.

    This is how the JieLi/M-VAVE .fwsc wrapper stores its package identity;
    the literal string need not appear in the outer file bytes.
    """
    if len(raw) < 20 * 0x30:
        return None
    chars=[]
    for i in range(20):
        marker=raw[i*0x30 + 0x2F]
        if marker == 0x7D:
            continue
        chars.append(chr((marker - i - 1) & 0xFF))
    product=''.join(chars).split('\x00',1)[0].strip()
    return product or None


def firmware_info(path: str) -> tuple[FirmwareInfo, bytes]:
    raw = Path(path).read_bytes()
    logical = fwsc_logical(raw)
    product = fwsc_product_from_markers(raw)
    # Do not require the literal ASCII name in the outer package.  The marker
    # identity is the authoritative FWSC package identity.  Keep a literal
    # search as a fallback for older/odd packages.
    m = re.search(rb'(?:ota-)?KPT\s*PRO[_ -]?(\d{1,4})', raw, re.I)
    if not m:
        m = re.search(rb'(?:ota-)?KPTPRO[_ -]?(\d{1,4})', raw, re.I)
    ver = int(m.group(1)) if m else None
    if product:
        pm = re.fullmatch(r'(?:ota-)?(KPT\s*PRO|KPTPRO)[_ -]?([0-9]{1,4})', product, re.I)
        if pm:
            ver = int(pm.group(2), 10)
    normalized_product = re.sub(r'[^A-Za-z0-9]+', '', product or '').upper()
    if normalized_product.startswith('KPTPRO'):
        pass
    elif b'KPTPRO' in raw.upper() or b'KPT PRO' in raw.upper():
        pass
    else:
        raise ValueError(f'identidade KPT PRO não encontrada no FWSC (product={product!r})')
    if ver not in SUPPORTED_KPT_VERSIONS:
        supported = ', '.join(str(v) for v in sorted(SUPPORTED_KPT_VERSIONS))
        raise ValueError(f'versão KPT PRO não autorizada nesta versão do updater: {ver!r} (permitidas: {supported})')
    return FirmwareInfo(str(Path(path).resolve()), ver, len(raw), len(logical),
                        hashlib.sha256(raw).hexdigest(), hashlib.sha256(logical).hexdigest()), logical

def protocol_self_test():
    for n in range(700):
        src = bytes((i*31+n) & 255 for i in range(n))
        assert unpack7(pack7(src)) == src
    raw = b'\x00\x59\x30'+(8).to_bytes(3,'little')+b'\x00'+(0x123456).to_bytes(4,'little')+(0x200).to_bytes(3,'little')
    pkt = b'\xf0'+pack7(raw+bytes([checksum8(raw[6:])]))+b'\xf7'
    assert parse_request(pkt) == (0,0x123456,0x200)
    assert build_success(DONE_VERIFY).startswith(b'\xf0')

if __name__ == '__main__':
    protocol_self_test(); print('KPT protocol self-test: OK')
