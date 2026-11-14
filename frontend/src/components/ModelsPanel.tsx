// The models panel: the sets of model files, what is already there, licence gates, and the "make one test character"
// check. Reads GET /api/model-sets (backend/vgac/models_setup.py). Files go where the linked ComfyUI reads them; nothing
// is downloaded without the button, and a licence is accepted only by typing its sentence.
import { useCallback, useEffect, useState } from 'react'
import { api, type ModelItem, type ModelSet, type ModelSets } from '../api'
import ModelFileChoice from './ModelFileChoice'
import ModelFolderChoice from './ModelFolderChoice'

function size(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`
}

const STATE_WORD: Record<ModelItem['state'], string> = { present: 'there', missing: 'missing', differs: 'differs', checking: 'being checked' }

export default function ModelsPanel({ onChanged, onClose }: { onChanged?: () => void; onClose?: () => void }) {
  const [data, setData] = useState<ModelSets | null>(null)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)
  const [typed, setTyped] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<string | null>(null)
  const [testNote, setTestNote] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await api.modelSets())
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const working = !!data?.sets.some((s) => s.job?.state === 'working') || data?.verify.state === 'working' || Object.values(data?.copying ?? {}).some((c) => c.state === 'working')
  useEffect(() => {
    if (!working) return
    const t = window.setInterval(() => void load(), 1500)
    return () => window.clearInterval(t)
  }, [working, load])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setAnswer(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const starter = data?.sets.find((s) => s.id === 'starter')
  const passed = data?.test_character.passed

  const makeTest = () =>
    run(async () => {
      setTestNote('Making the test character. This runs the six steps and can take several minutes.')
      const [cats, styles] = await Promise.all([api.categories(), api.styles()])
      const category = cats.find((c) => c.id === 'humanoid') ?? cats[0]
      const project = await api.createProject({ name: 'Setup test' })
      const asset = await api.createAsset(project.id, {
        name: 'Setup test character',
        category: category.id,
        style_preset: styles[0].id,
        prompt: 'a simple friendly knight standing straight, arms slightly out, plain background',
      })
      await api.recordTestCharacter(asset.id, null)
      await api.startAutopilot(asset.id)
      for (let i = 0; i < 720; i++) {
        await new Promise((r) => setTimeout(r, 5000))
        const st = await api.getAutopilot(asset.id)
        if (st.status === 'done') {
          await api.recordTestCharacter(asset.id, true)
          setTestNote('The test character was made. Everything works.')
          onChanged?.()
          return
        }
        if (st.status === 'error' || st.status === 'paused') {
          await api.recordTestCharacter(asset.id, false)
          setTestNote(`The test character stopped${st.reason ? ': ' + st.reason : ''}. Open the asset in the project "Setup test" to see which step.`)
          return
        }
      }
      setTestNote('The test character is still running. Check the project "Setup test".')
    })

  const card = (s: ModelSet) => {
    const job = s.job
    const gate = s.gate ? data!.gates[s.gate] : null
    const phraseOk = !gate || (typed[s.gate] ?? '').trim() === gate.phrase
    const isOpen = open === s.id
    return (
      <li key={s.id} className="setup-problem">
        <div className="setup-problem-head">
          <span className={'dot ' + (s.complete ? 'ok' : 'off')} />
          <strong>{s.name}</strong>
          <span className="muted small">
            {s.complete ? 'all there' : `${size(s.remaining)} to get, ${size(s.total)} in all`}
            {s.covered > 0 ? ` (${s.covered} earlier cop${s.covered === 1 ? 'y stands' : 'ies stand'} in for the publisher's file${s.covered === 1 ? '' : 's'}; getting ${s.covered === 1 ? 'it is' : 'them is'} optional, ${size(s.covered_size)})` : ''}
            {s.required ? ' - needed' : ' - optional'}
          </span>
        </div>
        <div className="small">{s.what}</div>
        {!s.complete && s.need > 0 && (
          <div className={s.enough_space ? 'muted small' : 'error small'}>
            This needs {size(s.need)}{s.free != null ? `; there is ${size(s.free)} free` : ''}.
          </div>
        )}
        {gate && !s.complete && (
          <div className="setup-field">
            {gate.notice.map((n) => (
              <div key={n} className="small">
                {n}
              </div>
            ))}
            <label className="field-label">To accept, type exactly: {gate.phrase}</label>
            <input type="text" className="setup-path" value={typed[s.gate] ?? ''} onChange={(e) => setTyped({ ...typed, [s.gate]: e.target.value })} />
          </div>
        )}
        <div className="row-gap">
          {!s.complete && job?.state !== 'working' && (
            <button
              type="button"
              className="small-btn"
              disabled={busy || !s.enough_space || !phraseOk || !data?.models_folder}
              onClick={() => void run(async () => void (await api.startModelSet(s.id, typed)))}
            >
              {job?.state === 'stopped' || job?.state === 'failed' ? 'Try again' : 'Get this set'}
            </button>
          )}
          {job?.state === 'working' && (
            <>
              <progress value={job.fraction} max={1} />
              <span className="small">{job.text}</span>
              <button type="button" className="small-btn" onClick={() => void run(async () => void (await api.stopModelSet(s.id)))}>
                Stop
              </button>
            </>
          )}
          <button type="button" className="link small" onClick={() => setOpen(isOpen ? null : s.id)}>
            {isOpen ? 'Hide the files' : 'Show the files'}
          </button>
        </div>
        {s.items.some((i) => i.can_choose) && <ModelFolderChoice setId={s.id} onChanged={load} />}
        {job?.state === 'failed' && job.error && (
          <div className="error small">
            {job.error.message} {job.error.fix}
          </div>
        )}
        {job?.state === 'stopped' && <div className="muted small">{job.text}</div>}
        {isOpen && (
          <ul className="setup-rows">
            {s.items.map((i) => (
              <li key={i.id} className="setup-row">
                <span className={'dot ' + (i.state === 'present' ? 'ok' : i.state === 'differs' ? 'warn' : 'off')} />
                <span className="setup-row-label">{i.name}</span>
                <span className="muted small setup-row-detail">
                  {size(i.size)} - {STATE_WORD[i.state]}
                  {i.reused && !i.chosen && !i.renamed ? ` (used where it is: ${i.path})` : ''}
                  {i.chosen && i.state === 'present' && !i.yours ? ` - your file at ${i.path}; it is the same as the published one` : ''}
                  {i.source ? ` (${i.placed === 'link' ? 'linked from' : 'copied from'} ${i.source})` : ''}
                  {i.chosen && i.state === 'present' && i.yours ? ` - your own file at ${i.path}, not the one this app was tested with (you chose to use it)` : ''}
                  {i.chosen && i.state === 'checking' ? ` - your file at ${i.path}; checking it against the published one in the background` : ''}
                  {i.chosen && i.state === 'differs' ? ` - your own file at ${i.path}. This is not the file the app was tested with. Results may differ or the step may fail. It is not used until you press Use it anyway.` : ''}
                  {!i.chosen && i.state === 'differs' && i.check === 'size' ? ' - a file with this name is there but is not the published size' : ''}
                  {!i.chosen && i.state === 'differs' && i.check === 'differs' ? ' - a file with this name is there but differs from the published one' : ''}
                  {i.renamed ? ` - the publisher's file, which you have as ${i.path}` : ''}
                  {i.covered && i.state === 'checking' ? ` - checking whether ${i.older} is the publisher's file; the app uses it meanwhile` : ''}
                  {i.covered && i.state === 'missing' ? ` - the publisher's file is not here; the app is using an earlier copy at ${i.older} (getting the publisher's file is optional)` : ''}
                  {!i.publisher ? ' - community upload, check the page' : ''}
                </span>
                {i.state === 'differs' && (
                  <button type="button" className="link small" disabled={busy} onClick={() => void run(async () => void (await api.acceptModel(i.id)))}>
                    Use it anyway
                  </button>
                )}
                {i.covered && i.state === 'missing' && job?.state !== 'working' && (
                  <button
                    type="button"
                    className="link small"
                    disabled={busy || !data?.models_folder}
                    onClick={() => void run(async () => void (await api.startModelSet(s.id, typed, [i.id])))}
                  >
                    Get the publisher&apos;s file ({size(i.size)})
                  </button>
                )}
                {data?.copying?.[i.id]?.state === 'working' && (
                  <span className="small">
                    <progress value={data.copying[i.id].done} max={data.copying[i.id].total || 1} /> copying your file in
                  </span>
                )}
                {data?.copying?.[i.id]?.state === 'failed' && <span className="error small">The copy failed: {data.copying[i.id].error}</span>}
                {i.can_choose && <ModelFileChoice specId={i.id} name={i.name} chosen={!!i.chosen} onChanged={load} />}
              </li>
            ))}
          </ul>
        )}
      </li>
    )
  }

  return (
    <div className="card setup-card" id="tool-models">
      <h3 className="card-title">
        Models
        {onClose && (
          <button type="button" className="link small" onClick={onClose}>
            Close
          </button>
        )}
      </h3>
      {!data && !answer && <div className="muted">Looking at the model files...</div>}
      {data && !data.models_folder && <div className="warn">No ComfyUI is linked yet, so there is nowhere to put the models. Use Find or use my ComfyUI first.</div>}
      {data?.models_folder && (
        <div className="muted small">
          New models go in {data.models_folder}. Files with the right name and size in the folders your ComfyUI already reads are used where they are, never copied.
        </div>
      )}
      {data && (
        <ul className="setup-problems">{data.sets.map((s) => card(s))}</ul>
      )}
      {data?.models_folder && (
        <div className="row-gap">
          <button type="button" className="small-btn" disabled={busy || data.verify.state === 'working'} onClick={() => void run(async () => void (await api.verifyModels()))}>
            Check my files against the published ones
          </button>
          {data.verify.state === 'working' && (
            <span className="muted small">
              Checking {data.verify.current} ({data.verify.done} of {data.verify.total}), in the background.
            </span>
          )}
        </div>
      )}
      {starter?.complete && (
        <div className="setup-field">
          <div className="field-label">The test character</div>
          <div className="small">Makes one character from a short description through all six steps. This is the honest check that everything works together.</div>
          <div className="row-gap">
            <button type="button" className="small-btn" disabled={busy} onClick={() => void makeTest()}>
              Make one test character
            </button>
            {passed === true && <span className="setup-ok small">It worked.</span>}
            {passed === false && <span className="error small">The last try did not finish.</span>}
          </div>
          {testNote && <div className="muted small">{testNote}</div>}
        </div>
      )}
      {answer && <div className={answer.ok ? 'setup-ok small' : 'error small'}>{answer.text}</div>}
    </div>
  )
}
