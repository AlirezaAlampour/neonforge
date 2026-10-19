"""Export native frames and honest, common-grid A/B measurements; no GPU/models.

Use uv run --locked --no-sync after verifying uv.lock. Image metrics are
descriptive proxies, never face-identity or perceptual-quality scores.
"""
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TIMES = [.5, 2.5, 4.5, 5.5, 7.5, 9.5]


def command(*args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def probe(path):
    data = json.loads(command('ffprobe', '-v', 'error', '-show_streams',
                              '-show_format', '-of', 'json', str(path)))
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    return {k: video.get(k) for k in ('width', 'height', 'avg_frame_rate',
            'nb_frames', 'codec_name', 'pix_fmt', 'bit_rate')} | {
                'duration': float(data['format']['duration']),
                'bytes': int(data['format']['size'])}


def image_metrics(path):
    raw = command('ffmpeg', '-v', 'error', '-threads', '2', '-i', str(path),
                  '-vf', 'fps=4,scale=192:336:flags=area,format=gray',
                  '-f', 'rawvideo', '-threads', '2', '-')
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 336, 192).astype(np.float32) / 255
    center = frames[:, 1:-1, 1:-1]
    lap = (4 * center - frames[:, :-2, 1:-1] - frames[:, 2:, 1:-1]
           - frames[:, 1:-1, :-2] - frames[:, 1:-1, 2:])
    changes, registered = [], []
    for a, b in zip(frames[:-1], frames[1:]):
        changes.append(float(np.abs(a-b).mean()))
        # Global phase correlation compensates translation only. Articulation,
        # occlusion, camera perspective and real scene motion still contribute.
        cross = np.fft.fft2(a-a.mean()) * np.conj(np.fft.fft2(b-b.mean()))
        corr = np.fft.ifft2(cross / np.maximum(np.abs(cross), 1e-8)).real
        shift = np.array(np.unravel_index(corr.argmax(), corr.shape))
        shift = np.where(shift > np.array(a.shape)//2, shift-np.array(a.shape), shift)
        if np.max(np.abs(shift)) > 32:
            shift[:] = 0
        moved = np.roll(b, tuple(shift), axis=(0, 1))
        mask = np.ones(a.shape, bool)
        for axis, displacement in enumerate(shift):
            sl = [slice(None)] * 2
            sl[axis] = slice(0, int(displacement)) if displacement > 0 else slice(int(displacement), None)
            if displacement:
                mask[tuple(sl)] = False
        registered.append(float(np.abs(a-moved)[mask].mean()))
    return {'grid': '192x336 grayscale, 4fps, downsampling only',
            'laplacian_rms': round(float(np.sqrt(np.mean(lap**2))), 5),
            'frame_difference_mean': round(float(np.mean(changes)), 5),
            'translation_compensated_difference_mean': round(float(np.mean(registered)), 5),
            'caveat': 'Content/contrast/motion affect these proxies. They do not measure identity or prove temporal consistency.'}


def main():
    manifest = json.loads((ROOT/'.motion-runtime/quality/comparison-manifest.json').read_text())
    folder = Path(manifest['folder'])
    results = {'seed_per_segment': [42, 43], 'sample_times_seconds': TIMES, 'cases': {}}
    for name in 'ABCD':
        video = folder/f'{name}.mp4'
        if not video.exists():
            continue
        record = {'video': probe(video), 'proxies': image_metrics(video)}
        benchmark = folder/f'{name}-benchmark.json'
        if benchmark.exists():
            data = json.loads(benchmark.read_text())
            record['benchmark'] = {k: data[k] for k in (
                'runtime_seconds', 'before_gib', 'minimum_available_gib',
                'peak_temperature_c', 'max_clock_mhz', 'minimum_admission_gib')}
            record['benchmark']['peak_shared_uma_consumption_from_admission_gib'] = round(
                data['before_gib']-data['minimum_available_gib'], 3)
            execution_seconds = []
            for history_path in sorted(folder.glob(f'{name}-clip*-history.json')):
                events = json.loads(history_path.read_text())['status']['messages']
                started = next(e[1]['timestamp'] for e in events if e[0]=='execution_start')
                finished = next(e[1]['timestamp'] for e in events if e[0]=='execution_success')
                execution_seconds.append((finished-started)/1000)
            record['benchmark']['successful_h3_execution_seconds'] = round(sum(execution_seconds), 3)
            if data.get('interrupted_attempts'):
                record['benchmark']['interrupted_attempts'] = len(data['interrupted_attempts'])
                record['benchmark']['runtime_note'] = data['runtime_note']
        elif name == 'A' and (ROOT/'.motion-runtime/3d-h3-samples.json').exists():
            samples = json.loads((ROOT/'.motion-runtime/3d-h3-samples.json').read_text())
            record['benchmark'] = {
                'runtime_seconds_approximate': samples[-1]['elapsed'],
                'minimum_available_gib': min(s['available_gib'] for s in samples),
                'peak_temperature_c': max(float(s['gpu'].split(',')[1]) for s in samples),
                'max_clock_mhz': max(float(s['gpu'].split(',')[2]) for s in samples),
                'caveat': 'Historical baseline monitor starts after submission acknowledgment; not a controlled runtime comparison.'}
        frames = folder/'frames'/name
        frames.mkdir(parents=True, exist_ok=True)
        for index, timestamp in enumerate(TIMES):
            command('ffmpeg', '-v', 'error', '-y', '-threads', '2', '-ss', str(timestamp),
                    '-i', str(video), '-frames:v', '1', '-threads', '2', str(frames/f'{index}.png'))
        command('ffmpeg', '-v', 'error', '-y', '-threads', '2', '-framerate', '1', '-i',
                str(frames/'%d.png'), '-vf', 'scale=384:672:flags=lanczos,tile=3x2',
                '-frames:v', '1', '-threads', '2', str(folder/f'{name}-contact.png'))
        results['cases'][name] = record
    results['memory_note'] = 'DGX shared UMA from /proc/meminfo; no dedicated VRAM counter. Host changes/page cache can affect the consumption delta.'
    results['blender_previews'] = {name: probe(folder/f'{name}-preview.mp4') for name in ('A', 'C')}
    (folder/'measurements.json').write_text(json.dumps(results, indent=2))
    cards = ''.join(f'''<article><h2>{name}</h2><video controls preload="metadata" src="{name}.mp4"></video>
        <p>{item['video']['width']} × {item['video']['height']} · 24fps · {item['video']['duration']:.2f}s</p>
        <a href="{name}-contact.png"><img src="{name}-contact.png" alt="{name}: frames at 0.5, 2.5, 4.5, 5.5, 7.5 and 9.5 seconds"></a>
        <a href="{name}.mp4" download>Download native clip</a></article>''' for name,item in results['cases'].items())
    (folder/'comparison.html').write_text('''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
        <title>Motion Director: running reference comparison</title><style>
        body{background:#11131c;color:#eee;font:16px system-ui;margin:24px}a{color:#9acbff}
        main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}
        article{background:#202432;padding:16px;border-radius:12px}video{width:100%;max-height:70vh}img{width:100%}
        button{padding:12px;font:inherit}p{max-width:90ch}</style>
        <h1>Running character · same reference · seeds 42/43</h1>
        <p>A: stored baseline. B: resolved Opus H3 plan. C: improved Blender geometry and camera reference.
        D: native 768×1344, full motion window, original appearance guide and CRF16.
        All clips are native generation sizes. Contact sheets are display reductions; individual frames retain native pixels.</p>
        <p>D's second segment was interrupted at the 70°C benchmark guard, then explicitly resumed from its saved predecessor after cooling.
        Successful H3 execution time and total active time including the interrupted attempt are recorded separately.</p>
        <button onclick="document.querySelectorAll('video').forEach(v=>{v.currentTime=0;v.muted=true;v.play()})">Play all from start</button>
        <p><a href="measurements.json">Measured metadata and proxy metrics</a> · <a href="visual-review.json">Manual screen-scale follow estimates</a> ·
        <a href="A-preview.mp4">Baseline Blender</a> · <a href="C-preview.mp4">Improved Blender</a></p>
        <main>'''+cards+'</main>')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
