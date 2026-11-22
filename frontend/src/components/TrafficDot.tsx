export type TrafficState = 'ok' | 'warn' | 'bad' | 'none'

export function TrafficDot({ state, title }: { state: TrafficState; title?: string }) {
  return <span className={'traffic traffic-' + state} title={title} aria-label={title} role={title ? 'img' : undefined} />
}
