"""Renderer plugins. Only the inspected local H3 graph can execute in v0.1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import httpx

from .planner import ShotSpec

WORKFLOW_PATH = Path(__file__).with_name("h3.workflow.json")
IMAGE_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
IMAGE_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"


class SubmissionUncertain(RuntimeError):
    """A prompt may have been accepted; its reservation must not be released."""


class Renderer(Protocol):
    async def available(self) -> dict: ...
    async def submit(self, shot: ShotSpec, assets: list, job_id: str) -> str: ...
    async def history(self, prompt_id: str) -> dict | None: ...
    async def retrieve(self, entry: dict, job_id: str) -> str: ...
    async def queue(self) -> dict: ...


def build_h3_graph(shot: ShotSpec, image_names: list[str], job_id: str) -> dict:
    graph = json.loads(WORKFLOW_PATH.read_text())
    graph["138"]["inputs"]["value"] = shot.renderer_prompt
    graph["129"]["inputs"]["noise_seed"] = shot.request.seed
    graph["92"]["inputs"]["filename_prefix"] = f"motion_director/{job_id}"
    node = graph["136"]["inputs"]
    node.update(width=shot.width, height=shot.height, length=shot.frames)
    for index, name in enumerate(image_names):
        load, scaled = f"ref_{index}", f"scale_{index}"
        graph[load] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph[scaled] = {"class_type": "ResizeImagesByLongerEdge", "inputs": {"images": [load, 0], "longer_edge": 672}}
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
                    "control_nodes_present": "MiniMaxH3FunControlNetApply" in schemas,
                    "workflows": {"reference": {"available": True}, "image": {"available": image_available,
                        "reason": "First-frame mode requires the installed blueprint's fl2va checkpoint and 8-step LoRA. Use reference-to-video with one image; no weights are downloaded." if not image_available else ""}}}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "reason": "The existing H3 instance is unreachable or incompatible. Check its private route; do not start another instance."}

    async def queue(self) -> dict:
        response = await self.client.get(f"{self.url}/queue", timeout=10)
        response.raise_for_status()
        return response.json()

    async def submit(self, shot: ShotSpec, assets: list, job_id: str) -> str:
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
        graph = build_h3_graph(shot, names, job_id)
        try:
            response = await self.client.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": job_id}, timeout=120)
        except httpx.TransportError as exc:
            raise SubmissionUncertain("H3 submission acknowledgement was lost. Reservation retained; inspect the existing H3 queue before retrying.") from exc
        if response.status_code >= 500:
            raise SubmissionUncertain("H3 returned an ambiguous submission error. Reservation retained; inspect its queue before retrying.")
        response.raise_for_status()
        result = response.json()
        if result.get("node_errors") or not result.get("prompt_id"):
            raise RuntimeError("H3 rejected the workflow: " + str(result.get("node_errors", "missing prompt ID"))[:1200])
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
        return {"available": False, "reason": "Grok OAuth video access is unverified. No authentication or paid calls are performed."}

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
