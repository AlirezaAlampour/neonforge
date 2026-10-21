"""Renderer plugins. Only the inspected local H3 graph can execute in v0.1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import httpx

from .planner import ShotSpec
from shared.motion_quality import PROFILES

WORKFLOW_PATH = Path(__file__).with_name("h3.workflow.json")
IMAGE_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
IMAGE_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"


class SubmissionUncertain(RuntimeError):
    """A prompt may have been accepted; its reservation must not be released."""


class SubmissionRejected(RuntimeError):
    """Failure before /prompt was sent; no inference could have been accepted."""


class Renderer(Protocol):
    async def available(self) -> dict: ...
    async def submit(self, shot: ShotSpec, assets: list, job_id: str) -> str: ...
    async def history(self, prompt_id: str) -> dict | None: ...
    async def retrieve(self, entry: dict, job_id: str) -> str: ...
    async def queue(self) -> dict: ...


def build_h3_graph(shot: ShotSpec, image_names: list[str], job_id: str, context: dict | None = None, scene_video: str | None = None) -> dict:
    graph = json.loads(WORKFLOW_PATH.read_text())
    graph["138"]["inputs"]["value"] = shot.renderer_prompt
    graph["129"]["inputs"]["noise_seed"] = shot.request.seed
    graph["92"]["inputs"]["filename_prefix"] = f"motion_director/{job_id}"
    node = graph["136"]["inputs"]
    node.update(width=shot.width, height=shot.height, length=shot.frames)
    profile = PROFILES[shot.request.quality]
    if profile['crf'] is not None:
        graph['92']['inputs'].update({'format.codec.encoding': 're-encode', 'format.codec.encoding.crf': profile['crf']})
    for index, name in enumerate(image_names):
        load, scaled = f"ref_{index}", f"scale_{index}"
        graph[load] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph[scaled] = {"class_type": "ResizeImagesByLongerEdge", "inputs": {"images": [load, 0], "longer_edge": max(shot.width,shot.height)}}
        node[f"ref_images.ref_image_{index}"] = [scaled, 0]
    if shot.request.workflow == "image":
        # The installed first/last-frame blueprint uses fl2va, not ref2va.
        # Never silently run a first-frame graph with the reference checkpoint.
        graph["127"]["inputs"]["unet_name"] = IMAGE_MODEL
        graph["145"]["inputs"]["lora_name"] = IMAGE_LORA
        graph["124"]["inputs"]["steps"] = 8
        graph["136"]["class_type"] = "MiniMaxH3ImageToVideo"
        graph["136"]["inputs"] = {key: value for key, value in node.items()
                                  if key in {"clip", "vae", "prompt", "width", "height", "length"}}
        graph["136"]["inputs"]["first_frame"] = ["scale_0", 0]
    if context:
        if shot.request.workflow != "reference":
            raise RuntimeError("Continuation requires the installed reference checkpoint")
        graph["context_video"] = {"class_type": "LoadVideo", "inputs": {"file": context["video"]}}
        graph["context_frames"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["context_video", 0]}}
        node["ref_videos.ref_video_0"] = ["context_frames", 0]
        graph["boundary_image"] = {"class_type": "LoadImage", "inputs": {"image": context["image"]}}
        graph["boundary_guide"] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": ["136", 0], "latent": ["136", 1], "vae": ["119", 0], "image": ["boundary_image", 0], "frame_idx": 0}}
        graph["126"]["inputs"]["conditioning"] = ["boundary_guide", 0]
    if scene_video:
        if shot.request.workflow != "reference":
            raise ValueError("Blender conditioning requires the reference checkpoint")
        graph["blender_video"] = {"class_type": "LoadVideo", "inputs": {"file": scene_video}}
        graph["blender_frames"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["blender_video", 0]}}
        # Use one bounded video. Continuations keep the exact previous boundary
        # guide, with this clip's Blender motion window.
        node["ref_videos.ref_video_0"] = ["blender_frames", 0]
        graph.pop("context_video", None); graph.pop("context_frames", None)
        # Matched real fight renders showed full proxy video degrading alien
        # identity/character count. Draft retains the verified appearance recipe;
        # native keeps its existing full-clip contract. Preview remains complete.
        full_motion = shot.request.quality == 'native'
        graph["138"]["inputs"]["value"] += ("\n<Video 1> supplies motion timing, layout and camera tracking ONLY. Preserve each subject's identity, clothing, screen side, environment and lighting from the original pictures; do not transfer the motion reference's materials or simplified subject shape. "
            + ("It shows the clip's full motion; hold the same subject framing and camera speed throughout." if full_motion else "It shows only the opening 22 frames; continue the specified action for the full clip."))
        if shot.request.quality == 'native' and not context:
            graph['appearance_guide'] = {'class_type':'MiniMaxH3AddGuide','inputs':{
                'positive':['136',0], 'latent':['136',1], 'vae':['119',0], 'image':['ref_0',0], 'frame_idx':0}}
            graph['126']['inputs']['conditioning'] = ['appearance_guide',0]
    return graph


class H3Renderer:
    def __init__(self, runtime):
        self.runtime = runtime
        self.url = runtime.H3_COMFYUI_URL.rstrip("/")

    @property
    def client(self):
        return self.runtime.http_client

    async def available(self) -> dict:
        if not self.url:
            return {"available": False, "reason": "Configure a verified private route to the existing H3 instance."}
        try:
            response = await self.client.get(f"{self.url}/object_info", timeout=15)
            response.raise_for_status()
            schemas = response.json()
            graph = json.loads(WORKFLOW_PATH.read_text())
            required = {n["class_type"] for n in graph.values()} | {"LoadImage", "ResizeImagesByLongerEdge"}
            missing = sorted(required.difference(schemas))
            for node in graph.values():
                model_input = {"UNETLoader": "unet_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name", "LoraLoaderModelOnly": "lora_name"}.get(node["class_type"])
                if model_input and node["class_type"] in schemas:
                    catalog = schemas[node["class_type"]]["input"]["required"][model_input][0]
                    if node["inputs"][model_input] not in catalog:
                        missing.append(node["inputs"][model_input])
            if missing:
                return {"available": False, "reason": "Missing local H3 nodes or weights: " + ", ".join(missing)}
            image_available = ("MiniMaxH3ImageToVideo" in schemas
                               and IMAGE_MODEL in schemas["UNETLoader"]["input"]["required"]["unet_name"][0]
                               and IMAGE_LORA in schemas["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0])
            queue = await self.queue()
            return {"available": True, "busy": bool(queue.get("queue_running") or queue.get("queue_pending")), "control_video": False,
                    "reference_video": all(n in schemas for n in ("LoadVideo", "GetVideoComponents")),
                    "continuation_available": all(n in schemas for n in ("MiniMaxH3AddGuide", "LoadVideo", "GetVideoComponents")),
                    "control_nodes_present": "MiniMaxH3FunControlNetApply" in schemas,
                    "workflows": {"reference": {"available": True}, "image": {"available": image_available,
                        "reason": "First-frame mode requires the installed blueprint's fl2va checkpoint and 8-step LoRA. Use reference-to-video with one image; no weights are downloaded." if not image_available else ""}}}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "reason": "The existing H3 instance is unreachable or incompatible. Check its private route; do not start another instance."}

    async def queue(self) -> dict:
        response = await self.client.get(f"{self.url}/queue", timeout=10)
        response.raise_for_status()
        return response.json()

    async def interrupt(self, prompt_id: str):
        response = await self.client.post(f'{self.url}/interrupt', json={'prompt_id': prompt_id}, timeout=15)
        response.raise_for_status()

    async def find_submission(self, client_id: str) -> str | None:
        """Read-only acknowledgement recovery; client IDs never authorize a POST."""
        queue = await self.queue()
        entries = queue.get('queue_running', []) + queue.get('queue_pending', [])
        response = await self.client.get(f'{self.url}/history', params={'max_items': 1000}, timeout=30)
        response.raise_for_status()
        entries += [entry.get('prompt', []) for entry in response.json().values()]
        matches = {entry[1] for entry in entries if len(entry) > 3 and isinstance(entry[3], dict)
                   and entry[3].get('client_id') == client_id}
        if len(matches) > 1:
            raise SubmissionUncertain('Multiple H3 prompts match the submission ID. Inspect before proceeding.')
        return next(iter(matches), None)

    async def submit(self, shot: ShotSpec, assets: list, job_id: str, context_paths: tuple[Path, Path] | None = None, scene_path: Path | None = None) -> str:
        try:
            return await self._submit(shot, assets, job_id, context_paths, scene_path)
        except (SubmissionUncertain, httpx.HTTPStatusError):
            raise
        except Exception as exc:
            raise SubmissionRejected(str(exc)) from exc

    async def _submit(self, shot: ShotSpec, assets: list, job_id: str, context_paths=None, scene_path=None) -> str:
        queue = await self.queue()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("H3 already has a workload. Wait for it to finish.")
        names = []
        for asset in assets:
            source = self.runtime.resolve_asset_path(asset.relative_path, self.runtime.COMFYUI_UPLOADS_DIR)
            with source.open("rb") as image:
                response = await self.client.post(f"{self.url}/upload/image",
                                                  files={"image": (asset.stored_filename, image, asset.content_type or "application/octet-stream")},
                                                  data={"type": "input", "overwrite": "false"}, timeout=60)
            response.raise_for_status()
            uploaded = response.json()
            names.append(str(Path(uploaded.get("subfolder", "")) / uploaded["name"]))
        # Only our local graph is used. No cloud nodes or caller-provided workflows.
        context = None
        if context_paths:
            context = {}
            for kind, path in zip(("image", "video"), context_paths):
                with path.open("rb") as source:
                    response = await self.client.post(f"{self.url}/upload/image", files={"image": (path.name, source, "image/png" if kind == "image" else "video/mp4")},
                                                      data={"type": "input", "overwrite": "false"}, timeout=60)
                response.raise_for_status()
                uploaded = response.json()
                context[kind] = str(Path(uploaded.get("subfolder", "")) / uploaded["name"])
        scene_video = None
        if scene_path:
            with scene_path.open("rb") as source:
                response = await self.client.post(f"{self.url}/upload/image", files={"image": (scene_path.name, source, "video/mp4")},
                                                  data={"type": "input", "overwrite": "false"}, timeout=60)
            response.raise_for_status()
            uploaded = response.json()
            scene_video = str(Path(uploaded.get("subfolder", "")) / uploaded["name"])
        graph = build_h3_graph(shot, names, job_id, context, scene_video)
        try:
            response = await self.client.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": job_id}, timeout=120)
        except httpx.TransportError as exc:
            raise SubmissionUncertain("H3 submission acknowledgement was lost. Reservation retained; inspect the existing H3 queue before retrying.") from exc
        if response.status_code >= 500:
            raise SubmissionUncertain("H3 returned an ambiguous submission error. Reservation retained; inspect its queue before retrying.")
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise SubmissionUncertain('H3 returned an unreadable acknowledgement. Check the accepted shot before retrying.') from exc
        if not result.get('prompt_id'):
            raise SubmissionUncertain('H3 returned no prompt ID. Check the accepted shot before retrying.')
        return result["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        response = await self.client.get(f"{self.url}/history/{prompt_id}", timeout=15)
        response.raise_for_status()
        return response.json().get(prompt_id)

    async def retrieve(self, entry: dict, job_id: str) -> str:
        outputs = entry.get("outputs", {}).get("92", {})
        files = [item for key in ("images", "videos", "gifs", "files") for item in outputs.get(key, [])
                 if item.get("type") == "output" and item.get("filename", "").lower().endswith(".mp4")]
        if not files:
            raise RuntimeError("H3 finished without an MP4 from the SaveVideo output node")
        artifact = files[0]
        filename, folder = artifact["filename"], artifact.get("subfolder", "")
        if Path(filename).name != filename or Path(folder).is_absolute() or ".." in Path(folder).parts:
            raise RuntimeError("Invalid H3 output path")
        relative = f"motion_director/{job_id}.mp4"
        target = self.runtime.resolve_output_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        size = 0
        async with self.client.stream("GET", f"{self.url}/view", params={"filename": filename, "subfolder": folder, "type": "output"}, timeout=120) as response:
            response.raise_for_status()
            with part.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1024**3:
                        raise RuntimeError("H3 output exceeds the 1 GiB retrieval limit")
                    output.write(chunk)
        if not size:
            raise RuntimeError("H3 returned an empty video")
        part.replace(target)
        return relative


class GrokRenderer:
    def __init__(self, runtime=None):
        self.runtime = runtime

    async def available(self) -> dict:
        from .host_client import host_request
        if self.runtime and getattr(self.runtime, "HOST_CONTROL_SOCKET", ""):
            try:
                return await host_request(self.runtime, "grok/status")
            except (RuntimeError, httpx.HTTPError, OSError):
                pass
        return {"available": False, "manual_import": True, "reason": "Existing Hermes Grok OAuth is unavailable. Import a video made in Grok for comparison; no paid API is called."}

    async def submit(self, *args, **kwargs):
        raise RuntimeError("Grok video rendering is unavailable until OAuth access and a no-cost renderer are verified")

    async def history(self, prompt_id: str):
        raise RuntimeError("Grok video access is unverified")

    async def retrieve(self, entry: dict, job_id: str):
        raise RuntimeError("Grok video access is unverified")

    async def queue(self):
        raise RuntimeError("Grok video access is unverified")


RENDERERS = {"h3": H3Renderer, "grok": GrokRenderer}


def get_renderer(name: str, runtime) -> Renderer:
    return RENDERERS[name](runtime)
