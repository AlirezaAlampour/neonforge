# Real verification — 2026-10-19

The pre-change audit was completed before implementation. This work stays on
`motion-director-v0.1`, unmerged. Inference below is real unless marked fixture.

## Opus and Blender

- Existing Claude Code Pro login and disabled paid extra usage verified. One
  direct vision/planning acceptance and a separate real live UI plan/revision
  succeeded. No Claude API key, new profile or API-billing fallback was used.
- Direct running-character plan: nine entities; 10 seconds / 240 frames.
  Evaluated Blender telemetry shows 29.875 m travel (last sample at 9.958s),
  horizontal follow distance 3.99999988–4.00000006 m, maximum normalized camera
  aim error below 8e-8, 129 distinct sampled left-shin poses, and all eight
  environment entity transforms stationary. Gait phase follows distance / 2.4m.
- Live phone workflow uploaded the owner's platform reference, built an actual
  Opus scene and Blender preview, then applied “camera six metres behind.”
  Scene `dffd16fc-b43c-4a47-b87f-0b41c4f4ae3c` was preserved; revision
  `2794503c-1467-4f4e-be8e-41f65bb36bb9` changed camera distance to 6m and
  preserved every entity/path exactly. The revised preview also decodes at 10s.
- Existing Forge3D asset `1e903b13530a3c94` was imported read-only and rendered
  with an orbit camera (48 frames). The first check caught the bundle's missing
  OpenBLAS; a subprocess-only library path supplied by the uv-locked optional
  group resolved it. No model or Forge3D container was launched.

## H3 acceptance

Real job: `1fe0024b-3958-4384-87ac-5e8ee42bd2f6`.
Result: **10.031006 seconds**, 384×672, **240 H.264 frames**, AAC audio, two clips.
The additional 31ms is container/audio padding. Monitoring after acknowledgement
through completion lasted approximately 144 seconds.

Both actual H3 histories contain `blender_video` → `blender_frames` →
`ref_videos.ref_video_0`; their original image inputs are also present. The
second graph retains the previous AI boundary-frame guide. Each submitted motion
window is 22 frames at the output size. This proves the rendered footage entered
reference conditioning; it does not prove precise frame-by-frame motion control.

| Resource evidence | Observation |
| --- | --- |
| UMA before accepted run | ~110.9 GiB |
| Lowest sampled MemAvailable during inference | 68.96 GiB |
| Highest sampled GPU utilization | 96% |
| Highest sampled temperature | 56°C |
| GPU clock | ≤1989MHz; existing 2000MHz cap active |
| Per-clip admission/recheck | 90 GiB retained; idle cache recovered before clip two |
| Cleanup | Exclusive reservation released; approved idle worker restored |
| Qwen restoration | `qwen3.6:35b-a3b`, original 262144 context, pinned residency restored |

The owner explicitly authorized temporarily unloading/restoring Qwen. H3's idle
cache was released through the existing verified-idle coordinator before Qwen
was restored. No unrelated process was killed. Trellis and Hunyuan were already
stopped and remained stopped. Existing ComfyUI, H3 and other unrelated container
identities were preserved. An earlier attempt failed **before submission** when
pre-existing health checks treated stopped Forge3D/Hunyuan as unknown. The fix
requires authoritative stopped-container inspection; unknown/running states still
block. No uncertain H3 prompt was retried.

## Observed motion differences

Contact sheets and both complete decodable clips were inspected. The Blender
runner travels continuously along +Y with constant size in the fixed-distance
rear-follow camera, synchronized stylized gait, and stationary bystanders/props.
The AI video recovers the reference's detailed clothing, neon train platform and
lighting and shows the runner moving away with the camera behind. However:

- It opens from the original image pose/framing, rather than the Blender first
  frame, then changes to the rear-follow view.
- The runner recedes and changes framing: exact six-metre distance and metric
  speed are not enforced by reference conditioning.
- Bystanders move and the cat/foreground details change or disappear, despite
  being fixed in Blender. Exact object stationarity does **not** pass in H3.
- Appearance/pose and scene geography drift; accurate IK, contact and realistic
  human gait were not verified. There is no claim of final-video 3D ground truth.

This is a real 3D-directed reference workflow with visible AI deviation, not a
claim that H3 obeys all Blender transforms.

## UI and regressions

- Live Chromium: 390×844 touch viewport and 1440×1000 desktop, real reference
  upload, Opus planning, actual Blender preview playback, natural correction,
  export ZIP download, persistence after reload, final H3 submit, decoded final
  download and paired Blender/final playback. No page errors or horizontal
  overflow. Sidebar measured 56px on phone and 220px on desktop.
- Screenshots: before/after desktop and mobile plus paired comparison. Local
  acceptance artifacts and resource/transform/history telemetry are retained in
  the ignored `.motion-runtime` directory, not in source control.
- Existing browser regression additionally covers drawings, touch, undo/clear,
  classic planning, two **mocked H3** takes, downloads and comparison. Those mock
  outputs are not claimed as inference evidence.
- Full backend suite: 196 tests pass; TypeScript and production build pass.
  Final active image tag: `motion-3d-v01-final`.
- Manual Grok import is regression-tested using a CPU fixture. No actual Grok
  generation was attempted: its existing plugin calls the forbidden xAI API.
  Export is usable; a browser-generated Grok clip is still needed for a real
  Grok comparison. Complex rigged interactions and realistic mocap remain absent.

## Deployment

Private activation uses the original `dgx-ai-stack` Compose project, the existing
base and motion overlay, and the generated image override under
`.motion-runtime/images/motion-3d-v01-final/images.json`. Only frontend,
gateway and supervisor images are promoted with `--no-build --no-deps`; the host
adapter uses the existing user service. Original rollback tags
`neonforge-motion-base-{frontend,gateway,supervisor}:motion-3d-v01-ready` retain
the pre-3D deployment. No merge or remote push is part of this work.
