import { useClerk } from '@clerk/react'
import type { CSSProperties } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

const NEON = ['#ff2079', '#00f0ff', '#ffe600', '#7cff00', '#ff6b00', '#b967ff']
const MAX_ESCAPES = 7

const FLEE_LABELS = [
  'SE CONNECTER',
  'TROP LENT !',
  'ESSAIE ENCORE',
  'PRESQUE...',
  'LOL NON',
  'TU CHAUFFES',
  'PLUS VITE !!',
  "OK J'AI PLUS DE SOUFFLE...",
]

interface Position {
  x: number
  y: number
}

interface Burst {
  id: number
  x: number
  y: number
  color: string
}

interface ConfettiPiece {
  id: number
  left: number
  color: string
  delay: number
  dur: number
  size: number
  spin: 1 | -1
}

// "Synthwave Chase" — the CRT/arcade landing page where the login button
// flees the cursor until you corner it. See landing-pages/index.ts to make
// this the active one.
export function SynthwaveChaseLanding() {
  const { openSignIn } = useClerk()
  const arenaRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<Position>({ x: 50, y: 62 })
  const [escapes, setEscapes] = useState(0)
  const [bursts, setBursts] = useState<Burst[]>([])
  const [confetti, setConfetti] = useState<ConfettiPiece[]>([])
  const [credits, setCredits] = useState(0)

  const tired = escapes >= MAX_ESCAPES

  const spawnBurst = useCallback((xPct: number, yPct: number) => {
    const id = Date.now() + Math.random()
    const color = NEON[Math.floor(Math.random() * NEON.length)]
    setBursts((b) => [...b.slice(-7), { id, x: xPct, y: yPct, color }])
    setTimeout(() => {
      setBursts((b) => b.filter((k) => k.id !== id))
    }, 950)
  }, [])

  // Ambient firecrackers popping around the page
  useEffect(() => {
    const t = setInterval(() => {
      spawnBurst(8 + Math.random() * 84, 8 + Math.random() * 74)
    }, 1600)
    return () => clearInterval(t)
  }, [spawnBurst])

  const flee = () => {
    if (tired) return
    const nx = 12 + Math.random() * 76
    const ny = 18 + Math.random() * 64
    setPos({ x: nx, y: ny })
    setEscapes((e) => e + 1)
    spawnBurst(pos.x, pos.y)
  }

  const catchIt = useCallback(() => {
    if (!tired) return
    const pieces: ConfettiPiece[] = Array.from({ length: 90 }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      color: NEON[i % NEON.length],
      delay: Math.random() * 0.8,
      dur: 2.2 + Math.random() * 2,
      size: 6 + Math.random() * 8,
      spin: Math.random() > 0.5 ? 1 : -1,
    }))
    setConfetti(pieces)
    void openSignIn()
  }, [tired, openSignIn])

  const label = FLEE_LABELS[Math.min(escapes, FLEE_LABELS.length - 1)]

  return (
    // The click only bumps the decorative arcade "CREDITS" counter — it drives
    // nothing, so there is no action a keyboard user is missing out on. The
    // arena itself wraps the real login button and can't become one.
    <div className="crt" role="none" ref={arenaRef} onClick={() => setCredits((c) => c + 1)}>
      <style>{css}</style>

      {/* Starfield */}
      <div className="stars s1" />
      <div className="stars s2" />

      {/* Synthwave floor */}
      <div className="floor" />

      {/* Marquee */}
      <div className="marquee">
        <div className="marquee-inner">
          {'☆ BIENVENUE SUR FINANCE QUEST ☆ CONNECTE-TOI POUR VOIR TES SOUS ☆ 0 BUGS, QUE DES FEATURES ☆ COMPATIBLE MINITEL ☆ TON PEL VA BIEN ☆ '.repeat(
            2,
          )}
        </div>
      </div>

      {/* Title */}
      <header className="hero">
        <div className="press-start blink">▼ INSÈRE UNE PIÈCE ▼</div>
        <h1 className="title">
          FINANCE<span className="title-alt">QUEST</span>
          <sup className="tm">™</sup>
        </h1>
        <p className="subtitle">LE DASHBOARD QUI RESPECTE PAS TON CURSEUR</p>
      </header>

      {/* Scoreboard */}
      <div className="scoreboard">
        <span>TENTATIVES&nbsp;:&nbsp;{String(escapes).padStart(2, '0')}</span>
        <span>CREDITS&nbsp;:&nbsp;{String(credits).padStart(3, '0')}</span>
        <span>HIGH&nbsp;SCORE&nbsp;:&nbsp;???</span>
      </div>

      {/* The fleeing login button */}
      <button
        type="button"
        className={`login ${tired ? 'tired' : ''}`}
        style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
        aria-label="Se connecter"
        onMouseEnter={flee}
        onTouchStart={(e) => {
          if (!tired) {
            e.preventDefault()
            flee()
          }
        }}
        onKeyDown={(e) => {
          // Keyboard / assistive-tech users can't chase a fleeing button, so
          // Enter or Space signs them in directly rather than playing the
          // pointer-only catch game — otherwise login is genuinely unreachable.
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            void openSignIn()
          }
        }}
        onClick={(e) => {
          e.stopPropagation()
          catchIt()
        }}
      >
        {label}
      </button>

      {/* Firecracker bursts */}
      {bursts.map((b) => (
        <div key={b.id} className="burst" style={{ left: `${b.x}%`, top: `${b.y}%` }}>
          {Array.from({ length: 10 }, (_, i) => {
            const a = (i / 10) * Math.PI * 2
            const d = 38 + (i % 3) * 16
            return (
              <span
                key={i}
                className="spark"
                style={
                  {
                    background: b.color,
                    '--dx': `${Math.cos(a) * d}px`,
                    '--dy': `${Math.sin(a) * d}px`,
                  } as CSSProperties
                }
              />
            )
          })}
        </div>
      ))}

      {/* Confetti */}
      {confetti.map((p) => (
        <span
          key={p.id}
          className="confetti"
          style={
            {
              left: `${p.left}%`,
              background: p.color,
              width: p.size,
              height: p.size * 0.6,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.dur}s`,
              '--spin': p.spin,
            } as CSSProperties
          }
        />
      ))}

      {/* Retro footer */}
      <footer className="footer">
        <div className="construction">⚠ EN CONSTRUCTION DEPUIS 1997 ⚠</div>
        <div className="counter">
          VISITEURS&nbsp;:
          {'001337'.split('').map((d, i) => (
            <span key={i} className="digit">
              {d}
            </span>
          ))}
        </div>
        <div className="badge">Optimisé pour Netscape Navigator 4.0 — 800×600</div>
      </footer>

      {/* CRT overlay */}
      <div className="scanlines" />
      <div className="vignette" />
    </div>
  )
}

const css = `
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

.crt {
  position: relative;
  width: 100%;
  min-height: 100vh;
  overflow: hidden;
  background: radial-gradient(ellipse at 50% 30%, #1a0b3d 0%, #0a0020 65%, #05000f 100%);
  font-family: 'Press Start 2P', 'Courier New', monospace;
  color: #fff;
  cursor: crosshair;
  user-select: none;
}

/* ---------- Starfield ---------- */
.stars { position: absolute; inset: 0; pointer-events: none; }
.stars.s1 {
  background-image:
    radial-gradient(1.5px 1.5px at 20% 30%, #fff, transparent),
    radial-gradient(1.5px 1.5px at 70% 15%, #00f0ff, transparent),
    radial-gradient(2px 2px at 40% 70%, #ffe600, transparent),
    radial-gradient(1.5px 1.5px at 85% 55%, #ff2079, transparent),
    radial-gradient(1px 1px at 55% 40%, #fff, transparent),
    radial-gradient(1px 1px at 10% 80%, #7cff00, transparent);
  background-size: 100% 100%;
  animation: twinkle 3s steps(2) infinite;
}
.stars.s2 {
  background-image:
    radial-gradient(1px 1px at 30% 55%, #fff, transparent),
    radial-gradient(1.5px 1.5px at 60% 80%, #b967ff, transparent),
    radial-gradient(1px 1px at 90% 25%, #fff, transparent),
    radial-gradient(2px 2px at 15% 10%, #00f0ff, transparent);
  animation: twinkle 2.2s steps(2) infinite reverse;
}
@keyframes twinkle { 50% { opacity: 0.35; } }

/* ---------- Synthwave floor ---------- */
.floor {
  position: absolute;
  left: -50%; right: -50%; bottom: -12%;
  height: 42%;
  background:
    linear-gradient(transparent 0%, rgba(255,32,121,0.25) 100%),
    repeating-linear-gradient(90deg, rgba(0,240,255,0.5) 0 2px, transparent 2px 70px),
    repeating-linear-gradient(0deg, rgba(255,32,121,0.55) 0 2px, transparent 2px 44px);
  transform: perspective(320px) rotateX(62deg);
  transform-origin: top;
  animation: floorScroll 2.4s linear infinite;
  pointer-events: none;
}
@keyframes floorScroll { to { background-position: 0 0, 0 0, 0 44px; } }

/* ---------- Marquee ---------- */
.marquee {
  position: relative;
  overflow: hidden;
  background: #ff2079;
  color: #0a0020;
  font-size: 10px;
  padding: 8px 0;
  border-bottom: 3px solid #ffe600;
  white-space: nowrap;
}
.marquee-inner { display: inline-block; animation: scroll 22s linear infinite; }
@keyframes scroll { to { transform: translateX(-50%); } }

/* ---------- Hero ---------- */
.hero { position: relative; text-align: center; margin-top: 7vh; z-index: 2; pointer-events: none; }
.press-start { font-size: 11px; color: #ffe600; letter-spacing: 2px; margin-bottom: 22px; }
.blink { animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }

.title {
  font-size: clamp(26px, 7vw, 64px);
  color: #00f0ff;
  letter-spacing: 4px;
  text-shadow:
    3px 3px 0 #ff2079,
    6px 6px 0 #7c00ff,
    0 0 24px rgba(0,240,255,0.8);
  animation: hueSpin 6s linear infinite;
}
.title-alt { color: #ffe600; }
.tm { font-size: 0.3em; color: #ff2079; vertical-align: super; }
@keyframes hueSpin { to { filter: hue-rotate(360deg); } }

.subtitle {
  margin-top: 18px;
  font-size: clamp(8px, 1.6vw, 12px);
  color: #b967ff;
  letter-spacing: 1px;
}

/* ---------- Scoreboard ---------- */
.scoreboard {
  position: relative;
  z-index: 2;
  display: flex;
  justify-content: center;
  gap: 28px;
  flex-wrap: wrap;
  margin-top: 26px;
  font-size: 9px;
  color: #7cff00;
  text-shadow: 0 0 8px rgba(124,255,0,0.7);
  pointer-events: none;
}

/* ---------- Fleeing button ---------- */
.login {
  position: absolute;
  transform: translate(-50%, -50%);
  z-index: 5;
  font-family: inherit;
  font-size: 12px;
  padding: 16px 22px;
  color: #0a0020;
  background: #ffe600;
  border: 4px solid #fff;
  box-shadow: 6px 6px 0 #ff2079, 0 0 30px rgba(255,230,0,0.5);
  cursor: pointer;
  transition: left 0.18s ease-out, top 0.18s ease-out;
  image-rendering: pixelated;
}
.login:active { box-shadow: 2px 2px 0 #ff2079; }
.login.tired {
  background: #7cff00;
  animation: wobble 0.6s ease-in-out infinite;
}
@keyframes wobble {
  0%,100% { transform: translate(-50%,-50%) rotate(-2deg); }
  50% { transform: translate(-50%,-50%) rotate(2deg) scale(1.04); }
}

/* ---------- Firecrackers ---------- */
.burst { position: absolute; z-index: 3; pointer-events: none; }
.spark {
  position: absolute;
  width: 6px; height: 6px;
  border-radius: 1px;
  animation: spark 0.9s ease-out forwards;
  box-shadow: 0 0 8px currentColor;
}
@keyframes spark {
  from { transform: translate(0,0) scale(1); opacity: 1; }
  to { transform: translate(var(--dx), var(--dy)) scale(0.1); opacity: 0; }
}

/* ---------- Confetti ---------- */
.confetti {
  position: absolute;
  top: -20px;
  z-index: 6;
  animation-name: fall;
  animation-timing-function: linear;
  animation-fill-mode: forwards;
  pointer-events: none;
}
@keyframes fall {
  to {
    transform: translateY(110vh) rotate(calc(720deg * var(--spin)));
    opacity: 0.8;
  }
}

/* ---------- Footer ---------- */
.footer {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  z-index: 4;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 16px;
  font-size: 8px;
}
.construction {
  background: repeating-linear-gradient(45deg, #ffe600 0 12px, #0a0020 12px 24px);
  color: #0a0020;
  padding: 6px 10px;
  font-weight: bold;
  text-shadow: 0 0 4px #ffe600;
  color: #fff;
}
.counter { color: #00f0ff; display: flex; align-items: center; gap: 3px; }
.digit {
  background: #000;
  border: 1px solid #00f0ff;
  padding: 3px 4px;
  color: #7cff00;
}
.badge { color: #b967ff; }

/* ---------- CRT effects ---------- */
.scanlines {
  position: absolute; inset: 0;
  z-index: 9;
  pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,0.22) 0 2px, transparent 2px 4px);
  mix-blend-mode: multiply;
  animation: flicker 0.12s steps(2) infinite;
}
@keyframes flicker { 50% { opacity: 0.92; } }
.vignette {
  position: absolute; inset: 0;
  z-index: 9;
  pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%);
}

/* ---------- Accessibility ---------- */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
`
