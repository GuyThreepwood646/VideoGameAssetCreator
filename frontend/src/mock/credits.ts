// Mock answer for GET /api/credits: a short sample in the same shape as the real one (backend/vgac/credits.py).
import type { CreditEntry, CreditsAnswer } from '../api'

const e = (name: string, does: string, author: string, licence: string, link: string, extra: Partial<CreditEntry> = {}): CreditEntry => ({
  name, does, author, licence, link, attribution_required: false, in_your_assets: false, note: '', confirmed: 'records', ...extra,
})

export const mockCredits: CreditsAnswer = {
  summary: {
    not_legal_advice: "This page is information, not legal advice. Licences are the authors' own terms; read the linked ones if a lot depends on it.",
    passing_on: {
      title: 'If you share the app with other people, what you must do',
      points: [
        'Keep THIRD-PARTY-NOTICES.md and CREDITS.md with the app; the library licences require their notices to travel with it.',
        "The AI models are not shipped. Each person downloads them, so each model's own licence applies to that person.",
        'Blender and ComfyUI are GPL tools that the app calls but does not include.',
      ],
    },
    before_sharing: {
      title: 'Before you share the app (licence choices and what they rest on)',
      points: [
        "The app is GPL-3.0-or-later: pass on the source and keep the notices. Later versions could be licensed differently, but others' code would need their agreement.",
        "Blender's FAQ says shared Python scripts that use its interface must be GPL; the app is GPL-3.0-or-later, so that is met. vgac-nodes is GPL too.",
      ],
    },
    game_credit: {
      title: 'What you must credit in a game made with these assets',
      points: [
        "As recorded, nothing that ends up inside an exported asset needs a credit. The animations and hairstyles are CC0 (public domain dedication), and the AI models' licences cover the model files, not what they make.",
        'Crediting is still polite, and this page lists everyone so you can copy it.',
        'Two exceptions are recorded: Hunyuan3D (optional; not granted in the EU, UK or South Korea; a 1-million-monthly-users clause) and Anima (model non-commercial, what it makes is usable).',
      ],
    },
  },
  groups: [
    { id: 'models', title: 'AI models (the trained files that make pictures and 3D shapes)', intro: 'These run on your computer. Their files are not part of your game; what they make is.', entries: [
      e('Z-Image Turbo', 'Paints the first concept picture quickly.', 'Tongyi-MAI (Alibaba)', 'Apache-2.0', 'https://huggingface.co/Tongyi-MAI/Z-Image-Turbo', { confirmed: 'lookup' }),
      e('Hunyuan3D-2mv', 'Optional second 3D model that builds the shape from several views.', 'Tencent', 'Tencent Hunyuan Community Licence', 'https://huggingface.co/tencent/Hunyuan3D-2mv', { note: 'Opt-in. Revenue clause and regional limits.' }),
    ] },
    { id: 'addons', title: 'Add-ons (LoRAs: small files that steer a model toward a look)', intro: 'Each one is loaded on top of a model above.', entries: [
      e('Hand-made clay finish', 'Gives pictures a hand-made clay look.', 'artificialguybr', 'Apache-2.0', 'https://huggingface.co/artificialguybr/ClayAnimation-Redmond-ZIMAGE'),
    ] },
    { id: 'data', title: 'Ready-made data (moves, light pictures)', intro: 'Files that come with the app. Moves can end up inside your characters.', entries: [
      e('Universal Animation Library (Standard)', 'The ready-made moves your rigged characters can use.', 'Quaternius', 'CC0 1.0', 'https://quaternius.com', { in_your_assets: true }),
    ] },
    { id: 'hair', title: 'Hairstyles (the hair library)', intro: 'Every style is CC0 (public domain dedication).', entries: [
      e('Hairstyle: Two buns', 'A hairstyle you can give a character.', 'Quaternius', 'CC0-1.0', 'https://quaternius.com', { in_your_assets: true }),
    ] },
    { id: 'tools', title: 'Tools (outside programs the app calls)', intro: 'None of these is included in the app.', entries: [
      e('Blender', 'Does the heavy 3D work.', 'Blender Foundation', 'GPL-3.0-or-later (a GPL tool)', 'https://www.blender.org/about/license/'),
      e('ComfyUI-SkinToken', 'Lets ComfyUI run the rigging model.', 'Rizzlord', 'not confirmed', 'https://github.com/Rizzlord/ComfyUI-SkinToken', { confirmed: 'not confirmed' }),
    ] },
    { id: 'libraries', title: 'Software libraries (code the app is built from)', intro: 'Their licence notices must travel with the app if you pass it on.', entries: [
      e('React', 'Draws every screen of the app.', 'Meta and contributors', 'MIT', 'https://react.dev'),
    ] },
  ],
}
