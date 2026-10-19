# Execution audit — 2026-10-19, before implementation

Branch: `motion-director-v0.1`, existing NeonForge repository. No merge.

| Stage | Observed execution / status |
| --- | --- |
| Reference upload | Implemented, connected: image bytes preserved in existing asset store. Browser upload/drawing tests exist. |
| Claude Opus | Implemented and connected through private authenticated host socket + existing Claude Code Pro login. Returns validated `DirectorDecisions` (camera text, composition, identity constraints, timed beats), then compiles an H3 text prompt. Subscription status can fail transiently. No 3D spec or Blender Python is produced. |
| Scene representation | Textual `ShotSpec`, normalized 2D annotations. No 3D transforms, meshes, depth or scene graph. |
| Blender | Existing ARM64 5.2 binary outside this project; needs its bundled `libExt` on LD_LIBRARY_PATH. Motion Director neither imports nor invokes it. Existing blockout-director has a reusable primitive armature, but is not connected to this interface. |
| Character/object animation | Missing here. H3 imagines motion from the prompt; no rigs, reusable clips or 3D paths. |
| Camera animation | Camera text/drawings only; no independent 3D camera or keyframes. |
| Reference preview | Missing. UI only previews the final AI video, with no correction loop or Blender comparison. |
| H3 conditioning | Connected and previously real-tested, including 10 seconds using two bounded sequential clips. First clip gets original images + text. Later clips also get previous AI video's final 22 frames and a boundary-image guide. No Blender render reaches it. |
| Render influence | Absent: no scene is rendered. Drawings affect text only. |
| Output | Real MP4s in live gallery (5, 10, 15, 30 seconds); inspected latest running-character video. Existence does not prove directed 3D motion. |
| Grok | Existing Hermes plugin inspected read-only: subscription OAuth and API-key fallback both use xAI `/videos/generations`. No saved OAuth found. User forbids Grok API: automatic generation must stay disabled, including via this plugin. Existing manual MP4 import/comparison works; Blender export bundle is missing. |

Installed H3 source: `comfy_extras/nodes_minimax_h3.py`, `MiniMaxH3ReferenceToVideo.execute` accepts reference frame batches, presents timestamped 2fps samples to Qwen and encodes them into VAE reference latents. It truncates to output length and the `17k+5` grid. `MiniMaxH3AddGuide` can anchor a frame. This supports reference-video conditioning; it does **not** establish strict pose/depth/trajectory control. ControlNet node presence alone is not proof of usable matching weights. Keep `control_video=false`.

Browser audit used running private `/motion-director`, desktop 1440×1000 and phone 390×844. Screenshots saved before edits. Problems: 220px sidebar persists at mobile width (incorrect important-class syntax), reference image hidden with drawing controls, primary action bypasses any preview, planner details are technical, no natural-language revision or Blender export, stale plans can survive changed fields in draft storage. Existing real video decodes; it is prompt-guided.

Baseline: `uv lock --check` passes. `uv run --locked --no-sync python -m pytest` targeted Motion Director suites: 48 pass. Direct `pytest` entry point needs repo on sys.path, so use `python -m pytest`.

Resources before new work: ~78 GiB MemAvailable, below unchanged H3 90 GiB admission. Host 2000MHz cap verified. No services stopped or memory thresholds changed. Blender preview will use bounded CPU rendering, independently of H3's exclusive GPU reservation.
