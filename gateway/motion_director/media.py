"""Bounded CPU media operations using installed FFmpeg; never launch inference."""
import asyncio
import json
from pathlib import Path


async def command(*args, timeout=120):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.kill(); await process.wait(); raise
    if process.returncode:
        raise RuntimeError("Media processing failed: " + error.decode(errors="replace")[-600:])
    return output


async def probe(path):
    return json.loads(await command("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path), timeout=15))


async def continuation(previous: Path, folder: Path, index: int, width: int, height: int):
    folder.mkdir(parents=True, exist_ok=True)
    image, video = folder / f"boundary-{index}.png", folder / f"context-{index}.mp4"
    await command("ffmpeg", "-v", "error", "-y", "-sseof", "-0.041667", "-i", str(previous), "-vf", f"scale={width}:{height}", "-frames:v", "1", "-threads", "1", str(image))
    await command("ffmpeg", "-v", "error", "-y", "-sseof", "-1", "-i", str(previous), "-an", "-vf", f"fps=24,scale={width}:{height}", "-frames:v", "22", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "2", str(video))
    return image, video


async def scene_window(source: Path, folder: Path, index: int, width: int, height: int, quality='draft'):
    """Real-time motion: draft opening window or native full-clip coverage."""
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / f"blender-motion-{index}.mp4"
    native = quality == 'native'
    # Motion geometry needs temporal coverage more than fine texture. Keep its
    # canvas bounded while original-image conditioning uses the native canvas.
    mw,mh = ((384,672) if height>width else (672,384)) if native else (width,height)
    filters = f'fps=24,scale={mw}:{mh}' + (',tpad=stop_mode=clone:stop_duration=0.17' if native else '')
    await command("ffmpeg", "-v", "error", "-y", "-ss", str(index*5), "-i", str(source), "-an", "-vf",
                  filters, "-frames:v", "124" if native else "22", "-c:v", "libx264", "-crf", "16" if native else "23",
                  "-pix_fmt", "yuv420p", "-threads", "2", str(video))
    return video


async def trim(source: Path, target: Path, duration: int, skip_boundary: bool, quality='draft'):
    target.parent.mkdir(parents=True, exist_ok=True)
    await command("ffmpeg", "-v", "error", "-y", "-i", str(source), "-ss", str(1/24 if skip_boundary else 0),
                  "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0?", "-vf", "fps=24", "-c:v", "libx264", "-crf", "16" if quality=='native' else "18",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-threads", "2", "-movflags", "+faststart", str(target))


async def assemble(paths: list[Path], target: Path, duration: int):
    if not paths or len(paths) > 6 or any(not p.is_file() for p in paths):
        raise RuntimeError("Incomplete sequential video")
    # Paths are generated UUID/index paths, never user-supplied concat syntax.
    manifest = target.with_suffix(".concat.txt")
    if any("'" in str(p.resolve()) or "\n" in str(p) for p in paths):
        raise RuntimeError("Invalid generated media path")
    manifest.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    await command("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-t", str(duration),
                  "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-af", "aresample=async=1:first_pts=0", "-c:a", "aac",
                  "-threads", "2", "-movflags", "+faststart", str(target))
    data = await probe(target)
    if abs(float(data["format"]["duration"]) - duration) > .12:
        raise RuntimeError("Assembled duration does not match the requested video")
    return data


async def normalize_import(source: Path, target: Path, width: int, height: int):
    await command("ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                  "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24",
                  "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-threads", "2", "-movflags", "+faststart", str(target))
