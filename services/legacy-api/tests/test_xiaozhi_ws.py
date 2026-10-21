import struct

from api.index import app
from api.routes.xiaozhi_ws import _decode_audio_frame


def test_xiaozhi_v1_audio_frame_is_raw_opus():
    packet = b"\xf8\xff\xfe"
    assert _decode_audio_frame(packet, 1) == packet


def test_xiaozhi_v2_audio_frame_extracts_opus_payload():
    packet = b"\xf8\xff\xfe"
    frame = struct.pack("!HHIII", 2, 0, 0, 1234, len(packet)) + packet
    assert _decode_audio_frame(frame, 2) == packet


def test_xiaozhi_v2_rejects_malformed_frame():
    assert _decode_audio_frame(b"short", 2) is None
    assert _decode_audio_frame(struct.pack("!HHIII", 2, 1, 0, 0, 0), 2) is None


def test_xiaozhi_websocket_paths_are_mounted():
    paths = {route.path for route in app.routes}
    assert "/xiaozhi/v1/" in paths
    assert "/xiaozhi/v1" in paths
