"""Authoritative camera text for local edits of an existing scene."""


def camera_contract(scene):
    c=scene['camera']
    if c.get('path'):
        movement='Follow the explicit world-space camera path in the motion reference.'
    elif c['mode']=='follow':
        movement=f"Follow behind {c['target']} at {c['distance']:g} m distance and {c['height']:g} m height, matching its travel speed."
    else:
        movement=f"Use the {c['mode']} camera trajectory shown in the motion reference."
    return f"{movement} Keep a fixed {c['lens']:g} mm lens and the same subject framing. The camera motion is continuous across clip boundaries."
