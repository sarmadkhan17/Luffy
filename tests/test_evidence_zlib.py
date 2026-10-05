import hashlib
import os
import zlib

import pytest

from trader.core import evidence_zlib as C, journal_evidence as E


@pytest.mark.parametrize('native', [False, True])
def test_codec_matches_installed_zlib_and_rejects_corruption(monkeypatch, native):
    if native and C._lib is None:
        pytest.skip('native libz unavailable')
    monkeypatch.setattr(C, 'NATIVE', native)
    for data in (b'', b'a' * 131072, os.urandom(131072), '🌊'.encode() * 32768):
        payload = C.compress(data)
        assert payload == zlib.compress(data, 6)
        assert zlib.decompress(payload) == data
        assert C.decompress(payload, len(data)) == data
        sha = hashlib.sha256(data).hexdigest()
        assert E._decode_blob((len(data), 'zlib.v1', payload), sha) == data
        for corrupt in (payload[:-1], payload + b'junk', payload + payload):
            with pytest.raises(E.EvidenceError):
                E._decode_blob((len(data), 'zlib.v1', corrupt), sha)
        with pytest.raises(E.EvidenceError):
            E._decode_blob((len(data) + 1, 'zlib.v1', payload), sha)
        with pytest.raises(E.EvidenceError):
            E._decode_blob((len(data), 'zlib.v1', payload), '0' * 64)
    with pytest.raises(E.EvidenceError):
        E._decode_blob((1, 'zlib.v1', zlib.compress(b'x' * (C.MAX_CHUNK + 1))), '0' * 64)
    with pytest.raises(ValueError):
        C.compress(b'x' * (C.MAX_CHUNK + 1))
