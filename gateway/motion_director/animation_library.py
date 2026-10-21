"""Approved authored clips on the bundled native Quaternius humanoid skeleton.

No automatic retargeting onto arbitrary user meshes. Keep native bind transforms
and weights; separate armature instances can share immutable animation data.
"""
from pathlib import Path

ROOT = Path(__file__).with_name('animation_assets')
# Source seconds. Guard holds an authored boxing stance, not generated motion.
CLIPS = {
    'guard': ('UAL1_Standard', 'Punch_Jab', 0, 0, 'stance'),
    'punch_jab': ('UAL1_Standard', 'Punch_Jab', 0, 26/30, 'attack'),
    'punch_cross': ('UAL1_Standard', 'Punch_Cross', 0, 1, 'attack'),
    'hook': ('UAL2_Standard', 'Melee_Hook', 0, None, 'attack'),
    'recover': ('UAL2_Standard', 'Melee_Hook_Rec', 0, None, 'recovery'),
    'hit_chest': ('UAL1_Standard', 'Hit_Chest', 0, 10/30, 'reaction'),
    'hit_head': ('UAL1_Standard', 'Hit_Head', 0, 13/30, 'reaction'),
    'hit_knockback': ('UAL2_Standard', 'Hit_Knockback', 0, None, 'reaction'),
    'dodge_crouch': ('UAL1_Standard', 'Crouch_Idle_Loop', 0, None, 'dodge'),
    'dodge_roll': ('UAL1_Standard', 'Roll', 0, 44/30, 'dodge'),
    'walk': ('UAL1_Standard', 'Walk_Loop', 0, 40/30, 'locomotion'),
    'jog': ('UAL1_Standard', 'Jog_Fwd_Loop', 0, 28/30, 'locomotion'),
}


def catalog():
    return [{'id': k, 'source_action': v[1], 'category': v[4], 'duration': None if v[3] is None else v[3]-v[2]}
            for k, v in CLIPS.items()]
