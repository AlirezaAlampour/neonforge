"""Small real-generation acceptance recorder. Uses the existing gateway and supervisor.

Run with `uv run --locked python scripts/accept_creative.py --help`.
Reports are measurements, never an assertion of visual quality or model licensing.
"""
import argparse
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def memory():
    values = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
              if line.startswith(('MemAvailable:', 'SwapFree:'))}
    return round(values['MemAvailable'] / 1048576, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', choices=['voice', 'video', 'character', 'lipsync'])
    parser.add_argument('--gateway', default='http://127.0.0.1:8080')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--reference-id')
    parser.add_argument('--driving-id')
    parser.add_argument('--video', type=Path)
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--wait-unload', action='store_true')
    args = parser.parse_args()
    before = memory()
    samples = [before]
    stop = threading.Event()
    def sample():
        while not stop.wait(1):
            samples.append(memory())
    threading.Thread(target=sample, daemon=True).start()
    report = {'workflow': args.workflow, 'date': datetime.now(timezone.utc).isoformat(), 'memory_before_gib': before}
    start = time.monotonic()
    client = httpx.Client(base_url=args.gateway, timeout=120)
    print(f'{args.workflow}: MemAvailable {before:.2f} GiB; supervisor admission required', flush=True)
    try:
        if args.workflow == 'voice':
            body = {'model_id': 'breeze_tts', 'script': 'Welcome to NeonForge. A quiet idea becomes a story, and every creative detail stays here with you.',
                    'breeze_mode': 'design', 'instruction': 'A warm, clear adult narrator with a calm, natural delivery.', 'seed': 42, 'output_format': 'wav'}
            response = client.post('/api/v1/voiceover/jobs', json=body)
            service, backend = 'breeze_tts', 'Breeze TTS 2'
        elif args.workflow == 'video':
            body = {'template_id': 'hunyuan-video-15-t2v', 'inputs': {}, 'params': {
                'prompt': 'A red fox walks slowly through a snowy pine forest at sunrise. Soft golden light catches its fur. A smooth cinematic tracking shot follows the fox, realistic natural motion.',
                'width': 832, 'height': 480, 'frames': 121, 'fps': 24, 'steps': 20, 'seed': 42, 'cfg': 1, 'shift': 5}}
            response = client.post('/api/v1/comfyui/jobs', json=body)
            service, backend = 'comfyui', 'HunyuanVideo 1.5 480p CFG-distilled FP8'
        elif args.workflow == 'character':
            if not args.reference_id or not args.driving_id:
                parser.error('Character requires --reference-id and --driving-id')
            body = {'template_id': 'wan-character-swap', 'inputs': {'reference_image': args.reference_id, 'driving_video': args.driving_id},
                    'params': {'max_frames': 17, 'steps': 4, 'seed': 42, 'frame_rate': 16}}
            response = client.post('/api/v1/comfyui/jobs', json=body)
            service, backend = 'comfyui', 'Wan2.2 Animate 14B FP8 Replace'
        else:
            if not args.video or not args.audio:
                parser.error('Lip Sync requires --video and --audio')
            body = {'video': args.video.name, 'audio': args.audio.name}
            with args.video.open('rb') as video, args.audio.open('rb') as audio:
                response = client.post('/api/v1/lipsync/sync', files={'video': (args.video.name, video), 'audio': (args.audio.name, audio)})
            service, backend = 'lipsync', 'LatentSync 1.6'
        response.raise_for_status()
        job_id = response.json()['job_id']
        report.update(job_id=job_id, backend=backend, configuration=body)
        print(f'Job {job_id}', flush=True)
        endpoint = f'/api/v1/voiceover/jobs/{job_id}' if args.workflow == 'voice' else f'/jobs/{job_id}'
        last = None
        deadline = time.monotonic() + 7200
        while time.monotonic() < deadline:
            response = client.get(endpoint)
            response.raise_for_status()
            job = response.json()
            state = job['status']
            if state != last:
                print(f'{state}: MemAvailable {memory():.2f} GiB', flush=True)
                last = state
            if state in {'completed', 'complete', 'done', 'failed', 'error'}:
                break
            time.sleep(2)
        else:
            raise TimeoutError('Generation did not finish within two hours')
        report.update(status=state, runtime_seconds=round(time.monotonic() - start, 2), lowest_mem_available_gib=min(samples), memory_on_completion_gib=memory())
        if state not in {'completed', 'complete', 'done'}:
            raise RuntimeError(str(job.get('error') or job.get('message') or job))
        output = job.get('result_path') or job.get('output_path')
        if not output and args.workflow == 'voice':
            outputs = list((Path('/srv/ai/outputs/voiceover') / job_id).glob('*.wav'))
            output = str(outputs[0]) if outputs else None
        if not output:
            raise RuntimeError('Completed job has no output')
        path = Path(output)
        if output.startswith('/outputs/'):
            path = Path('/srv/ai/outputs') / path.relative_to('/outputs')
        elif not path.is_absolute():
            path = Path('/srv/ai/outputs') / path
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,width,height,nb_frames,r_frame_rate,duration:format=duration', '-of', 'json', str(path)], check=True, capture_output=True, text=True)
        report['output'] = str(path.relative_to('/srv/ai/outputs'))
        report['media'] = json.loads(probe.stdout)
        # Decode the whole file to catch corrupt/incomplete output, not just a valid header.
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True, capture_output=True)
        print(f'Valid media: {report["media"]}', flush=True)
        if args.wait_unload:
            deadline = time.monotonic() + 1000
            while time.monotonic() < deadline:
                lifecycle = client.get('/workloads/status').json()
                if not lifecycle['managed_services'][service]['running']:
                    report['memory_after_unload_gib'] = memory()
                    break
                time.sleep(5)
            else:
                raise TimeoutError('Idle unload not observed')
        lifecycle = client.get('/workloads/status').json()
        report['lifecycle_events'] = [event for event in lifecycle['events'] if event.get('job_id') == job_id or event['service'] == service][-12:]
    except Exception as exc:
        report.update(error=str(exc), status='failed', runtime_seconds=round(time.monotonic() - start, 2), lowest_mem_available_gib=min(samples))
        raise
    finally:
        stop.set()
        client.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key not in {'configuration', 'lifecycle_events'}}), flush=True)


if __name__ == '__main__':
    main()
