import asyncio
import copy
import hashlib
import json
import struct

import pytest
from gateway.motion_director.choreography import fight_study
from gateway.motion_director.scene import SceneSpec
from gateway.motion_director.animation_library import ROOT, CLIPS
from gateway.motion_director.blender.validation import validate
from gateway.motion_director import media
from gateway.motion_director.planner import LocalDirector, ShotRequest
from gateway.motion_director.renderers import build_h3_graph


def test_native_assets_have_original_keyframes_and_verified_content():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    available=set()
    for stem,record in manifest.items():
        raw=(ROOT/(stem+'.glb')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==record['sha256']
        n=struct.unpack_from('<I',raw,12)[0];data=json.loads(raw[20:20+n])
        assert data['skins'] and data['meshes']
        available.update(a['name'] for a in data['animations'])
        assert all(a['channels'] and a['samplers'] for a in data['animations'])
    assert all(c[1] in available for c in CLIPS.values())


@pytest.mark.parametrize('defect',['overlap','missing-reaction','bad-spacing','missing-camera-subject','wrong-rig','unknown-facing','unsupported-kick'])
def test_choreography_rejects_unexecutable_sequences(defect):
    scene=fight_study().model_dump()
    if defect=='overlap':scene['entities'][0]['actions'][1]['start']=.2
    if defect=='missing-reaction':scene['entities'][1]['actions']=[a for a in scene['entities'][1]['actions'] if a['clip']!='hit_head']
    if defect=='bad-spacing':scene['interactions'][0]['time']=.5
    if defect=='missing-camera-subject':scene['camera']['include']=[]
    if defect=='wrong-rig':scene['entities'][0]['rig']='procedural-v1'
    if defect=='unknown-facing':scene['entities'][0]['facing']='unknown'
    if defect=='unsupported-kick':scene['entities'][0]['actions'][1]['clip']='kick'
    with pytest.raises(ValueError):SceneSpec.model_validate(scene).validate_timeline(5)


def test_full_motion_export_and_verified_draft_appearance_recipe(tmp_path,monkeypatch):
    calls=[]
    async def command(*args,**kwargs):calls.append(args)
    monkeypatch.setattr(media,'command',command)
    asyncio.run(media.scene_window(tmp_path/'scene.mp4',tmp_path,0,384,672,'draft',full_motion=True))
    assert calls[0][calls[0].index('-frames:v')+1]=='124'
    assert 'fps=24' in calls[0][calls[0].index('-vf')+1]
    shot=LocalDirector().plan(ShotRequest(prompt='the characters start fighting',reference_ids=['r'],duration=5))
    shot.scene=fight_study().model_dump()
    graph=build_h3_graph(shot,['original.png'],'study',scene_video='full-motion.mp4')
    assert graph['136']['inputs']['ref_images.ref_image_0']==['scale_0',0]
    assert graph['136']['inputs']['ref_videos.ref_video_0']==['blender_frames',0]
    assert 'appearance_guide' not in graph
    assert graph['126']['inputs']['conditioning']==['136',0]
    assert '22 frames' in graph['138']['inputs']['value']


def test_evaluated_contact_failure_is_a_real_generation_blocker():
    scene=fight_study().model_dump()
    joints={n:[0,0,1] for n in ('hand_l','hand_r','Head','spine_03','ball_l','ball_r')}
    joints['ball_l']=joints['ball_r']=[0,0,.015]
    frame={'time':1.3,'camera':[0,-5,2], 'framing':{'left':.1,'right':.9,'bottom':.1,'top':.9,'depth':4},
           'entities':{'hero':{'joints':copy.deepcopy(joints)},'alien':{'joints':copy.deepcopy(joints)}}}
    frame['entities']['hero']['joints']['hand_l']=[10,0,1]
    assert not validate(scene,[frame,copy.deepcopy(frame)])['valid']


def test_short_authored_guard_hold_does_not_require_two_unused_crossfades():
    scene=fight_study().model_dump()
    scene['entities'][0]['actions'].insert(1,{'clip':'guard','start':.7,'end':.85,'blend':.1})
    assert SceneSpec.model_validate(scene).validate_timeline(5)


def test_planning_schema_separates_rig_scale_from_prop_dimensions():
    from gateway.motion_director.claude_host import planning_schema
    character,prop=planning_schema(True)['$defs']['Entity']['anyOf']
    assert character['properties']['geometry']['const']=='mannequin'
    assert all(character['properties']['size']['properties'][axis]['const']==1 for axis in 'xyz')
    assert 'mannequin' not in prop['properties']['geometry']['enum']
    assert prop['properties']['size']=={'$ref':'#/$defs/Vec3'}
