"""Smart AI Review: finding parsing + keyframe sampling (no real API call)."""
import pytest

from app.vlm import VLMDescriber

cv2 = pytest.importorskip("cv2")
from app.analyze import VideoAnalyzer
from tests.make_sample_video import make


def test_parse_findings_valid_json():
    text = ('Here is what I found:\n[{"time_s": 3.5, "activity": "person breaks '
            'car window", "severity": "high"}, {"time_s": 8, "activity": '
            '"reaches inside", "severity": "MEDIUM"}]')
    out = VLMDescriber._parse_findings(text)
    assert len(out) == 2
    assert out[0] == {"time_s": 3.5, "activity": "person breaks car window",
                      "severity": "HIGH"}
    assert out[1]["severity"] == "MEDIUM"


def test_parse_findings_empty_and_garbage():
    assert VLMDescriber._parse_findings("[]") == []
    assert VLMDescriber._parse_findings("nothing suspicious here") == []
    assert VLMDescriber._parse_findings("[not json") == []


def test_parse_findings_bad_severity_defaults_medium():
    out = VLMDescriber._parse_findings('[{"time_s": 1, "activity": "x", "severity": "weird"}]')
    assert out[0]["severity"] == "MEDIUM"


def test_review_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = VLMDescriber({"enabled": True})
    assert not r.available
    assert r.review_video([(1.0, b"x")]) == []


def test_sample_keyframes(tmp_path):
    vid = str(tmp_path / "s.mp4")
    make(vid, seconds=4, fps=10)
    frames = VideoAnalyzer._sample_keyframes(vid, max_frames=8)
    assert 1 <= len(frames) <= 8
    # each is (time_s, jpeg_bytes), times increasing
    times = [t for t, _ in frames]
    assert times == sorted(times)
    assert all(isinstance(j, (bytes, bytearray)) and j[:2] == b"\xff\xd8"
               for _, j in frames)  # JPEG magic


def test_review_with_fake_client(monkeypatch):
    r = VLMDescriber({"enabled": True})

    class FakeBlock:
        type = "text"
        text = '[{"time_s": 2.0, "activity": "window smashed", "severity": "HIGH"}]'

    class FakeResp:
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kw):
            # verify images + prompt were assembled
            content = kw["messages"][0]["content"]
            assert any(c.get("type") == "image" for c in content)
            return FakeResp()

    class FakeClient:
        messages = FakeMessages()

    r._client = FakeClient()
    findings = r.review_video([(2.0, b"\xff\xd8fakejpeg")])
    assert findings == [{"time_s": 2.0, "activity": "window smashed",
                         "severity": "HIGH"}]
