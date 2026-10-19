"""Reusable stylized locomotion clips, sampled by travelled metres.

Two steps per stride. These blockout clips deliberately make no mocap or
photorealism claim. The director selects a clip; it never generates bone code.
"""
import math

STRIDE = {'run': 2.4, 'walk': 1.3}


def evaluate(clip, distance):
    phase = distance / STRIDE.get(clip, 1)
    s, c = math.sin(2*math.pi*phase), math.cos(2*math.pi*phase)
    bones = {'spine': (8 if clip == 'run' else 3, 0, 0)}
    if clip == 'idle':
        return {'bones': {}, 'hips': (0,0,0)}, 0
    run = clip == 'run'
    for side, sign in [('L',1), ('R',-1)]:
        swing = s*sign
        knee = max(0, c*sign)
        thigh = -(48 if run else 24)*swing
        shin = 8+(95 if run else 45)*knee*knee
        bones.update({f'thigh.{side}': (thigh,0,0), f'shin.{side}': (shin,0,0),
                      f'foot.{side}': (-.65*(thigh+shin),0,0),
                      f'upper_arm.{side}': ((42 if run else 20)*swing,-5*sign,0),
                      f'forearm.{side}': (-85 if run else -20,0,0)})
    return {'bones':bones, 'hips':(0,0,.035*(1-c*c))}, phase
