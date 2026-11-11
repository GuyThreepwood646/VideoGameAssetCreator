// The picture size the later steps are made for (backend readiness.DETAIL_SUBJECT_PX): every character that built well
// here had a subject 868 to 980 pixels tall, in a picture of about 1 megapixel. Smaller still works, softer; step 1
// then offers "Upscale it".
import type { PickedImage } from './components/ImageDrop'

export const SUBJECT_PX = 900

export const SIZE_ADVICE = `Size: about 1 megapixel (for example 1024 x 1024 or 768 x 1344), with the subject at least ${SUBJECT_PX} pixels tall. A smaller picture still works; step 1 then offers "Upscale it".`

// A note for a picture that cannot reach the size (the subject is never taller than the picture), else null.
export function sizeNote(p: PickedImage | null): string | null {
  if (!p) return null
  const mp = (p.width * p.height) / 1e6
  if (p.height >= SUBJECT_PX && mp >= 0.6) return null
  return `This picture is ${p.width} x ${p.height} pixels, so the subject is under ${SUBJECT_PX} pixels tall and the face and small parts may come out soft. You can still use it: once it is added, "Upscale it" on step 1 makes a bigger copy.`
}
