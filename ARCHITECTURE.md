# Architecture

NeonForge is a single-host media studio for DGX Spark. It deliberately uses the existing Next.js, FastAPI, Redis, Docker Compose, and model-service boundaries rather than a general scheduler.

```mermaid
flowchart TD
    Browser --> Frontend[Next.js creator UI]
    Frontend --> Gateway[FastAPI gateway]
    Gateway <--> Redis[(Redis status cache)]
    Gateway <--> SQLite[(SQLite durable Motion jobs/plans/history)]
    Gateway --> Host[Private Opus/CPU Blender adapter]
    Gateway --> H3[Existing external H3]
    Supervisor --> Lease[Shared Forge3D GPU lease]
    Gateway --> Supervisor[Internal resource manager]
    Supervisor --> Compose[Targeted Docker Compose start/stop]

    Gateway --> Voice[Voice services]
    Gateway --> Comfy[ComfyUI]
    Gateway --> Lip[LatentSync]
    Gateway --> Whisper[Faster-Whisper]

    Comfy --> Hunyuan[HunyuanVideo 1.5 graph]
    Comfy --> Character[Wan2.2 Character graph]
    Voice & Comfy & Lip & Whisper --> Storage[(Shared models/cache/assets/outputs)]
    Voice & Comfy & Lip & Whisper --> UMA[GB10 shared UMA]
```

## Workload sequence

```mermaid
sequenceDiagram
    participant UI as Creator UI
    participant GW as Gateway
    participant RM as Supervisor
    participant S as Model service

    UI->>GW: Submit generation
    GW->>RM: Prepare allowlisted workload
    RM->>RM: Read /proc/meminfo
    RM->>RM: Stop idle allowlisted conflicts if needed
    RM->>S: Compose start target
    RM->>S: Wait for readiness
    RM-->>GW: Claim + memory evidence
    GW->>S: Execute job
    S-->>GW: Output
    GW->>RM: Release claim
    RM->>RM: Record duration and lowest observed MemAvailable
    RM->>S: Stop after idle timeout
```

The gateway does not have the Docker socket. The supervisor is internal-only and accepts service identifiers from an immutable policy. Its protected set is frontend, gateway, Redis, supervisor, and Whisper. Candidate selection intersects running containers with the allowlist, excludes the requested and claimed services, then tries the largest measured consumers first. No arbitrary kill or host-process path exists.

## Product-to-backend map

| Primary surface | Backend | Lifecycle |
| --- | --- | --- |
| Voiceover | F5/Fish/Miso/Breeze/VoxCPM2 | Voice policies, normally 15-minute warm window |
| Video | HunyuanVideo 1.5 managed ComfyUI template | 40–60 GiB floor by frame count/resolution, 5-minute warm window |
| Character | Wan2.2 Animate managed ComfyUI template | 64 GiB floor for 17-frame preview, 5-minute warm window |
| Avatar | EchoMimicV3-Flash target | Disabled until ARM64 real render passes |
| Lip Sync | LatentSync 1.6 service | 32 GiB cold-start floor, 5-minute warm window |
| System Info | Gateway and supervisor inspection | Always-on control plane |
| Motion Director (privately active) | Existing Opus Pro → CPU Blender scene/preview → H3 reference-video; manual Grok export/import | 90 GiB draft / 105 GiB native before each bounded clip, shared exclusive lease, verified-idle cache cleanup; H3 never started/stopped |

ComfyUI is intentionally shared by the two managed graph workflows. The gateway supplies the managed workflow identity and bounded frame/resolution inputs. The supervisor applies small measured admission rules, checks warm containers too, and rejects competing active heavy claims.

## Storage

| Host path | Container path | Purpose |
| --- | --- | --- |
| `/srv/ai/models` | `/models` | Explicit checkpoints |
| `/srv/ai/cache/hf` | `/cache/hf` | Shared Hugging Face cache |
| `/srv/ai/outputs` | `/outputs` | Generated media, ComfyUI I/O, history database |
| `/srv/ai/assets` | `/app/data/assets` | Reusable uploads and voice profiles |
| `/srv/ai/logs` | `/logs` | Service logs |

