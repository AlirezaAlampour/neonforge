"""Reproducibly retain selected authored CC0 glTF clips and skin data.

Run with uv after verifying uv.lock. Inputs are the official free Standard ZIPs
from https://quaternius.itch.io/universal-animation-library and its -2 sibling.
No motion is generated or reconstructed by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zipfile

SELECTED = {
    'UAL1_Standard': ['Idle_Loop', 'Walk_Loop', 'Jog_Fwd_Loop', 'Punch_Jab', 'Punch_Cross', 'Hit_Chest', 'Hit_Head', 'Crouch_Idle_Loop', 'Roll'],
    'UAL2_Standard': ['Melee_Hook', 'Melee_Hook_Rec', 'Hit_Knockback', 'Sword_Block'],
}


def subset(raw, names):
    size = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20+size])
    binary = raw[28+size:]
    doc['animations'] = [a for a in doc['animations'] if a['name'] in names]
    if set(names) != {a['name'] for a in doc['animations']}:
        raise ValueError('Official asset is missing selected authored clips')
    refs = []
    for a in doc['animations']:
        for s in a['samplers']:
            refs.extend((s, key) for key in ('input', 'output'))
    for mesh in doc['meshes']:
        for prim in mesh['primitives']:
            refs.extend((prim['attributes'], k) for k in prim['attributes'])
            if 'indices' in prim: refs.append((prim, 'indices'))
            for target in prim.get('targets', []):refs.extend((target, k) for k in target)
    refs.extend((s, 'inverseBindMatrices') for s in doc['skins'] if 'inverseBindMatrices' in s)
    keep = sorted({obj[key] for obj, key in refs})
    remap = {old: new for new, old in enumerate(keep)}
    doc['accessors'] = [doc['accessors'][i] for i in keep]
    for obj, key in refs: obj[key] = remap[obj[key]]
    views = []
    for accessor in doc['accessors']:
        if 'bufferView' in accessor: views.append((accessor, 'bufferView'))
        if 'sparse' in accessor:
            views.extend((accessor['sparse'][key], 'bufferView') for key in ('indices', 'values'))
    views.extend((i, 'bufferView') for i in doc.get('images', []) if 'bufferView' in i)
    packed = bytearray(); new_views = []; mapping = {}
    for old in sorted({obj[key] for obj, key in views}):
        view = dict(doc['bufferViews'][old]); start = view.get('byteOffset', 0)
        mapping[old] = len(new_views)
        packed.extend(b'\0' * (-len(packed) % 4))
        view['byteOffset'] = len(packed)
        packed.extend(binary[start:start+view['byteLength']]); new_views.append(view)
    for obj, key in views: obj[key] = mapping[obj[key]]
    doc['bufferViews'] = new_views; doc['buffers'] = [{'byteLength': len(packed)}]
    encoded = json.dumps(doc, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4); packed.extend(b'\0' * (-len(packed) % 4))
    return (struct.pack('<III', 0x46546c67, 2, 28+len(encoded)+len(packed)) +
            struct.pack('<II', len(encoded), 0x4e4f534a) + encoded +
            struct.pack('<II', len(packed), 0x004e4942) + packed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ual1', type=Path); parser.add_argument('ual2', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'gateway/motion_director/animation_assets'
    root.mkdir(exist_ok=True); manifest = {}
    for stem, archive in zip(SELECTED, (args.ual1, args.ual2)):
        with zipfile.ZipFile(archive) as bundle:
            name = next(n for n in bundle.namelist() if n.endswith('/Unreal-Godot/'+stem+'.glb'))
            raw = bundle.read(name); output = subset(raw, SELECTED[stem])
            (root/(stem+'.glb')).write_bytes(output)
            manifest[stem] = {'clips': SELECTED[stem], 'source_zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                              'source_glb_sha256': hashlib.sha256(raw).hexdigest(), 'sha256': hashlib.sha256(output).hexdigest(),
                              'source': 'https://quaternius.itch.io/universal-animation-library'+('-2' if 'UAL2' in stem else ''),
                              'license': 'CC0-1.0', 'modification': 'Retain named animation clips and referenced binary data; source rig, mesh and keyframes unchanged.'}
            license_name = next(n for n in bundle.namelist() if n.endswith('/License.txt'))
            (root/'LICENSE.txt').write_bytes(bundle.read(license_name))
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':main()
