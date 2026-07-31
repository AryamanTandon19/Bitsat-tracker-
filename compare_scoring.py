#!/usr/bin/env python3
"""Old fixed severity table vs the scoring layer, side by side.

    python compare_scoring.py

IMPORTANT — what this is and is not. The situations below are written from
what a society actually sees all day; they are NOT labelled footage. This is a
sanity check that the layer separates a resident at their own car from a
stranger at someone else's, and it will happily agree with itself. Turning it
into a real number needs Priority 2: 30-50 labelled clips in testset/clips/.
Treat the totals as "the direction is right", never as accuracy.
"""
from app.rules import (LOITERING, RESTRICTED_ZONE, SEVERITY,
                       UNAUTHORIZED_VEHICLE, UNIDENTIFIED_VEHICLE,
                       VEHICLE_CONTACT)
from app.scoring import score_event

# (label, is_really_an_incident, event_type, context)
CASES = [
    ("resident parks, walks to flat, 6pm",     False, LOITERING,
     {"registered": True, "dwell_s": 25}),
    ("resident loads boot for 3 min, 7pm",     False, LOITERING,
     {"registered": True, "dwell_s": 190, "at_vehicle": True}),
    ("delivery rider waits at gate, 1pm",      False, LOITERING, {"dwell_s": 70}),
    ("kids playing near cars, 5pm",            False, LOITERING, {"dwell_s": 120}),
    ("resident returns late, 11:40pm",         False, LOITERING,
     {"registered": True, "dwell_s": 40, "night": True}),
    ("visitor car arrives, 2pm",               False, UNAUTHORIZED_VEHICLE, {}),
    ("cab drops resident, 9pm",                False, UNAUTHORIZED_VEHICLE, {}),
    ("plate glare, resident's own car, 8pm",   False, UNIDENTIFIED_VEHICLE,
     {"plate_known": False}),
    ("guard walks his round, 2am",             False, LOITERING,
     {"night": True, "dwell_s": 30}),
    ("two cars parked close, no contact",      False, VEHICLE_CONTACT,
     {"registered": True}),

    ("stranger at a car, 3am, 4 min",          True,  LOITERING,
     {"night": True, "dwell_s": 240, "at_vehicle": True, "plate_known": False}),
    ("unknown car enters, 3am",                True,  UNAUTHORIZED_VEHICLE,
     {"night": True}),
    ("person in restricted area, 2am",         True,  RESTRICTED_ZONE,
     {"night": True, "restricted": True, "dwell_s": 60}),
    ("car struck, driver leaves, 11pm",        True,  VEHICLE_CONTACT,
     {"night": True, "at_vehicle": True}),
    ("stranger circling, 1am, unknown plate",  True,  UNAUTHORIZED_VEHICLE,
     {"night": True, "plate_known": False, "repeat": True}),
]

ALERT = {"MEDIUM", "HIGH"}


def main(cfg: dict | None = None) -> tuple[int, int, int, int]:
    print(f"{'situation':<40}{'old':<10}{'new':<10}{'score':<8}why")
    print("-" * 110)
    old_false = new_false = old_missed = new_missed = 0
    for label, real, etype, ctx in CASES:
        old = SEVERITY[etype]
        s = score_event(etype, ctx, cfg)
        tag = "INCIDENT " if real else "         "
        print(f"{label:<40}{old:<10}{s.severity:<10}{s.value:<8.2f}{tag}{s.explain()}")
        if real:
            old_missed += old not in ALERT
            new_missed += s.severity not in ALERT
        else:
            old_false += old in ALERT
            new_false += s.severity in ALERT

    normal = sum(1 for c in CASES if not c[1])
    real_n = len(CASES) - normal
    print("-" * 110)
    print(f"alerts raised on the {normal} everyday situations : "
          f"old {old_false}  ->  new {new_false}")
    print(f"incidents missed out of {real_n}                   : "
          f"old {old_missed}  ->  new {new_missed}")
    print("\nThese are written scenarios, not footage. See the module docstring.")
    return old_false, new_false, old_missed, new_missed


if __name__ == "__main__":
    main()
