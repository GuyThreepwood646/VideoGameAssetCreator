# "Describe a move": text-to-motion and other routes to a custom move

Research date: 2026-11-05. Updates section 5.3 of `CHARACTER-PARTS-RESEARCH.md` (early November 2026).

Hard rules applied: free, runs locally, output usable in a commercial game. Anything trained on SMPL, AMASS or HumanML3D, or with research-only or non-commercial weights, is unusable unless the licence text says otherwise.

Markers: "(secondary)" means confirmed only from a third-party source; "(not verified)" means not confirmed at all. Everything else was read from a primary source (model card, licence file, repository, paper) on the date above. Source numbers in square brackets point to the list at the end.

## Summary

1. The earlier conclusion holds, and the case for Kimodo is stronger. NVIDIA Kimodo, using a SOMA checkpoint trained on Bones Rigplay, is still the only text-to-motion model that is free, runs locally and has commercially usable weights. Since the last review NVIDIA has released v1.2 (15 Oct 2026), which slides the feet less. It also added an official option that runs the text encoder on the CPU, which brings the motion model under 3 GB of VRAM. So it now fits our 10 GB and 12 GB cards.
2. The costs have not changed. You need about 17.5 GB of downloads, most of it a gated Meta Llama 3 8B, and Meta does reject some access requests. The motion reads as "generic mocap": good for blocking out a move and for secondary characters, not for hero animation. Following the prompt is the weak point.
3. HY-Motion (Tencent) now fits a 12 GB card through community quantisation. Its licence still rules it out. Neither the model nor its output may be used or displayed in the EU, the UK or South Korea, and a game sold worldwide breaks that clause. Its training data also leans on GVHMR, which is non-commercial.
4. New and important: NVIDIA GEM-X (v1.1, 30 Oct 2026) turns video of a person into motion on the same SOMA skeleton. NVIDIA states its training data and body model are all owned by NVIDIA or permissively licensed, and the card calls it "ready for commercial use". It is the first commercially clean local "film yourself" route found. Its output goes through the same SOMA-to-Mixamo map as Kimodo's.
5. Every popular ComfyUI motion node (Kimodo wrappers aside) wraps something unusable: MDM or HumanML3D, or GVHMR plus SMPL.

---

## 1. Kimodo today

### 1.1 What it is

| Item | Finding | Source |
|---|---|---|
| Maker | NVIDIA (Spatial Intelligence Lab, nv-tlabs). Paper arXiv 2603.15546, 16 Mar 2026, Rempe et al. | [K1], [K3] |
| Model | Kinematic motion diffusion model, two-stage transformer denoiser, 282M parameters, 100 DDIM steps by default. | [K2], [K3] |
| Versions | v1 (16 Mar 2026), v1.1 (10 Apr 2026), v1.2 and ArdyRep-v1.2 (15 Oct 2026). v1.2 retrains on corrected splits: less foot skating, slightly lower text-following scores. | [K1], [K2] |
| Training data | RP checkpoints: Bones Rigplay 1, about 700 hours of optical mocap from 170 subjects (proprietary, licensed by NVIDIA). SEED checkpoints: BONES-SEED, 288 hours. | [K2], [K3] |
| Output | SOMA skeleton at 30 fps, at most 10 s (300 frames) per generation. The model predicts 30 joints, and the I/O format expands them to the 77-joint `somaskel77` (with fingers, relaxed hands). Output is NPZ (joint positions, local rotations, root, foot contacts, heading). The `kimodo_convert` tool writes SOMA BVH. | [K1], [K2], [K6] |
| Controls | Text prompt, duration, multi-prompt sequences, full-body pose keyframes at chosen frames, end-effector position and rotation targets (hands, feet), 2D root path and waypoints, heading. Seed, CFG weight (text and constraint), step count. | [K1], [K2], [K7] |
| Foot contacts | Predicted contacts are part of the output. The paper says "simple foot locking and IK" cleans up the remaining skate, and the demo uses it (the `motion_correction` C++ module). The benchmark numbers exclude it. | [K3], [C1] |
| Stated limits | Some foot skating; does not always follow the prompt; no cartoon or non-physical motion; no awareness of objects in the scene; one skeleton per model; best at locomotion, gestures, combat, dancing and everyday actions. | [K2] |

### 1.2 Licences

