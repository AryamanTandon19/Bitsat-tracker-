#!/usr/bin/env python3
"""Measure what a training step actually costs on THIS machine.

    python -m training.profile_gpu
    python -m training.profile_gpu --model r3d18 --sizes 1,2,4,8

Run it on the laptop before choosing a batch size. The plan's numbers are an
analytic estimate; this is the measurement, and the two are allowed to
disagree — driver version, cuDNN workspace and whatever else holds VRAM all
move the real figure.

It profiles a full training step (forward + loss + backward + optimizer), not
just a forward pass, because the backward pass is where the memory goes and a
model that infers happily at batch 16 may not train at batch 2.

The input shape is the one `app.specialist` actually feeds: [B, 3, 16, 128,
128]. Profiling a different shape would tell you about a model you are not
going to run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.specialist import FRAME_SIZE, NUM_FRAMES

# Leave headroom: Windows keeps a slice of VRAM for the desktop, and a run
# that peaks at 99% will die on the epoch where something else opens a window.
SAFE_FRACTION = 0.75


def build(model_name: str, torch):
    """Return (module, description). Only architectures that fit the plan."""
    import torch.nn as nn

    if model_name == "r3d18":
        from torchvision.models.video import r3d_18
        m = r3d_18(weights="KINETICS400_V1")
        m.fc = nn.Linear(m.fc.in_features, 2)
        return m, "torchvision r3d_18, Kinetics weights, 2-logit head"

    if model_name == "r3d18-frozen":
        from torchvision.models.video import r3d_18
        m = r3d_18(weights="KINETICS400_V1")
        for name, p in m.named_parameters():
            p.requires_grad = name.startswith(("layer3", "layer4", "fc"))
        m.fc = nn.Linear(m.fc.in_features, 2)
        return m, "r3d_18 with stem/layer1/layer2 frozen (the plan's Tier 2)"

    if model_name == "gru-head":
        # Tier 1: a frozen per-frame encoder plus a small temporal head. Only
        # the head trains, so this is the cheap end of the plan.
        from torchvision.models import resnet18
        enc = resnet18(weights="IMAGENET1K_V1")
        enc.fc = nn.Identity()
        for p in enc.parameters():
            p.requires_grad = False

        class FrozenEncoderGRU(nn.Module):
            def __init__(self):
                super().__init__()
                self.enc = enc
                self.gru = nn.GRU(512, 128, num_layers=2, batch_first=True)
                self.fc = nn.Linear(128, 2)

            def forward(self, x):               # x: [B,3,T,H,W]
                b, c, t, h, w = x.shape
                frames = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
                feats = self.enc(frames).reshape(b, t, -1)
                out, _ = self.gru(feats)
                return self.fc(out[:, -1])

        return FrozenEncoderGRU(), "frozen ResNet-18 per frame + 2-layer GRU head"

    raise SystemExit(f"unknown model {model_name!r}; "
                     "try r3d18, r3d18-frozen or gru-head")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="r3d18-frozen",
                   choices=("r3d18", "r3d18-frozen", "gru-head"))
    p.add_argument("--sizes", default="1,2,4,8")
    p.add_argument("--no-amp", action="store_true",
                   help="profile without mixed precision, for comparison")
    p.add_argument("--frames", type=int, default=NUM_FRAMES)
    p.add_argument("--size", type=int, default=FRAME_SIZE)
    args = p.parse_args()

    try:
        import torch
    except ImportError:
        print("torch is not installed here. Run this on the training laptop:")
        print("  python -m training.profile_gpu")
        return 1

    if not torch.cuda.is_available():
        print("No CUDA device visible — this would profile the CPU, which "
              "tells you nothing\nabout the 4050. Check your torch build:")
        print(f"  torch {torch.__version__}, cuda build "
              f"{getattr(torch.version, 'cuda', None)}")
        return 1

    dev = torch.device("cuda:0")
    total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    print(f"device : {torch.cuda.get_device_name(0)}  ({total:.1f} GB)")
    print(f"torch  : {torch.__version__} / cuda {torch.version.cuda}")
    print(f"input  : [B, 3, {args.frames}, {args.size}, {args.size}]  "
          f"(the shape app.specialist feeds)")
    print(f"amp    : {'off' if args.no_amp else 'on'}")

    model, desc = build(args.model, torch)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    frozen = sum(p.numel() for p in model.parameters() if not p.requires_grad)
    print(f"model  : {desc}")
    print(f"         {trainable/1e6:.1f}M trainable, {frozen/1e6:.1f}M frozen\n")
    model = model.to(dev)

    import torch.nn as nn
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                            lr=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    scaler = torch.amp.GradScaler("cuda", enabled=not args.no_amp)

    print(f"{'batch':>6}{'peak VRAM':>12}{'of total':>10}{'verdict':>26}")
    print("-" * 54)
    ok = []
    for b in [int(x) for x in args.sizes.split(",") if x.strip()]:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        try:
            x = torch.randn(b, 3, args.frames, args.size, args.size, device=dev)
            y = torch.randint(0, 2, (b,), device=dev)
            for _ in range(3):              # warm up, then measure steady state
                opt.zero_grad(set_to_none=True)
                with torch.amp.autocast("cuda", enabled=not args.no_amp):
                    loss = loss_fn(model(x), y)
                scaler.scale(loss).backward()
                scaler.step(opt)
                scaler.update()
            torch.cuda.synchronize()
            peak = torch.cuda.max_memory_allocated() / 1024 ** 3
            frac = peak / total
            verdict = ("comfortable" if frac < SAFE_FRACTION * 0.7 else
                       "fine" if frac < SAFE_FRACTION else
                       "too close to the edge")
            print(f"{b:>6}{peak:>10.2f} GB{frac*100:>9.0f}%{verdict:>26}")
            if frac < SAFE_FRACTION:
                ok.append(b)
            del x, y, loss
        except torch.cuda.OutOfMemoryError:
            print(f"{b:>6}{'—':>12}{'—':>10}{'out of memory':>26}")
            torch.cuda.empty_cache()
            break

    print()
    if ok:
        print(f"Use batch {max(ok)}. Anything larger either did not fit or "
              f"left less than\n{(1-SAFE_FRACTION)*100:.0f}% headroom, and a "
              "laptop needs that headroom for the desktop.")
        print("\nIf you want a larger effective batch, accumulate gradients "
              f"rather than raising\nthe batch: {max(ok)} x 4 accumulation "
              f"steps behaves like batch {max(ok)*4} at the memory\nof "
              f"{max(ok)}.")
    else:
        print("Nothing fit. Drop --size to 112, or use --model gru-head, "
              "which trains\nonly a small head over a frozen encoder.")
    print("\nOn Windows also set, before training:")
    print("  set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True")
    print("  DataLoader(..., num_workers=0)   # multiprocessing hangs on Windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