## Failure behavior

- If allowlisted reclamation cannot reach the minimum, the request fails with the required/current memory, what was stopped, and active claims.
- If a model service never becomes ready, the claim is cleaned up and the failure is recorded.
- ComfyUI uses `restart: "no"`; OOM is not hidden by a silent container restart.
- Completed Comfy prompts receive a bounded history-publication grace to avoid queue/history races.
- Service wrappers expose liveness separately from model/readiness state.

This is a small single-machine control plane, not Kubernetes, a DAG engine, a plugin system, or a universal GPU scheduler.

Motion Director adds a persistent shot/job table to the existing SQLite metadata
and reuses uploads, the heavy semaphore, supervisor claims, output storage and
history. `H3_COMFYUI_URL` is blank by default. Once a private route is verified,
the external H3 reservation joins the same claim exclusion rules and guards
other model admissions by inspecting its queue. H3 never becomes a Compose
lifecycle target. Automatic Hermes/Grok generation remains disabled because the existing plugin calls the xAI API. Manual Blender export and video import/comparison are available. Classic annotations guide the prompt; the new validated 3D scene generates actual reference footage. See [Motion Director's
activation notes](docs/motion-director-v0.1.md).

The continuation adds an opt-in authenticated Unix-socket host adapter. Separate
gateway/supervisor HMAC capabilities expose only planning/resource operations;
provider credentials stay on the host. The installed socket proxy reaches H3
through bridge-only TCP 18189. Managed GPU claims coordinate with the existing
Forge3D lease, preserving/restoring approved idle workloads. Optional Claude
Code Opus checks subscription quota and refuses paid overflow; validated plans
are persisted and never regenerated during inference. Real reference-to-video
acceptance passed and the tested images are privately active. Claude extra usage
is now verified off and one real subscription Opus plan passed. See
[continuation evidence](docs/motion-director-continuation.md).

5–30-second scenes persist individual segment intents/prompts/results. Each
bounded clip is admitted at 90 GiB; its predecessor supplies a short reference
video and a boundary-frame guide through inspected existing H3 nodes. CPU FFmpeg
trims and joins clips. The director runs once for the scene, outside rendering.
Cancellation/unknown acknowledgements retain reservations. Idle H3 cache is
reclaimed under the existing exclusive lease and paused workers restored.
The authenticated socket directory uses `RuntimeDirectoryPreserve=restart` to
keep Docker mounts valid; missing supervisor host safety disables admission.


The 3D continuation persists immutable scene plans and revisions in the existing
plan table. A scoped host adapter compiles validated geometry/rig/paths/camera
data into Blender Python, runs CPU previews and exposes only rendered artifacts
through a read-only output mount. H3 receives a bounded Blender window per clip
through its real reference-video path, retaining prior AI boundary-image guides.
Draft uses 22 frames; native uses 124 real-time motion frames at 384×672 while
generating 768×1344, with the original image as a first-frame appearance guide.
Opus returns separate validated spatial and H3 appearance contexts. Native
admission and per-clip rechecks require 105 GiB; draft keeps the 90 GiB floor.
No arbitrary model-generated code is executed, and strict ControlNet/trajectory
control is not claimed. See [3D execution and limits](docs/motion-3d/WORKFLOW.md)
and the [quality comparison](docs/motion-quality/RESULTS.md).

Motion submission and segment intent commit together in SQLite; Redis failure
cannot prevent acknowledgement or dispatch. Restart recovery tracks accepted H3
work without submitting another clip; legacy or ambiguous jobs receive explicit
recovery states. The gateway remains a single worker. See
[stabilization](docs/motion-stabilization.md) and the
[non-destructive modernization proposal](docs/modernization-proposal.md).