| Part | Licence | Commercial output? | Source |
|---|---|---|---|
| Code (nv-tlabs/kimodo) | Apache-2.0, which covers every file in the repo, including the bundled SOMA skeleton assets (maintainer reply, issue #55, 29 Oct 2026). | Yes | [K1], [K8] |
| Kimodo-SOMA-RP v1, v1.1, v1.2, ArdyRep-v1.2; SOMA-SEED; G1 | NVIDIA Open Model License (version 24 Oct 2025): "Models are commercially usable", "NVIDIA claims no ownership rights in outputs". Conditions: keep the licence and a Notice file if you redistribute the weights; rights end if you sue over the model or bypass its guardrails. The cards say "ready for commercial use". | Yes | [K2], [L1] |
| Kimodo-SMPLX-RP-v1 | NVIDIA R&D Model licence: research only. | **No. Never use it.** Note: kimodo.cpp and some demos mention SMPL-X output; that is this checkpoint. | [K1], [K9] |
| Text encoder | LLM2Vec adapters (McGill-NLP, MIT, 0.17 GB each) on `meta-llama/Meta-Llama-3-8B-Instruct`. That model is under the Meta Llama 3 Community Licence (18 Apr 2024): free below 700M monthly users. If you distribute or make available a product that uses it, you must show "Built with Meta Llama 3". Its outputs may not be used to improve other large language models. It is gated: the user must request access and use a Hugging Face token. | Yes. The motion is not a Llama output used to train an LLM; VGAC would need the attribution line in its docs if it offers the feature. | [K4], [L2], [HF] |
| SEED checkpoints, extra caution | The BONES-SEED dataset licence forbids using the data to train generative motion models [B1], yet NVIDIA trained the SEED checkpoints on it, presumably under a separate agreement (not verified). The weights are still labelled NVIDIA Open Model. **Use the RP checkpoints**, which NVIDIA recommends anyway. | Prefer RP | [K1], [B1] |

A user asked NVIDIA in issue #55 whether baked, retargeted exports can ship in a commercial game. The reply pointed back to the model licence and did not add a separate yes [K8]. The model licence and model card are clear enough on their own.

### 1.3 Downloads, VRAM, speed, platforms

| Item | Finding | Source |
|---|---|---|
| Download | Kimodo-SOMA-RP-v1.2 `model.safetensors` 1.13 GB; LLM2Vec adapters about 0.35 GB; Llama 3 8B Instruct safetensors 16.07 GB. Total about 17.5 GB. (One wrapper reports about 18 GB on first run (secondary).) | [HF], [W5] |
| VRAM, all on GPU | About 17 GB, mostly the text encoder. Does not fit our cards. | [K1] |
| VRAM, encoder on CPU | `TEXT_ENCODER_DEVICE=cpu` (official since 24 Apr 2026): motion model under 3 GB of VRAM, "slightly slower". **Fits the 3080 (10 GB) or the 3060 (12 GB).** With 96 GB of RAM, the CPU encoder is not a problem. | [K1], [K10] |
| Quantised encoder | Community PR #12 (open, not merged): 8-bit about 9 GB, 4-bit about 5 GB, through bitsandbytes. The kimodo.cpp port (Apache-2.0, 953 stars) runs on CPU or Vulkan with a Q8 encoder by default, but it has no constraints, no 77-joint expansion and Linux-only build instructions. | [K11], [K9] |
| Speed | Paper: 2 to 5 s per prompt on an RTX 3090 for the motion model. One Windows user on a 4090 measured about 50 s for a full basic ComfyUI workflow at 17.8 GB VRAM (secondary). **On our cards, estimated (not verified):** first prompt 1 to 2 minutes while 16 GB of encoder weights load into RAM, then perhaps 10 to 40 s per move with the CPU encoder, including the diffusion on the 3080. | [K3], [C3] |
| Windows | The model card lists Windows. The repo says it was built on Linux and Windows "should work", with Docker suggested. Third-party evidence of Windows working: ComfyUI wrappers ship a Windows build of `motion_correction`, but only for Python 3.11. The Blender bridge has been tested on Windows 11. | [K1], [K2], [C1], [C4] |
| CPU only | Not supported as a target; the docs strongly advise a GPU. An AMD user got it running on Windows ROCm PyTorch (issue #30). | [K4], [K12] |

### 1.4 ComfyUI nodes and Blender add-ons for Kimodo

See section 4 for the ComfyUI nodes. Blender:

| Tool | What | Licence | State |
|---|---|---|---|
| Kimodo Blender Bridge (lewdineer) | Runs Kimodo in a managed venv beside Blender. Generate, constraints and timeline in the N-panel. Retargets with Blender constraints (fuzzy bone match, then bake). Blender 4.2 to 5.1, Windows 11 tested, 8 GB+ VRAM recommended with the CPU encoder option. Also on Superhive, free. | GPL-2.0-or-later (manifest) | Active: v1.5.8, 161 stars, last push 21 Aug 2026, 59 issues, many fixed quickly. One issue (#57) reports Rigify characters "exploding" on retarget. | 
| animation-builder (micahmills88) | Web UI: upload a Mixamo FBX, prompt, get a loopable FBX. Retargets through the FBX SDK with a SOMA-to-Mixamo map, auto-trims loops, blends loop ends. Docker, about 17 GB VRAM. | Apache-2.0 | Small (12 stars). Useful for its loop-making ideas. |
| ARDY (NVIDIA, Jul and Oct 2026) | Real-time sibling of Kimodo, with SOMA checkpoints from 15 Oct 2026. Same Llama encoder, same NVIDIA Open Model licence. | Apache-2.0 code, NVIDIA Open Model weights | 1.0k stars. Built for interactive streaming, not needed for offline clips. |

Sources: [C4], [W5], [A1].

### 1.5 Retargeting Kimodo output to our Mixamo-style rig

Read from the SOMA BVH in the repo [K6]. The SOMA body chain is Root, Hips, Spine1, Spine2, Chest, Neck1, Neck2, Head (plus Jaw and eyes), Shoulder, Arm, ForeArm, Hand (plus fingers), and the legs Leg, Shin, Foot, ToeBase, ToeEnd.

Map to our names:

| SOMA | Ours (Mixamo) |
|---|---|
| Hips | Hips |
| Spine1, Spine2, Chest | Spine, Spine1, Spine2 |
| Neck1 + Neck2 (compose the rotations) | Neck |
| Head | Head |
| Left/RightShoulder, Arm, ForeArm, Hand | the same names |
| **Left/RightLeg** (the thigh) | **Left/RightUpLeg** |
| **Left/RightShin** | **Left/RightLeg** |
| Foot, ToeBase | Foot, ToeBase |
| Fingers, Jaw, Eyes | dropped (our rig has no fingers) |

Practical route for VGAC: run `kimodo_convert` to SOMA BVH (use `--bvh_standard_tpose` so the rest pose is a T-pose), import it with Blender's BVH importer, and feed it to the existing `backend/vgac/blender/retarget.py` as another "library". That script already does rest-pose compensation, hip scaling and foot planting through two-bone IK. The only new work is a SOMA bone map, which must not reuse the Mixamo meaning of `LeftLeg`, and a conversion from 30 fps to the bake rate. The ComfyUI wrappers and animation-builder already contain a SOMA-to-Mixamo map in `kimodo_retarget_fbx.py`, which can be used as a cross-check (Apache-2.0).

## 2. Community reception of Kimodo

There is not much hands-on discussion. Search found no Reddit threads and no X posts with substance. Most coverage is press rewrites of NVIDIA's announcement. Some of that coverage overstates the model: one article says foot sliding "essentially disappears", which the paper's own numbers do not support [P1]. Measurable interest:

1. GitHub: about 4.1k stars, 462 forks, 35 issues in 8 months. Maintainers answer and fix things (the CPU encoder option was added within 11 days of an out-of-memory report) [K1], [K10]. Hugging Face: v1.1 has 88 likes and 6.5k downloads; v1.2 has 6.9k downloads and a single discussion [HF].
2. Quality: the most considered write-up (3dxdev, 26 Aug 2026) calls it good enough for blocking but not for final hero animation: plausible, generic motion that lacks the weight shifts of authored animation [P2] (secondary). It adds that many complaints come from setup mistakes: the wrong frame rate (the output is 30 fps), unapplied scale, or the wrong retarget mode. The animation-builder author warns of occasional odd poses, hand clipping and floaty motion, and says durations written into the prompt are ignored [W5].
3. Prompt adherence: the clearest weak point. A Japanese hands-on post (Windows 11, RTX 4090, 6 Apr 2026) found text adherence low across several runs, but said it is accurate "for simple movements" [C3] (secondary). NVIDIA's own card says the same [K2]. Issue #29: a backflip failed until the user restored the default 100 steps and CFG 2.0 [K12]. Lowering steps for speed costs adherence.
4. Foot sliding: the paper reports 3.87 cm/s of foot skate for the full model. v1.2 lowers it further, and the foot-lock post-process cleans up the rest [K3], [K2]. No user report singles foot sliding out as a problem. Without the `motion_correction` module the ComfyUI wrappers warn that the feet slide [C2].
5. Install pain: mostly the gated Llama. Meta has rejected access requests (issues #16 and #39, Apr to Aug 2026), and the issue asking NVIDIA to drop it has no NVIDIA reply [K5]. Others: Docker image index failures (#48), problems on RTX 50-series cards (#53), the Python 3.11-only Windows binary in the wrappers, and the FBX SDK bindings [C1].
6. Known failures: no fingers (feature request closed); no awareness of objects in the scene; motion that is not realistic (cartoon squash, superhuman jumps) is out of scope [K2].

## 3. Other text-to-motion options

| Option | Code / weights licence | Output commercially usable? | Hardware | Reputation | Verdict |
|---|---|---|---|---|---|
| **Kimodo SOMA-RP v1.2** (NVIDIA) | Apache-2.0 / NVIDIA Open Model | Yes | Under 3 GB VRAM with the CPU encoder; 17.5 GB download | Generic but plausible; weak prompt adherence | **The only usable one.** |
| ARDY (NVIDIA, Jul to Oct 2026) [A1] | Apache-2.0 / NVIDIA Open Model | Yes | Same encoder (about 14 GB on GPU by default, CPU option); a community fork offers an NF4 encoder for 6 GB [A2] (secondary) | New, little feedback | Usable, but built for real-time streaming. Kimodo is better suited to offline clips. |
| MotionBricks (NVIDIA, SIGGRAPH 2026) [A3] | Apache-2.0 / NVIDIA Open Model | Yes | RTX 4090 tested; Windows and Linux | Research demo | **Not text-driven** (it fills motion between poses). Not for "describe a move". |
| HY-Motion 1.0 and Lite (Tencent, 30 Dec 2025) [H1], [H2] | Tencent HY-Motion 1.0 Community Licence | **No for a game sold worldwide.** The licence "does not apply in the European Union, United Kingdom and South Korea", and 5(c) forbids using or displaying Output outside the Territory. There is also a 1M monthly-user cap and a ban on using Output to improve other models. | Official 24 to 26 GB. Community: motion network about 8 GB (Lite about 4 GB) plus a Qwen3-8B GGUF encoder of about 5 GB; one wrapper reports it runs on a 3060 12 GB [H3], [H4] (secondary). | Better prompt following than older academic models (its own paper: 3.24 vs about 2.2 instruction score). Users report retarget pain to Mixamo (issue #42) and loops that drift [H5] (secondary). | **Unusable.** Training data also includes motion extracted from video by GVHMR, which is non-commercial [H2]. Output is on the SMPL-H skeleton definition. |
| MDM, MoMask, T2M-GPT, MotionGPT, MotionGPT3, MotionLCM, MARDM, Motion Anything and similar | Code mostly MIT; weights trained on HumanML3D (built from AMASS) and the SMPL body | **No.** Confirmed: the AMASS licence prohibits "any use for commercial purposes" and training networks "for commercial use of any kind" [L3]. | Small, fits | Research benchmarks | **Unusable** (unchanged). |
| OpenT2M / MonoFrill (CVPR 2026) [O1] | No licence stated; "freely available to researchers" | No: motion extracted from web video with WHAM (SMPL), plus Motion-X | n/a | Research | **Unusable.** |
| Meta Motivo, other Meta motion releases | (not verified in this pass) | Earlier releases were CC BY-NC and AMASS-based (not verified) | | | Treat as unusable. |
| Stability, Microsoft, Roblox, Unity, Epic | Nothing found: no open text-to-motion weights from these in 2026 (search found none). | | | | n/a |
| Models trained on licensed studio mocap | Only NVIDIA's (Bones Rigplay). Nothing else found. | | | | Kimodo, ARDY, MotionBricks |
| Hosted text-to-motion: Rokoko Text-to-Motion (free plan, unlimited clips, limited Studio imports) [V3], DeepMotion SayMotion, Animatica (Kimodo-based) | Proprietary | Rokoko free-tier commercial terms not found (not verified). DeepMotion's free tier is non-commercial (secondary). | Cloud, account | | Break the local rule. Mention at most. |
| Cascadeur | Proprietary | Free plan: "Only non-commercial use", saves to .casc only, no FBX [V4] | Local | Good AI-assisted keyframing | **Free tier unusable.** Indie plan USD 8 to 19 per month under USD 100k revenue. |

## 4. The ComfyUI space

| Node pack | Wraps | Licence | Maintained? | Verdict |
|---|---|---|---|---|
| ComfyUI-Kimodo (jtydhr88) [C1] | Kimodo: Load Model, Text Encode, Sampler, Post Process, Preview 2D and 3D, Save NPZ, Export BVH (SOMA), Export FBX (Mixamo, needs the FBX SDK). Windows `motion_correction` binary for Python 3.11 only. | Declared Apache-2.0 in the README, but **no LICENSE file** (open issue #6) | **Stale**: one commit, last push 31 Mar 2026; 9 open issues unanswered (CPU offload, local models, FBX retarget fix). Predates v1.1, v1.2 and the CPU encoder option. | Wraps the right model, but do not depend on it. |
| ComfyUI-Kimodo-Bridge (GuardSkill) [C2] | Fork of the above, adding Unity Humanoid bundle, BVH, Mixamo FBX and Rive export, and the foot-skate post-process. | Apache-2.0 | Last push 4 Sep 2026, 0 stars, not in the registry | Small, young. |
| ComfyUI-Kimodo-Enhanced (stevelittlefish) | Fork adding GLB (Mixamo) export. | Apache-2.0 | Last push 7 Sep 2026, 1 star | Same. |
| ComfyUI-HY-Motion1 (jtydhr88) [H3], ComfyUI-HyMotion (Aero-Ex) | HY-Motion with GGUF and quantised Qwen encoders, Mixamo FBX retarget. | No own licence; defers to Tencent's | Active (Aug 2026), 314 and 45 stars | Unusable model licence. |
| ComfyUI-MotionDiff (Fannovel16) | MDM, MotionGPT (HumanML3D) | MIT code | **Archived** (Aug 2024) | Unusable model, dead. |
| ComfyUI-MotionCapture (PozzettiAndrea), ComfyUI-Video2MotionCapture (AKASubaz) [V1] | GVHMR video-to-SMPL, SMPL-to-BVH and FBX, retarget to Mixamo | GPL-3.0 node code; GVHMR is "educational, research and non-profit purposes only" [V2]; SMPL non-commercial | Active (Aug 2026) and WIP (Feb 2026) | **Unusable.** |
| GEM-X ComfyUI node | none found | | | Would have to be written. |

Conclusion: no ComfyUI node is worth relying on. If VGAC wires Kimodo in, call `kimodo_gen` or the Python API directly from the backend, in its own venv, the way the Blender bridge does. ComfyUI adds nothing here; the model is not image-based.

## 5. Other free routes to a custom move

### 5a. Motion from video

| Tool | Licence | Commercial output? | Practicality for a non-technical user |
|---|---|---|---|
| **NVIDIA GEM-X v1.1** (30 Oct 2026) [G1], [G2] | Code Apache-2.0; weights NVIDIA Open Model; card: "ready for commercial or non-commercial use"; "All training data and the SOMA parametric body model are owned by NVIDIA or released under permissive licenses". SOMA-X body model Apache-2.0 [G3]. Detectors: YOLOX (HumanArt) and ViTPose (licences not checked, not verified). Optional hand model EgoHand is access-managed. | **Yes, per the card.** | One person per video, moving camera allowed, body plus hands, 77-joint SOMA output (the same map as Kimodo's). Windows listed on the card; only an A100 was tested; VRAM not stated (not verified); the rendering extra is Linux-only, but VGAC does not need it. Output is PyTorch `.pt`, so VGAC would write the SOMA BVH or bake itself. Brand new, so there is no community feedback. **The best "film yourself" candidate; needs a trial.** |
| GVHMR | Research and non-profit only; SMPL | No | Popular, good quality, but unusable. |
| WHAM, TRAM, 4D-Humans | MIT code, but they need the SMPL model files (non-commercial) and SMPL-based training data | No | Unusable. |
| Meta SAM 3D Body (+ MHR rig) | SAM License (commercial use allowed per secondary sources, with a litigation clause); MHR Apache-2.0 (secondary) | Probably (secondary) | Single image, not video: per-frame jitter. Used by the paid BlendCap add-on [V5] (secondary). Second choice after GEM-X. |
| MediaPipe Pose Landmarker | Apache-2.0 code; model cards say Apache-2.0 (secondary) | Yes (secondary) | 33 landmarks, weak depth and root motion; needs heavy cleanup. Fine for rough moves only. |
| FreeMoCap | AGPL-3.0 software; your recordings are your own | Yes (the AGPL covers the software, not your data) | Best with 2+ calibrated webcams; MediaPipe underneath. Too fiddly for our users; a standalone app, not something to embed (AGPL). |
| Rokoko Vision | Hosted; free plan 30 s of video processing per month, FBX export [V3] | Free-tier commercial terms not found (not verified) | Easy, but needs an account and the cloud. One-line mention. |
| DeepMotion, Move.ai, Plask | Hosted | DeepMotion free tier is non-commercial (secondary); others not verified | Break the local rule. |
| Video from a local video model, then extraction | Depends on the video model's licence (for example LTX-2.5) and on the extractor | Only with a clean extractor (GEM-X) | Generated video often has body warping that becomes bad motion. Experimental only. |

### 5b. Free animation libraries

| Library | Licence | Can VGAC bundle it? | Verdict |
|---|---|---|---|
| Quaternius UAL 1 and 2, Mesh2Motion | CC0 / MIT | Yes | In use already. |
| Mixamo (Adobe) [M1] | Royalty-free in games; you may not distribute the raw character or animation files (for example as asset packs or templates). Needs an Adobe account. The only text is a 2022 Adobe forum FAQ, and a 2026 question on whether it still applies has no reply (secondary). | **No**; the user downloads, VGAC imports | Keep as "bring your own Mixamo FBX". |
| CMU Graphics Lab mocap [M2] | "free for all uses"; "You may include this data in commercially-sold products, but you may not resell this data directly, even in converted form." | Only baked into a user's game. Shipping the raw BVH library inside VGAC looks like redistribution, which is risky | User-side or picked clips only. Raw capture needs cleanup; the 4TU FBX conversion is CC BY-NC (avoid). |
| 100STYLE (Ian Mason) [M3] | CC BY 4.0, with credit "The 100STYLE Dataset - Ian Mason" | Yes, with attribution | **Usable.** 100 locomotion styles (stylised walks), BVH. A good addition for "walk like X". |
| LAFAN1 (Ubisoft) [M4] | CC BY-NC-ND 4.0 | No | Unusable. |
| Bandai Namco Research Motion Dataset 1 and 2 [M5] | CC BY-NC 4.0 | No | Unusable. |
| BONES-SEED (Bones Studio) [B1] | Free only for academics and for companies under USD 1M revenue; credit required; no redistribution; ends 30 days after you pass USD 1M; no training of generative motion models | No | User-side only, and a risky dependency (it is revocable and has a revenue cliff). Not recommended. |
| Truebones free packs [M6] | Gumroad page (2019): "royalty free" for "any and all purposes even commercial"; full terms of service not read (not verified) | Not verified | Possibly usable as user-supplied; check the terms of service first. |
| Rokoko free packs | Commercial use in projects (secondary); redistribution terms not found | No | User-supplied only (unchanged). |
| MoCap Online free sampler | Commercial up to 1M users or USD 1M revenue, but "AI Usage Permit" required for AI applications (secondary) | No | Avoid. |
| Kenney | Kenney's assets are generally CC0, but the animated humanoid packs have only a few clips (3 in Animated Characters 3) (secondary) | Yes if CC0 | Too few clips to matter. |
| itch.io CC0 packs | Per pack | Per pack | Case by case, no standard rig. |

### 5c. Keyframe assistance

| Tool | Licence | Verdict |
|---|---|---|
| Cascadeur free plan [V4] | Non-commercial only, .casc only, 300 frames, 120 joints | Unusable for this project. |
| Kimodo constraints mode | Same as Kimodo | **The free "few poses into a move" tool.** Kimodo takes full-body keyframes, hand and foot targets and a root path, and fills in the motion between them. A user could pose 2 or 3 key poses (or pick them from an existing clip) and let Kimodo in-between them. It still needs the text encoder; a constraint-only mode was requested but does not exist (not verified). |
| MotionBricks (NVIDIA) | Apache-2.0 / NVIDIA Open Model | In-betweens between poses in real time; no text; built for runtime use. Possible later. |
| Blender built-ins (Rigify IK, pose library, NLA) | GPL, free | Manual; the realistic fallback for a determined user, not for our target user. |

No free, commercially usable Blender "AI keyframe" or physics add-on was found that beats Kimodo's constraints mode (not exhaustively verified).

## 6. Recommendation for VGAC

### 6.1 Wire in: Kimodo SOMA-RP v1.2 as an optional "Describe a move"

Why: it is the only free, local, commercially clean text-to-motion model; it now fits our cards; and its output goes through the retarget bake we already have.

Honest costs:

1. Download: about 17.5 GB (1.1 GB motion model, 0.35 GB adapters, 16.1 GB Llama 3 8B). On a fresh install the user must request Llama access from Meta, which can take time or be refused, and paste a Hugging Face read token.
2. Hardware plan: motion model on the 3080 (under 3 GB VRAM), text encoder on the CPU (`TEXT_ENCODER_DEVICE=cpu`, 96 GB of RAM is plenty). It can run while ComfyUI holds the 3060. If speed matters, a later option is an 8-bit encoder on the 3060 (community PR, about 9 GB, untested).
3. Time per move (estimate, not verified on our machine): 1 to 2 minutes the first time, then roughly 10 to 40 s per 3 to 6 s clip at 100 steps. Generate 3 or 4 seeds and let the user pick. Keep the defaults (100 steps, CFG 2.0); lowering them breaks harder moves.
4. Expected quality: plausible, realistic motion-capture style. Good for common actions (attacks, gestures, dances, idles, reactions, locomotion variants). Prompt adherence is hit and miss, so a pick-from-several UI is required. No fingers, no props, no cartoon exaggeration. Stylised proportions should be handled by our own retarget (rest-pose compensation, leg-ratio hip scale, foot planting). How well that works on chibi or big-headed bodies is the main unknown and needs a trial.
5. Engineering: own venv (Python 3.10 to 3.12, PyTorch with CUDA), `kimodo_gen`, then `kimodo_convert` to SOMA BVH (T-pose rest), then a new SOMA bone map in the existing `retarget.py`. Skip the ComfyUI wrappers.
6. Licence chores: show the user the NVIDIA Open Model and Llama 3 licences at download. Put "Built with Meta Llama 3" and "Licensed by NVIDIA Corporation under the NVIDIA Open Model License" in VGAC's About or credits. Never offer the SMPL-X checkpoint. Prefer the RP checkpoints over the SEED ones.

### 6.2 Best fallback: "Pick from a bigger free library", then "film yourself"

1. Cheapest win: add 100STYLE (CC BY 4.0, bundling allowed with credit) next to Quaternius for stylised locomotion, and keep "bring your own Mixamo FBX" for users who have an Adobe account.
2. Film yourself, in a later trial: NVIDIA GEM-X (commercial per its card, same SOMA output, so the same bone map serves both features). Its value: the user records the exact move on a phone. Risks: brand new (released 6 days ago), VRAM and Windows behaviour unverified, detector licences not checked.

### 6.3 Not worth doing

1. HY-Motion in any form: territory clause on output, GVHMR-derived training data.
2. Any MDM, MoMask, T2M-GPT, MotionGPT or MotionDiff node, or any GVHMR, WHAM, TRAM or 4D-Humans video route: SMPL and AMASS licences.
3. The existing ComfyUI Kimodo nodes: stale or tiny, and they add a Python 3.11 and FBX SDK constraint for no gain.
4. Cascadeur free, LAFAN1, Bandai Namco, BONES-SEED, MoCap Online sampler: licence.
5. Hosted tools (Rokoko, DeepMotion, Move.ai, Plask, Animatica) inside the app: they break the local rule. A one-line pointer in help is enough.
6. kimodo.cpp for now: no constraints, no full skeleton, Linux build.

### 6.4 Uncertainty

1. Speed of Kimodo on our 3080 with a CPU encoder: not measured.
2. Quality of the retarget onto stylised VGAC bodies: unknown until trialled.
3. Whether Meta will grant Llama access to every user: not guaranteed. Ungated re-uploads of Llama 3 weights exist and the Llama licence permits redistribution with attribution, but relying on third-party mirrors is a judgment call (not verified which mirrors are faithful).
4. GEM-X: VRAM, Windows, and the licences of its third-party detectors: not verified.
5. Mixamo's current terms: only a 2022 FAQ is available.

---

## Sources

Kimodo and NVIDIA
- [K1] Kimodo GitHub README, model table and news (read 2026-11-05; v1.2 dated 2026-10-15): https://github.com/nv-tlabs/kimodo
- [K2] Kimodo-SOMA-RP-v1.2 model card (2026-10-15): https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1.2 ; v1 card: https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1
- [K3] Kimodo paper, arXiv 2603.15546 (2026-03-16): https://arxiv.org/html/2603.15546v1
- [K4] Kimodo installation docs: https://research.nvidia.com/labs/sil/projects/kimodo/docs/getting_started/installation.html
- [K5] Issue #39, gated Llama vs Qwen3 (2026-06-16 to 2026-08-22): https://github.com/nv-tlabs/kimodo/issues/39 ; issue #16 (access rejected, 2026-04)
- [K6] SOMA 77-joint T-pose BVH and motion_convert docs in the repo: https://github.com/nv-tlabs/kimodo/blob/main/kimodo/assets/skeletons/somaskel77/somaskel77_standard_tpose.bvh ; https://github.com/nv-tlabs/kimodo/blob/main/docs/source/user_guide/motion_convert.md
- [K7] Generation parameters docs: https://research.nvidia.com/labs/sil/projects/kimodo/docs/user_guide/configuration.html
- [K8] Issue #55, licence clarification (2026-10-28/29): https://github.com/nv-tlabs/kimodo/issues/55
- [K9] kimodo.cpp (Apache-2.0, last push 2026-10-30): https://github.com/localai-org/kimodo.cpp
- [K10] Issue #27, OOM on 16 GB and the TEXT_ENCODER_DEVICE fix (2026-04-14 to 04-25): https://github.com/nv-tlabs/kimodo/issues/27
- [K11] PR #12, quantised encoder (open, 2026-03-26): https://github.com/nv-tlabs/kimodo/pull/12
- [K12] Issues #29 (backflip, 2026-04) and #30 (AMD, 2026-04/05): https://github.com/nv-tlabs/kimodo/issues/29 ; https://github.com/nv-tlabs/kimodo/issues/30
- [HF] Hugging Face API file sizes and stats, read 2026-11-05: Kimodo-SOMA-RP-v1.2, McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp(-supervised), meta-llama/Meta-Llama-3-8B-Instruct
- [A1] ARDY repo (checkpoints 2026-07-10 and 2026-10-15): https://github.com/nv-tlabs/ardy
- [A2] ARDY community fork with NF4 encoder (secondary): https://github.com/Aero-Ex/ardy
- [A3] MotionBricks: https://github.com/NVlabs/motionbricks
- [G1] GEM-X model card (v1.1, 2026-10-30): https://huggingface.co/nvidia/GEM-X
- [G2] GEM-X repo: https://github.com/NVlabs/GEM-X
- [G3] SOMA-X model card: https://huggingface.co/nvidia/SOMA-X

Licences
- [L1] NVIDIA Open Model License (2025-10-24): https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
- [L2] Meta Llama 3 Community Licence (2024-04-18): https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct/blob/main/LICENSE
- [L3] AMASS licence: https://amass.is.tue.mpg.de/license.html
- [B1] BONES-SEED licence: https://huggingface.co/datasets/bones-studio/seed/blob/main/LICENSE.md

Community and wrappers
- [C1] ComfyUI-Kimodo (jtydhr88; last push 2026-03-31): https://github.com/jtydhr88/ComfyUI-Kimodo
- [C2] ComfyUI-Kimodo-Bridge (GuardSkill; 2026-09-04): https://github.com/GuardSkill/ComfyUI-Kimodo-Bridge ; Enhanced fork: https://github.com/stevelittlefish/ComfyUI-Kimodo-Enhanced
- [C3] "Trying out Kimodo in ComfyUI", note.com (2026-04-06) (secondary): https://note.com/kongo_jun/n/n0656b49249cf
- [C4] Kimodo Blender Bridge (v1.5.8, GPL-2.0-or-later): https://github.com/lewdineer/Kimodo_Blender_Bridge
- [W5] animation-builder (Apache-2.0): https://github.com/micahmills88/animation-builder
- [P1] Efficienist press article (secondary): https://efficienist.com/nvidias-kimodo-generates-studio-quality-3d-motion-from-text-and-its-free/
- [P2] 3dxdev, "Text to Motion in Blender" (2026-08-26) (secondary): https://3dxdev.com/how-to-generate-animation-from-text-in-blender-free-2026/ ; kimodo.cpp article (2026-08-23): https://3dxdev.com/assets/kimodo-cpp-text-to-motion-animation-running-locally-in-c/

HY-Motion and other models
- [H1] HY-Motion repo and licence file (Tencent HY-Motion 1.0 Community License, release 2025-12-30): https://github.com/Tencent-Hunyuan/HY-Motion-1.0 ; https://github.com/Tencent-Hunyuan/HY-Motion-1.0/blob/main/License.txt
- [H2] HY-Motion paper, arXiv 2512.23464: https://arxiv.org/html/2512.23464v1
- [H3] ComfyUI-HY-Motion1: https://github.com/jtydhr88/ComfyUI-HY-Motion1 ; ComfyUI-HyMotion: https://github.com/Aero-Ex/ComfyUI-HyMotion
- [H4] hymotion-local, 12 GB wrapper (secondary): https://github.com/rottenvia/hymotion-local
- [H5] HY-Motion issue #42, Mixamo retarget: https://github.com/Tencent-Hunyuan/HY-Motion-1.0/issues/42
- [O1] OpenT2M paper: https://arxiv.org/html/2603.18623v1
- ComfyUI-MotionDiff (archived): https://github.com/Fannovel16/ComfyUI-MotionDiff

Video and libraries
- [V1] ComfyUI-MotionCapture: https://github.com/PozzettiAndrea/ComfyUI-MotionCapture ; ComfyUI-Video2MotionCapture: https://github.com/AKASubaz/ComfyUI-Video2MotionCapture
- [V2] GVHMR licence: https://github.com/zju3dv/GVHMR/blob/main/LICENSE
- [V3] Rokoko Vision plans: https://www.rokoko.com/products/vision
- [V4] Cascadeur plans: https://cascadeur.com/plans
- [V5] BlendCap docs (secondary): https://superhivemarket.com/products/blendcap/docs ; SAM 3D Body licence summary (secondary): https://www.libreyolo.com/docs/models/sam-3d-body
- FreeMoCap (AGPL-3.0): https://github.com/freemocap/freemocap ; MediaPipe (Apache-2.0): https://github.com/google-ai-edge/mediapipe
- [M1] Mixamo FAQ repost on Adobe Community (2022) (secondary): https://community.adobe.com/t5/mixamo-discussions/mixamo-faq-licensing-royalties-ownership-eula-and-tos/td-p/13234775
- [M2] CMU Graphics Lab Motion Capture Database: http://mocap.cs.cmu.edu/
- [M3] 100STYLE: https://www.ianxmason.com/100style
- [M4] LAFAN1: https://github.com/ubisoft/ubisoft-laforge-animation-dataset
- [M5] Bandai Namco Research Motion Dataset: https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset
- [M6] Truebones terms (secondary): https://truebones.gumroad.com/p/truebones-terms-of-use-for-truebones-motions-t-o-s

## GEM-X checks before a trial (2026-11-05)

Checked against the Hugging Face file listings and the repo's own docs (README, docs/PRETRAINED_MODELS.md, docs/INSTALL.md, ATTRIBUTIONS.md, the model card).

Download size
- nvidia/GEM-X lists 10.5 GB, but the pose detector is there twice (vitpose.pth 3.39 GB and the same model as ONNX 3.39 GB).
- Body motion from a video needs: body_s240000.ckpt 3.54 GB, vitpose.pth 3.39 GB, motion_native77 checkpoint 0.19 GB, the person detector 0.37 GB (zip, from OpenMMLab), and SOMA-X assets up to 0.86 GB. About 8.4 GB, plus a Python environment with PyTorch 2.10.
- Hands (optional): EgoHand-v1 checkpoint 10.66 GB plus an 0.08 GB hand detector.

Memory
- Still not stated. The only hardware named is an A100 80 GB. The motion model itself is small (46.8M parameters); the two large files are the pose detector and the body-pose feature model, about 3.4 to 3.5 GB each on disk. Estimate, not a measurement: about 4 to 5 GB of graphics memory if they run one after another, 8 GB or more if both stay loaded. Only a run on the 3060 (12 GB) and the 3080 (10 GB) settles it.
- Windows is listed as a supported operating system on the model card. The rendering extra is Linux-only; VGAC does not need it.

Licences of the detectors
- Pose detector ("vitpose.pth"): not the public ViTPose weights. It is NVIDIA's own 77-point model (release id vitpose77-dinov3-huge-metrosim-256x192-v1), shipped inside nvidia/GEM-X under the NVIDIA Open Model License, so it falls under the card's "ready for commercial or non-commercial use". The name indicates a DINOv3 backbone (Meta); the card does not discuss that licence separately.
- Person detector (YOLOX-X HumanArt, downloaded separately from OpenMMLab): the code is Apache-2.0, but the weights were trained on the Human-Art dataset, which its authors release "for non-commercial purposes". NVIDIA's commercial statement does not cover this file. It is optional: gem-infer takes person boxes from elsewhere (--boxes), so VGAC should supply boxes from a cleanly licensed detector instead of downloading this one.
- ATTRIBUTIONS.md also lists SAM 3D Body under Meta's SAM License (commercial use allowed, with trade-control and weapons-use restrictions and a clause ending the licence if you sue Meta over it), and code pieces under MIT and BSD-3.

Hand model
- nvidia/EgoHand-v1 and nvidia/RT-DETR-Hand-Detector-v1 are not gated on Hugging Face today (the API reports gated: false), despite the repo saying access is "managed" there. Both are under OpenMDW-1.1, a permissive model licence. The cost is size: 10.66 GB.

Output
- PyTorch .pt files; no BVH or FBX export. VGAC would write the conversion to the SOMA skeleton itself (the same bone map Kimodo would need).

## GEM-X test on this machine (2026-11-05)

Installed to F:\Tools\gemx (Python 3.12, PyTorch 2.10 cu128, about 12.4 GB without caches). The YOLOX HumanArt detector was never downloaded; the person boxes come from RT-DETRv2-S (PekingU/rtdetr_v2_r18vd, Apache-2.0, trained on COCO), swapped in for GEM-X's detector class only. Scripts: runtime/gemx (converter gemx_to_bvh.py, sheets), runner F:\Tools\gemx\scripts\run_gemx.py. Result sheets: runtime/gemx/results. Test footage: library/my_videos (Wikimedia Commons, see CREDITS.txt there).

Results
- Real footage of one man walking on a beach, hand-held panning camera, 17.8 s: works end to end. GEM-X output, converted to a 61-joint BVH, went through the existing "Use my own animation file" route ("We recognised 22 of the 22 main bones") onto the Ranger. Same walk, right facing, feet mostly planted (about 6 cm/s of slide while a foot is down), no visible shake. GEM-X's built-in camera-turn estimate is needed for a panning camera (without it the path curves about 60 degrees) and was enough; ViPE was not needed and not downloaded (part of it is CC BY-NC-SA).
- Crowded street, 14 to 21 people per frame: fails as one person's motion. The followed person changes; only the span where one track was confirmed is usable. A feature must keep only confirmed frames and show who was picked.
- Small far subject (about 15 percent of frame height): works.
- Known motion (rendered Ranger, sanity check only): walk distance 93 percent of true; an on-the-spot attack drifts about 0.6 m; a jump sits about 14 cm high.
- Hands are weak without the separate 10.7 GB hand model.

Cost
- RTX 3060, full precision, models one after another: peak about 4.4 GB of graphics memory; about 10 seconds of processing per second of video. GEM-X's limit is 60 s per run.

Open licence questions before shipping a feature
- ATTRIBUTIONS.md lists Meta's SAM License for SAM 3D Body; in the code it appears as an optional training warm-start. Whether the released body weights derive from it is not stated.
- Both large models use a DINOv3 backbone (Meta); its licence is not discussed on the card.
- RT-DETRv2-S: COCO images are Flickr photos under mixed licences (only the annotations are CC BY 4.0), and the backbone was probably pre-trained on ImageNet. The usual industry grey zone.

What a "From a video" feature needs
- A background job (GEM-X, converter, then the existing own-file route), a trim control, a picture of who was picked with a way to choose another person, an "in place" option for moves done on the spot, ground levelling limited to small slopes, and filming advice: one person, whole body in view, phone held still or turned slowly, 3 to 15 s, start and end standing still.
