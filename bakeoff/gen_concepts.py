"""Phase 0 bake-off, step 1: concept images for three subjects on instance B (RTX 3080).

Z-Image Turbo, 1024 square, 8 steps, CFG 1. Two seeds per subject so a pick exists.
Output: bakeoff/out/concepts/<subject>_<seed>.png
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
from comfy import B, BS, run, download, images_of

OUT = os.path.join(os.path.dirname(__file__), "out", "concepts")

READY = (" The character stands in a relaxed A-pose, arms held slightly away from the body, palms inward, "
         "feet shoulder-width apart, facing the camera directly, whole figure visible from head to toe with "
         "margin around it, centred in frame. Plain flat light grey background. Soft even studio lighting "
         "from the front with no hard shadows. No text, no logos, no background scenery, no motion blur, "
         "no cropping, nothing held in the hands.")

READY_QUAD = (" The animal stands in a neutral four-legged standing pose, all four feet on the ground, head level "
              "and facing forward, seen from a three-quarter front angle so the face, chest and one flank are "
              "visible, whole body visible with margin around it, centred in frame. Plain flat light grey "
              "background. Soft even studio lighting from the front with no hard shadows. No text, no logos, "
              "no background scenery, no motion blur, no cropping.")

READY_PROP = (" The object sits on a plain flat light grey surface against a plain flat light grey background, "
              "seen from a three-quarter front angle slightly above, whole object visible with margin around it, "
              "centred in frame. Soft even studio lighting with no hard shadows. No text, no logos, no other "
              "objects, no scenery, no motion blur, no cropping.")

SUBJECTS = {
    "humanoid": (
        "A full-body painted concept art reference of a stylized fantasy knight character for a video game, "
        "a real digital painting with smooth painterly shading. A sturdy young woman with short dark hair and "
        "no helmet, wearing worn steel plate armour with brass trim over a deep blue padded tunic, leather "
        "gloves, heavy boots, a small round shield strapped flat on her back. Clean readable shapes, slightly "
        "exaggerated proportions with a large head and hands, muted earthy colours with one bright blue accent."
        + READY),
    "quadruped": (
        "A full-body painted concept art reference of a stylized fantasy wolf creature for a video game, a real "
        "digital painting with smooth painterly shading. A lean grey wolf with a thick cream chest ruff, dark "
        "stripes along the back, a long bushy tail held low, pointed ears, amber eyes, slightly oversized paws. "
        "Clean readable shapes, slightly exaggerated proportions, muted cool colours with warm amber accents."
        + READY_QUAD),
    "prop": (
        "A painted concept art reference of a stylized wooden treasure chest prop for a video game, a real digital "
        "painting with smooth painterly shading. A small sturdy chest with a curved lid, dark oak planks, chunky "
        "iron bands and rivets, a heavy brass padlock on the front, a few scuffs and scratches. Clean readable "
        "shapes, slightly exaggerated chunky proportions, warm brown wood with dark iron and bright brass."
        + READY_PROP),
}


def zimage_graph(prompt, seed, prefix):
    ckpt = "Z-Image" + BS + "z-image-turbo-fp8-aio.safetensors"
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                                                  "latent_image": ["5", 0], "seed": seed, "steps": 8, "cfg": 1.0,
                                                  "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "bakeoff/concepts/" + prefix}},
    }


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    want = sys.argv[1:] or list(SUBJECTS)
    for name in want:
        for k in range(2):
            seed = random.randint(1, 2**31)
            outputs, secs = run(B, zimage_graph(SUBJECTS[name], seed, f"{name}_{seed}"))
            for img in images_of(outputs):
                dest = os.path.join(OUT, f"{name}_{seed}.png")
                download(B, img, dest)
                print(f"{name} seed {seed} {secs}s -> {dest}", flush=True)
