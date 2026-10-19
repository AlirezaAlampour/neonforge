# Quality audit — before changes, 2026-10-19

Existing branch: motion-director-v0.1 at 138add8. Same running-character reference,
seed 42/43 and stored ten-second Blender/H3 pair are the baseline.

| Stage | Actual baseline behavior |
| --- | --- |
| Opus vision input | Original reference preserved; JPEG vision copy capped at 1024 square, quality 3. |
| Scene planning | Cinematic text and metric scene generated together. The final H3 prompt also contains the raw user prompt, implementation terms such as mannequin, and repeated/corrected camera distances. No distinct H3 context representation. |
| Native canvas | Application planner hardcodes 384×672 (landscape reversed); host preview validator and supervisor allowlist enforce it. This is an intentionally conservative application envelope, **not H3's native limit**. |
| Blender | Actual 384×672, 24fps, 240 PNG frames. One-sample emission materials, generic rigid mannequin and boxes; preview H.264 CRF20, yuv420p, 162 kbit/s video. Low bitrate reflects simple content, not a configured bitrate cap. |
| Original image into H3 | Resized to 672px long edge, then ref_image_size=match reduces to generation pixel area. Original image still enters Qwen and VAE when Blender video is present; it is not disconnected. |
| Motion into H3 | Only opening 22 frames (0.917s) per five-second segment at 24fps, re-encoded at x264 default CRF23. Installed implementation accepts this, but official reference guidance expects 2–15 second video references. Qwen sees only two timestamped frames from this window. |
| Generation | Ref2VA INT8-convrot + Qwen32B NVFP4/AWQ + INT8 video VAE, existing 4-step v0.1 Turbo LoRA, res_multistep/simple, denoise 1, seeds42/43; no CFG increase. Only this reference checkpoint/LoRA is installed. |
| H3 raw SaveVideo | 384×672, 24fps, 124 frames, 5.167s; default H.264 encoder quality (CRF23), 1.729/1.190 Mbit/s video for the two segments. |
| Trim/assembly | Trim to 120 frames, second clip skips duplicate boundary. Re-encode CRF18, no spatial resize. Final concat copies video; AAC re-encoded. Final 384×672, 240 frames, 10.031006s, video2.019Mbit/s. No upscaling/downscaling at export. |
| Compression measurement | Raw vs first trimmed clip SSIM0.989753, PSNR43.817dB over the same120 frames. Compression is measurable but is not the main source of the coarse image/temporal drift visible before export. |

Installed H3 code has a default 768×1344 native canvas, 24fps, 32px alignment
and a 768×1344 reference area cap. Official local H3-Base guidance targets768p;
2K regeneration is a separate hosted module, not installed. No hosted service or
upscaler is part of this work. Native capability is not a DGX benchmark result:
higher resolution must pass actual admission, memory/runtime and visual tests.

Blur attribution: limited native pixel count and identity detail lost by image
preprocessing are proven; some additional compression loss is measured. Scene
and character changes across time are visible in the generated raw clips, not
created by concatenation. Quantization, distilled sampling and competing image/
video appearance references remain possible contributors; their individual
causal effect cannot be asserted without controlled tests or different weights.

Primary sources inspected:
- https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/main/README.md
- https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md
- https://github.com/ModelTC/Minimax-H3-Turbo/blob/main/COMFYUI_SETUP_AND_INFERENCE.md
- Installed ComfyUI nodes_minimax_h3.py, nodes_video.py and video input implementation.

The A/B design changes one group at a time: A stored baseline; B optimized Opus
H3 context with the old reference footage/settings; C improved scene reference
with B's context/settings; D longer motion + appearance anchoring + native
resolution and explicit export quality. D is a grouped improvement, not a causal
claim about each individual option. Identity/scene/camera assessment must include
actual frames and clips; low-level image metrics alone cannot prove identity.
