"""Specialist model wrapper — preprocessing exactness + graceful degradation.

These tests deliberately need NO torch and NO .pt weights: they lock down the
exact input pipeline (shape, normalization, temporal sampling) and prove the
bank degrades gracefully when the models are absent (as on any machine without
the ChatGPT branch's checkpoints)."""
import numpy as np

from app.specialist import (FRAME_SIZE, MEAN, NUM_FRAMES, STD, SpecialistBank,
                            SpecialistModel, preprocess_clip)


def _frame(val=128):
    return np.full((64, 80, 3), val, dtype=np.uint8)   # arbitrary HxWx3


def test_preprocess_shape_is_batch_ready():
    clip = preprocess_clip([_frame() for _ in range(20)])
    assert clip.shape == (1, 3, NUM_FRAMES, FRAME_SIZE, FRAME_SIZE)
    assert clip.dtype == np.float32


def test_preprocess_pads_short_clips_to_16():
    clip = preprocess_clip([_frame()])          # single frame -> repeated
    assert clip.shape == (1, 3, NUM_FRAMES, FRAME_SIZE, FRAME_SIZE)


def test_preprocess_applies_exact_normalization():
    # a uniform mid-grey frame: after /255 then (x-mean)/std we can predict it
    val = 128
    clip = preprocess_clip([_frame(val)], bgr=False)
    scaled = val / 255.0
    for ch in range(3):
        expected = (scaled - MEAN[ch]) / STD[ch]
        got = clip[0, ch].mean()
        assert abs(got - expected) < 1e-4, (ch, got, expected)


def test_preprocess_bgr_to_rgb_channel_swap():
    # a pure-blue BGR frame (B=255 at BGR channel 0) must land in the BLUE
    # output channel (RGB channel 2) after the swap, leaving R (channel 0) at 0
    bgr = np.zeros((10, 10, 3), dtype=np.uint8)
    bgr[..., 0] = 255                           # blue in BGR
    clip = preprocess_clip([bgr], bgr=True)
    red_pre = (0.0 - MEAN[0]) / STD[0]          # R channel sees value 0
    blue_pre = (255 / 255.0 - MEAN[2]) / STD[2]  # B channel sees value 255
    assert abs(clip[0, 0].mean() - red_pre) < 1e-3   # channel 0 = R = 0
    assert abs(clip[0, 2].mean() - blue_pre) < 1e-3  # channel 2 = B = 255


def test_preprocess_temporal_sampling_is_even():
    # 32 distinct frames -> linspace(0,31,16) picks indices 0,2,4,...,30
    frames = [_frame(i) for i in range(32)]
    clip = preprocess_clip(frames, bgr=False)
    # channel 0, first pixel, across T: recover original values via inverse norm
    t_vals = clip[0, 0, :, 0, 0] * STD[0] + MEAN[0]
    approx = np.round(t_vals * 255).astype(int)
    assert approx[0] == 0 and approx[-1] == 31       # spans full clip
    assert len(approx) == NUM_FRAMES


def test_preprocess_empty_raises():
    try:
        preprocess_clip([])
        assert False
    except ValueError:
        pass


def test_model_missing_weights_degrades():
    m = SpecialistModel(name="break_in", weights_path="/does/not/exist.pt",
                        classes=("HOUSE_BREAK_IN", "NORMAL"),
                        suspicious_class="HOUSE_BREAK_IN")
    assert m.load() is False
    assert m.available is False
    assert m.probability([_frame()]) is None
    assert m.load_error                      # a reason was recorded


def test_bank_disabled_returns_none():
    bank = SpecialistBank({"enabled": False})
    assert bank.score([_frame()], which="break_in") is None
    assert bank.warmup() == {"break_in": False, "vehicle": False}


def test_bank_enabled_but_no_weights_is_safe():
    bank = SpecialistBank({"enabled": True,
                           "break_in": {"weights": "/nope.pt"},
                           "vehicle": {"weights": "/nope.pt"}})
    # no crash, just unavailable
    assert bank.warmup() == {"break_in": False, "vehicle": False}
    assert bank.score([_frame()], which="vehicle") is None


def test_bank_rejects_unknown_model():
    bank = SpecialistBank({"enabled": True})
    try:
        bank.score([_frame()], which="airplane")
        assert False
    except ValueError:
        pass
