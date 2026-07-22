"""Low-light enhancement — pure cv2/numpy, no torch."""
import numpy as np
from app.enhance import enhance_frame, _is_dark


def dark_frame():
    f = np.full((60, 80, 3), 15, np.uint8)   # near-black night frame
    f[10:30, 10:40] = 35                       # a slightly-lighter subject
    return f


def bright_frame():
    return np.full((60, 80, 3), 200, np.uint8)


def test_off_returns_same_object():
    f = dark_frame()
    assert enhance_frame(f, "off") is f


def test_on_brightens_a_dark_frame():
    f = dark_frame()
    out = enhance_frame(f, "on")
    assert out.mean() > f.mean()               # lifted the shadows
    assert out.shape == f.shape and out.dtype == np.uint8


def test_auto_enhances_dark_skips_bright():
    d = dark_frame()
    assert _is_dark(d, 110.0)
    assert enhance_frame(d, "auto").mean() > d.mean()
    b = bright_frame()
    assert not _is_dark(b, 110.0)
    assert (enhance_frame(b, "auto") == b).all()   # untouched


def test_locally_dark_scene_triggers_auto():
    # bright overall (road) but large dark region (car in shadow)
    f = np.full((100, 100, 3), 150, np.uint8)
    f[:, :60] = 20                              # 60% of pixels very dark
    assert _is_dark(f, 110.0)                   # global mean high, still "dark"
