# Motion Director: real 3D preview and reference-conditioned video

This continues `motion-director-v0.1` in the existing NeonForge checkout. No new
project, model service, Hermes profile or branch was created. The pre-change
execution audit is in [AUDIT.md](AUDIT.md).

## Connected execution

1. Original images stay in the existing asset store. Existing authenticated
   Claude Code Pro performs vision analysis and returns `SceneDecisions`: camera,
   composition, timed beats and a validated metric scene specification.
2. The scene describes approximate depth layers, primitive geometry or existing
   Forge3D GLBs, explicit transforms, object/character paths, reusable clips and
   a separate follow/orbit/track/static camera. Units are metres, Z up.
3. The private host adapter validates IDs, geometry, paths, time coverage, asset
   selection and hardware bounds. A reviewed Python compiler generates the
   downloadable `scene.py`. **Model-authored Python is never executed**: Claude
   authors the data compiled into the script. The generated script and `.blend`
   are editable; this is intentionally a constrained blockout director.
4. Existing ARM64 Blender runs on CPU, two threads, without CUDA or a model
   load. Its reusable 17-bone rigidly skinned mannequin and geometry derive from
   the owner's existing blockout-director. The checked-in idle/walk/run library
   drives bone poses by travelled distance (run: 2.4 m per full stride).
5. Blender saves evaluated transforms and camera aim for every frame, the
   editable `.blend`, PNG first frame and 24fps H.264 MP4. The UI shows this
   **before** enabling final generation. Corrections create a new immutable plan
   and render; earlier takes are preserved. No planner is called during H3 work.
6. H3 receives original identity images **and actual Blender-rendered footage**
   through installed `MiniMaxH3ReferenceToVideo` → Qwen/VAE reference conditioning.
   Each bounded 5-second clip uses only its opening **22 real-time Blender
   frames**, at the exact output dimensions. This keeps the tested one-video
   context budget. It does not transmit every preview frame to the model.
   Continuations retain the exact preceding AI boundary image guide and replace
   the old AI video context with the new segment's Blender motion window.
7. Existing 90 GiB admission, exclusive leases, clip-by-clip recovery, thermal
   checks, 2000MHz cap, uncertain-acknowledgement handling and sequential output
   assembly remain. The UI pairs the Blender preview with the resulting H3 or
   manually imported Grok video and can play both from the start.

`control_video=false` remains correct: this is reference-video conditioning,
not a verified pose/depth ControlNet and not a guarantee of exact trajectories.
The installed source explicitly tokenizes sampled reference frames and encodes
reference latents. Graph presence alone was not used as evidence of influence;
real acceptance records show the new graph and uploaded video entering H3.

## Supported and limited

- Approximate image analysis and editable 3D staging work; no exact single-image
  reconstruction, dense depth estimation, detailed character modelling or
  automatic retargeting is claimed.
- Primitive environments, moving props, multiple mannequin characters, smooth
  path turns, independent camera paths, follow and orbit cameras are available.
- Clips are stylized blockout locomotion, not realistic human mocap. No suitable
  interaction/IK/hand-contact library is installed; complex object interactions,
  facial acting, wings and automatic rigging of Forge3D meshes remain unsupported.
- Existing Forge3D library retrieval is read-only. A real existing GLB was
  imported and rendered with an orbit camera; no asset-generation model ran.
- H3 may change identity detail, timing, poses, paths and stationary object
  behavior. Inspect the paired videos; short reference windows cannot enforce
  a complete ten-second choreography.
- The existing Hermes xAI video plugin supports OAuth and API-key resolution,
  but **both call the xAI generation API**. Automatic Grok stays disabled under
  the owner's no-Grok-API instruction. No OpenRouter, new authorization flow,
  reinstallation or profile edit was introduced.
- Export for Grok contains `preview.mp4`, `first-frame.png`, `prompt.txt`,
  `scene.py`, `scene.json`, `.blend`, transform telemetry and required compiler/
  selected asset files. Use PNG/prompt in Grok's browser, MP4 only if that browser
  supports it, then import the generated MP4 for comparison. Browser MP4
  conditioning support is not assumed. The PNG is a styled Blender blockout,
  not an AI-restyled photorealistic reconstruction.

## Operations

Only uv manages packages. Verify the existing lock before executing code:

```bash
uv lock --check
uv sync --group blender
uv run --locked --no-sync python -m pytest -q -p no:cacheprovider
```

The optional `blender` group pins NumPy 2.4.2 to match the existing Blender
bundle's NumPy. That ARM64 bundle was missing its OpenBLAS shared library; the
host renderer adds uv's `numpy.libs` to **only its subprocess** library lookup.
It does not replace the bundle, its Python, NumPy, PyTorch or any model service.

Host configuration supports `NEONFORGE_BLENDER_BIN`, `NEONFORGE_FORGE_ASSETS`
and `NEONFORGE_HOST_OUTPUTS_DIR`. Defaults reuse the existing owner directories.
Blender outputs live under `.motion-runtime/scene-outputs`; a read-only nested
mount exposes just the scene artifacts to the gateway's existing output route.
No credential or executable mount was added. New host socket scopes are fixed
`scene/capabilities`, `scene/start`, `scene/status`; no arbitrary command route.

Offline Trellis/Hunyuan endpoints no longer imply unknown busy state when
`docker inspect` authoritatively confirms their **specific existing containers**
are stopped (running false, PID 0, no restart/pause). Running, missing or unknown
containers still refuse admission. No Forge3D/Hunyuan container is started or
stopped by this check. Resident unrelated Ollama models remain blockers.

Subscription quota/billing checks are cached for at most 60 seconds so UI polls
do not flood Claude's status endpoint. Failed checks cool down without retaining
prior success. API/provider environment, unverified paid extra usage and quota
exhaustion continue to refuse Opus. A 3D planning failure returns an actionable
error; it is never represented as a simulated or locally analysed 3D scene.

Real UI acceptance uses the installed Playwright with:
`uv run --locked --no-sync node scripts/verify_motion_3d_browser.cjs`.
It makes real subscription planning calls, and performs no H3 inference unless
`MOTION_3D_RENDER=1` is explicitly set. The separate existing browser regression
uses mocked H3 through `MOTION_TEST_API`; its results are labelled as fixtures.
