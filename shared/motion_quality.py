"""Small, explicit native generation profiles shared by gateway and admission.

All resolutions are model canvases, never export upscales. The 90 GiB floor is
retained; the larger candidate keeps extra activation headroom on shared UMA.
"""
import asyncio
import time

PROFILES = {
    'draft': {'vertical': (384, 672), 'minimum_gib': 90.0, 'motion_frames': 22, 'crf': None},
    'native': {'vertical': (768, 1344), 'minimum_gib': 105.0, 'motion_frames': 124, 'crf': 16},
}


def dimensions(quality, orientation):
    size = PROFILES[quality]['vertical']
    return size if orientation == 'vertical' else size[::-1]


def minimum_for_size(width, height):
    for spec in PROFILES.values():
        if (width, height) in {spec['vertical'], spec['vertical'][::-1]}:
            return max(90.0, spec['minimum_gib'])
    raise ValueError('Unsupported native H3 canvas; use a validated quality profile')


async def wait_native_cooldown(read_gpu, timeout=30):
    """Cool under the existing exclusive lease; never change clocks or fans."""
    start = time.monotonic()
    while True:
        gpu = await read_gpu()
        if gpu.get('utilization_pct', 100) > 10 or gpu.get('clock_mhz', 2001) > 2000:
            raise RuntimeError('GPU activity or an unverified clock prevents native H3 admission')
        if gpu.get('temperature_c', 100) <= 50:
            return round(time.monotonic()-start, 2)
        if time.monotonic()-start >= timeout:
            raise RuntimeError('Native H3 needs the idle GPU to cool to 50°C; let it cool before retrying')
        await asyncio.sleep(2)
