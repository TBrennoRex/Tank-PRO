from protocol import *
assert SUPPORTED_KPT_VERSIONS == frozenset({9, 14})
for n in range(700):
    s=bytes((n*17+i*31)&255 for i in range(n))
    assert unpack7(pack7(s))==s
body=b'\x00\x59\x30'+(8).to_bytes(3,'little')+b'\x00'+(0x123456).to_bytes(4,'little')+(0x200).to_bytes(3,'little')
p=b'\xf0'+pack7(body+bytes([checksum8(body[6:])]))+b'\xf7'
assert parse_request(p)==(0,0x123456,0x200)
assert build_success(DONE_VERIFY).startswith(b'\xf0')
print('protocol tests: OK')
