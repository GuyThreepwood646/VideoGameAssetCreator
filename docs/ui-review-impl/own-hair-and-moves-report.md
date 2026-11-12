# Your own hairstyles and moves (branch claude/own-hair-and-moves)

Owner request: "For the hairstyles and for the moves, we have preset libraries. We should add an option to these for the
user to add their own, so they can expand their options if they have another source."

## What was built

| Part | Where | Notes |
|---|---|---|
| Add your own hairstyle | `frontend/src/pipeline/AddMyHairstyle.tsx`, `HairPicker.tsx` | Button next to "Show more hairstyles". Form: file (GLB, glTF or FBX), name, kind of hairstyle, fringe, curly, where it came from, licence, and the responsibility line. Own styles sit in a "Yours" group with a Yours mark, Change and Remove (the app's own confirm). |
| Upload, checks, registry | `backend/vgac/my_hair.py` | Streamed upload, 80 MB cap (413), header check, plain-name check, duplicate-name check, self-contained .gltf check. Registry `<library>/my_hair/my_hair.json`, files in `my_hair/<id>/`, ids `mine_*` that never shadow a built-in. Remove is refused (409) while a character has it picked. |
| Reading the mesh | `backend/vgac/blender/hairimport.py` | One Blender run: refuses no mesh, a rigged or whole character, too few or too many triangles, a flat mesh. Places the hair on the library's reference head by its crown (width, centre, top), thins it to about 4,500 triangles (the hair share of a PC budget), bakes the same arrays as the pack styles (full and lite), makes the grey texture from the mesh's own colour picture, renders 256 px front and three-quarter thumbnails. |
| Reference head | `backend/vgac/data/hairlib/reference_head.npz` | CC0 Quaternius figure, so imports do not need the hair packs installed. |
| Merge into the library | `hairstyles.py` (`catalogue`, `paths`), `hairlib.py` (file route, `own` flag), `credits.py` | The picker, ranking, fit, phone rule, `LOOKS_OFF` and the credits read own styles like built-in ones. Credits show the source and licence the person gave. |
| Add your own move | `MovesPanel.tsx`, `MyMoves.tsx` | Button next to "Add a move" on step 5 opens the existing own-file flow directly. Yours mark in the list and the picker. Same wording as hairstyles. |
| Your library | `frontend/src/YourLibrary.tsx` on the System page | Own add-ons, hairstyles and moves with where each came from. |
| Demo mode | `frontend/src/mock/hair.ts`, `mock/index.ts` | `/api/my-hair` in the demo; file names with "nomesh", "character" or "huge" give the plain refusals. |

## Proof

- Backend: `backend/tests/test_my_hair.py`, 27 tests (names, headers, form checks, every refusal sentence, add, rename, remove,
  in-use removal, own-id never shadows, catalogue merge, Phone and `LOOKS_OFF`, file route and path safety, credits, `run_import`
  with a Blender stand-in, reference head present).
- Frontend: `npm run build` clean. Demo-mode screenshots at 1280 x 720 and 1440 x 900 in this folder:
  `own-hair-add-form-1280x720.jpg`, `own-hair-yours-group-1280x720.jpg`, `own-hair-yours-group-1440x900.jpg`,
  `own-move-add-1280x720.jpg`, `your-library-system-page-1440x900.jpg`. At both sizes there was no horizontal scroll and the
  Add a move and Add your own move buttons fit.
- Real run on the live backend (port 8765, this branch's code): the CC0 hair file `culturalibre_hair_05` (MakeHuman community
  pack, not in the catalogue, left out of the library because it sits above the head) was turned into a GLB with Blender and
  uploaded through `POST /api/my-hair/upload` as "Messy crop" (short, fringe, CC0). It was picked on the existing character
  "bob (library hair)" (asset 54b387d6-296b-47d1-bf12-f7367e02bf37) and the hair-only rebuild ran: prep version
  575470e3-5e2b-46db-abed-b9e06c4ab291 (style mine_messy_crop, 4,618 triangles with its cap, skin through 0.0 on all,
  crown and sides, fit score 0.53), rig 817e1f2c-86be-4932-88d6-dcbec618ee2d, export 802bee4b-7b21-4d46-bbf5-575fb992d74b.
  Pictures: `own-hair-real-fit-sheet.png` (the fitted hair on the knight from four sides) and `own-hair-real-thumb-quarter.png`.
  The fit score is under the app's 0.6 "poor match" line: this style is a different shape from the character's picture, which
  the picker says in its own words.
- The test hairstyle "Messy crop" was left in the live library (`library/my_hair/`) because the character now uses it;
  remove it from the hair picker after picking another style for that character.

## Not verified

- Orientation: the reader assumes Z up and front -Y (as glTF and FBX imports give); a file modelled facing the other way is
  not detected.
- Only one real file was fitted, a GLB converted from an OBJ without a texture. An FBX, a glTF with embedded pictures, and
  hair with an alpha texture (cards) were not tried in Blender.
- Screen reader, keyboard-only use of the new form, and the Electron launcher were not tried.
- The "length" field named in the request is not a form field: the measured length is stored and the kind of hairstyle covers it.
