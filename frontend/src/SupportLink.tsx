// A quiet link to the project's Ko-fi page. A plain link the person chooses to click: nothing is loaded from Ko-fi.
// target="_blank" is how every outside link here opens in the person's own browser: both launcher windows
// (desktop/main.js, starter/src/WebWindow.cs) hand a new-window request to the system browser.
export const SUPPORT_URL = 'https://ko-fi.com/guythreepwood646'

export function SupportLink({ className = 'support-link', text = 'Support VGAC' }: { className?: string; text?: string }) {
  return (
    <a className={className} href={SUPPORT_URL} target="_blank" rel="noopener noreferrer" title="Opens ko-fi.com in your browser">
      {text}
    </a>
  )
}
