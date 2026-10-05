"""Bounded zlib.v1 operations without a scheduling handoff per small chunk.

PyDLL retains the GIL during these short, bounded operations. Python still
schedules threads between calls. The portable fallback uses the same format
and rejects incomplete streams, trailing bytes and incorrect expanded lengths.
"""
import ctypes
import zlib

MAX_CHUNK = 1024 * 1024
try:
    _lib = ctypes.PyDLL('libz.so.1')
    _bound = _lib.compressBound
    _bound.argtypes = [ctypes.c_ulong]
    _bound.restype = ctypes.c_ulong
    _compress = _lib.compress2
    _compress.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
                         ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    _compress.restype = ctypes.c_int
    _expand = _lib.uncompress2
    _expand.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _expand.restype = ctypes.c_int
except (OSError, AttributeError):
    _lib = None

NATIVE = _lib is not None


def compress(data, level=6):
    if not isinstance(data, bytes) or len(data) > MAX_CHUNK:
        raise ValueError('evidence_compression_input_invalid')
    if not NATIVE:
        return zlib.compress(data, level)
    length = ctypes.c_ulong(_bound(len(data)))
    output = ctypes.create_string_buffer(length.value)
    status = _compress(output, ctypes.byref(length), ctypes.c_char_p(data), len(data), level)
    if status:
        raise zlib.error('evidence_compression_failed:' + str(status))
    return output.raw[:length.value]


def decompress(payload, length):
    if not isinstance(payload, bytes) or type(length) is not int or not 0 <= length <= MAX_CHUNK:
        raise ValueError('evidence_decompression_input_invalid')
    if not NATIVE:
        decoder = zlib.decompressobj()
        data = decoder.decompress(payload, length + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(data) != length:
            raise zlib.error('evidence_expanded_length_invalid')
        return data
    size = ctypes.c_ulong(length + 1)
    consumed = ctypes.c_ulong(len(payload))
    output = ctypes.create_string_buffer(size.value)
    status = _expand(output, ctypes.byref(size), ctypes.c_char_p(payload), ctypes.byref(consumed))
    if status or size.value != length or consumed.value != len(payload):
        raise zlib.error('evidence_expanded_length_invalid')
    return output.raw[:size.value]
