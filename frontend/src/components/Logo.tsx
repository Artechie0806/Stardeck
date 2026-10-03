import { useId, type CSSProperties } from 'react'

// The Stardeck mark ("2b, cut one colour"): four bars rising to a growth curve that ends in a data
// point. Geometry matches public/brand/stardeck-mark.svg; it draws in currentColor so it follows the theme.
const CURVE = 'M10 62C50 62 50 14 90 14'
const BARS = [11, 32, 53, 74]

/**
 * The mark alone. `animate` plays the 1.5s intro (3a): the line draws left to right, each bar grows
 * up to it as the line passes, and the end point pops in last. Reduced-motion users see it finished.
 */
export function LogoMark({ size = 28, animate = false, label = 'Stardeck' }: { size?: number; animate?: boolean; label?: string }) {
  const id = `sd-cut-${useId().replace(/[^a-zA-Z0-9]/g, '')}` // unique per instance: masks are document-global
  return (
    <svg className={`sd-mark${animate ? ' sd-animate' : ''}`} viewBox="0 0 100 100" width={size} height={size}
      fill="currentColor" {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}>
      <defs>
        <mask id={id} maskUnits="userSpaceOnUse" x="-10" y="-10" width="120" height="120">
          <path d="M-10 62H10C50 62 50 14 90 14H110V110H-10Z" fill="#fff" />
          <path d={CURVE} fill="none" stroke="#000" strokeWidth={20} strokeLinecap="round" />
          <circle cx="90" cy="14" r="15" fill="#000" />
        </mask>
      </defs>
      <g mask={`url(#${id})`}>
        {BARS.map((x, i) => (
          <rect key={x} className="sd-bar" x={x} y={0} width={15} height={90} rx={4}
            style={{ '--sd-delay': `${0.22 + i * 0.2}s` } as CSSProperties} />
        ))}
      </g>
      <path className="sd-line" pathLength={1} d={CURVE} fill="none" stroke="currentColor" strokeWidth={8} strokeLinecap="round" />
      <circle className="sd-dot" cx="90" cy="14" r="9" />
    </svg>
  )
}

/** Mark + lowercase "stardeck" wordmark. */
export function Logo({ size = 26, animate = false, wordmark = true }: { size?: number; animate?: boolean; wordmark?: boolean }) {
  return (
    <span className="logo" style={{ '--sd-size': `${size}px` } as CSSProperties}>
      <LogoMark size={size} animate={animate} label={wordmark ? '' : 'Stardeck'} />
      {wordmark && <span className="wordmark">stardeck</span>}
    </span>
  )
}
