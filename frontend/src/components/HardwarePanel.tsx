// The hardware plan on the setup card: what graphics cards and memory this computer has, which engines VGAC proposes and
// which step goes on which card, with the reasons. Nothing is saved until the person presses Use this plan; the person
// can leave a card out, give the 3D step to the other card, or change the memory to keep free first.
// Reads GET /api/hardware, saves with POST /api/hardware/accept (backend/vgac/hardware_setup.py).
import { useCallback, useEffect, useState } from 'react'
import { api, type HardwareChoice, type HardwarePlan } from '../api'

export default function HardwarePanel({ onChanged, onClose }: { onChanged?: () => void; onClose?: () => void }) {
  const [data, setData] = useState<HardwarePlan | null>(null)
  const [use, setUse] = useState<number[] | null>(null)
  const [threeD, setThreeD] = useState<number | null>(null)
  const [ram, setRam] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<{ ok: boolean; text: string } | null>(null)

  const choice = (u: number[] | null, t: number | null, r: string): HardwareChoice => {
    const c: HardwareChoice = {}
    if (u) c.use = u
    if (t != null) c.three_d = t
    const n = parseFloat(r)
    if (r.trim() !== '' && Number.isFinite(n)) c.min_free_ram_gb = n
    return c
  }

  const load = useCallback(async (u: number[] | null, t: number | null, r: string) => {
    try {
      setData(await api.hardware(choice(u, t, r)))
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    }
  }, [])
  useEffect(() => {
    void load(null, null, '')
  }, [load])

  const cards = data?.hardware.cards ?? []
  const plan = data?.plan
  const chosen = use ?? plan?.engines.map((e) => e.card) ?? []

  const change = (u: number[] | null, t: number | null, r: string) => {
    setUse(u)
    setThreeD(t)
    setRam(r)
    setAnswer(null)
    void load(u, t, r)
  }
  const toggle = (i: number) => {
    const now = chosen.includes(i) ? chosen.filter((x) => x !== i) : [...chosen, i]
    if (now.length > 0) change(now, threeD, ram)
  }
  const accept = async () => {
    setBusy(true)
    setAnswer(null)
    try {
      const r = await api.hardwareAccept(choice(use, threeD, ram))
      setAnswer({ ok: true, text: r.message + (r.note ? ' ' + r.note : '') })
      onChanged?.()
      await load(use, threeD, ram)
    } catch (e) {
      setAnswer({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card setup-card" id="tool-hardware">
      <h3 className="card-title">
        Your graphics cards and memory
        {onClose && (
          <button type="button" className="link small" onClick={onClose}>
            Close
          </button>
        )}
      </h3>
      {!data && !answer && <div className="muted">Looking at this computer...</div>}
      {data && (
        <>
          <div className="field-label">What was found</div>
          <ul className="setup-rows">
            {cards.map((c) => (
              <li key={c.index} className="setup-row">
                <span className={'dot ' + (plan?.engines.some((e) => e.card === c.index) ? 'ok' : 'off')} />
                <span className="setup-row-label">{c.short}</span>
                <span className="muted small setup-row-detail">{c.memory_gb} GB of graphics memory</span>
                {cards.length > 1 && (
                  <label className="small">
                    <input type="checkbox" checked={chosen.includes(c.index)} disabled={busy} onChange={() => toggle(c.index)} /> Use this card
                  </label>
                )}
              </li>
            ))}
            {cards.length === 0 && (
              <li className="setup-row">
                <span className="dot bad" />
                <span className="setup-row-label">No NVIDIA graphics card</span>
                <span className="muted small setup-row-detail">{data.hardware.other_cards.join(', ')}</span>
              </li>
            )}
            <li className="setup-row">
              <span className="dot ok" />
              <span className="setup-row-label">Memory</span>
              <span className="muted small setup-row-detail">
                {data.hardware.ram_total_gb} GB in the computer, {data.hardware.ram_free_gb} GB free now
              </span>
            </li>
          </ul>

          {plan && plan.engines.length > 0 && (
            <>
              <div className="field-label">What is proposed</div>
              <ul className="setup-rows">
                {plan.engines.map((e) => (
                  <li key={e.name} className="setup-row">
                    <span className="dot ok" />
                    <span className="setup-row-label">{e.gpu}</span>
                    <span className="muted small setup-row-detail">
                      runs {e.steps} ({e.memory_gb} GB)
                    </span>
                  </li>
                ))}
              </ul>
              {plan.engines.length === 2 && (
                <label className="small">
                  The 3D model is made on{' '}
                  <select
                    value={plan.engines[0].card}
                    disabled={busy}
                    onChange={(ev) => change(use, parseInt(ev.target.value, 10), ram)}
                  >
                    {plan.engines.map((e) => (
                      <option key={e.card} value={e.card}>
                        {e.gpu} ({e.memory_gb} GB)
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="small">
                <label>
                  Keep this much memory free before a job starts:{' '}
                  <input
                    type="number"
                    min={0}
                    step={1}
                    style={{ width: '4.5em' }}
                    value={ram === '' ? plan.min_free_ram_gb : ram}
                    disabled={busy}
                    onChange={(ev) => change(use, threeD, ev.target.value)}
                  />{' '}
                  GB
                </label>
              </div>
            </>
          )}

          {plan && plan.messages.map((m, i) => (
            <div key={i} className={m.level === 'info' ? 'small' : 'small ' + (m.level === 'bad' ? 'bad' : 'warn')}>
              {m.text}
            </div>
          ))}
          {plan && plan.explain.length > 0 && (
            <details>
              <summary>Why this plan?</summary>
              {plan.explain.map((t, i) => (
                <div key={i} className="small">
                  {t}
                </div>
              ))}
            </details>
          )}

          {data.problem && <div className="small warn">{data.problem}</div>}
          {data.can_accept ? (
            <div>
              {data.already ? (
                <div className="muted small">This is what is saved now.</div>
              ) : (
                <div className="muted small">Nothing changes until you press the button. A change is used after the app restarts.</div>
              )}
              <button type="button" className="small-btn" disabled={busy || data.already} onClick={() => void accept()}>
                Use this plan
              </button>
            </div>
          ) : (
            <div className="muted small">There is nothing to save on this computer.</div>
          )}
        </>
      )}
      {answer && <div className={'small ' + (answer.ok ? '' : 'bad')}>{answer.text}</div>}
    </div>
  )
}
