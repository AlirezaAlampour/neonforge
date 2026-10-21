"""Validate evaluated skeletal output, rather than assuming a schema means contact."""
import math


def validate(spec, frames):
    errors=[];contacts=[]
    for beat in spec['interactions']:
        frame=min(frames,key=lambda f:abs(f['time']-beat['time']))
        actor=frame['entities'][beat['actor']]['joints'];target=frame['entities'][beat['target']]['joints']
        joint='Head' if beat['zone']=='head' else 'spine_03'
        distance=math.dist(actor[beat['limb']],target[joint])
        valid=distance<=.36 if beat['outcome']=='hit' else distance>=.2
        contacts.append({**beat,'sample_time':frame['time'],'distance_m':round(distance,4),'valid':valid})
        if not valid:errors.append(f"{beat['actor']} → {beat['target']} at {beat['time']:g}s: {beat['outcome']} contact distance {distance:.2f}m")
    speed=max(math.dist(a['camera'],b['camera'])*24 for a,b in zip(frames,frames[1:]))
    if speed>3:errors.append(f'Camera speed {speed:.2f}m/s exceeds controlled fight framing')
    for f in frames:
        bounds=f.get('framing')
        if bounds and (bounds['left']<.035 or bounds['right']>.965 or bounds['bottom']<.035 or bounds['top']>.965 or bounds['depth']<=0):
            errors.append(f"Characters leave safe camera margins at {f['time']:.2f}s");break
    for entity in spec['entities']:
        if entity.get('rig')!='quaternius-v1':continue
        height=(entity.get('character') or {}).get('height_m',1.8)/1.8*entity['size']['x']
        for f in frames:
            joints=f['entities'][entity['id']]['joints']
            soles=[joints['ball_'+s][2]-.015*height for s in ('l','r')]
            if min(soles)<entity['position']['z']-.025:
                errors.append(f"{entity['id']} penetrates the ground at {f['time']:.2f}s");break
    return {'valid':not errors,'errors':errors,'contacts':contacts,'max_camera_speed_m_s':round(speed,4),
            'frames_checked':len(frames),'limits':'Approximate joint reach and sole/camera checks; not a physics/contact simulation.'}
