# Architecture

NeonForge is a single-host media studio for DGX Spark. It deliberately uses the existing Next.js, FastAPI, Redis, Docker Compose, and model-service boundaries rather than a general scheduler.

```mermaid
flowchart TD
    Browser --> Frontend[Next.js creator UI]
    Frontend --> Gateway[FastAPI gateway]
    Gateway <--> Redis[(Redis job/activity state)]
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
| Video Generation | HunyuanVideo 1.5 managed ComfyUI template | 40 GiB cold-start floor, 5-minute warm window |
| Character | Wan2.2 Animate managed ComfyUI template | 48 GiB cold-start floor, 5-minute warm window |
| Avatar | EchoMimicV3-Flash target | Disabled until ARM64 real render passes |
| Lip Sync | LatentSync 1.6 service | 32 GiB cold-start floor, 5-minute warm window |
| Utilities & Status | Gateway and supervisor inspection | Always-on control plane |

ComfyUI is intentionally shared by the two managed graph workflows. The supervisor applies an explicit workflow-id-to-memory-minimum map, while the gateway supplies only the allowlisted workflow identity and display label.

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
