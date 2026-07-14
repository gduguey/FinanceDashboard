import { useClerk } from '@clerk/react'
import type { CSSProperties } from 'react'
import { useCallback, useEffect, useState } from 'react'

const NEON = ['#ff2079', '#00f0ff', '#ffe600', '#7cff00', '#ff6b00', '#b967ff']

interface LoginButtonSpec {
  id: string
  label: string
  cls: string
  note: string
}

// Login buttons wander on their own paths — they never flee the cursor.
const LOGIN_BUTTONS: LoginButtonSpec[] = [
  { id: 'peak', label: 'LOG IN', cls: 'drift-peak', note: '▲' },
  { id: 'ledge', label: 'OR HERE', cls: 'drift-ledge', note: '◆' },
  { id: 'valley', label: 'MAYBE HERE?', cls: 'drift-valley', note: '✦' },
  { id: 'sea', label: 'VIP ENTRANCE', cls: 'drift-sea', note: '~' },
]

interface GoatSpec {
  id: number
  top: number
  dur: number
  delay: number
  dir: 1 | -1
  scale: number
}

// Mountain goats hopping along the ledges
const GOATS: GoatSpec[] = [
  { id: 1, top: 46, dur: 30, delay: 0, dir: 1, scale: 0.9 },
  { id: 2, top: 57, dur: 24, delay: -10, dir: -1, scale: 1.1 },
  { id: 3, top: 66, dur: 38, delay: -20, dir: 1, scale: 0.75 },
]

interface BirdSpec {
  id: number
  top: number
  dur: number
  delay: number
}

const BIRDS: BirdSpec[] = [
  { id: 1, top: 8, dur: 20, delay: 0 },
  { id: 2, top: 14, dur: 28, delay: -9 },
  { id: 3, top: 5, dur: 34, delay: -18 },
]

interface ConfettiPiece {
  id: number
  left: number
  color: string
  delay: number
  dur: number
  size: number
  spin: 1 | -1
}

interface Coin {
  id: number
  drift: number
  dur: number
  delay: number
}

// "Summit Edition" — the mountain / piggy-shrine themed landing page.
// See landing-pages/index.ts to make this the active one.
export function SummitPiggyBankLanding() {
  const { openSignIn } = useClerk()
  const [caught, setCaught] = useState(false)
  const [confetti, setConfetti] = useState<ConfettiPiece[]>([])
  const [credits, setCredits] = useState(0)
  const [coins, setCoins] = useState<Coin[]>([])

  // The Piggy Shrine erupts golden coins on loop
  useEffect(() => {
    const t = setInterval(() => {
      const id = Date.now() + Math.random()
      setCoins((c) => [
        ...c.slice(-8),
        {
          id,
          drift: (Math.random() - 0.5) * 90,
          dur: 1.8 + Math.random() * 1.4,
          delay: Math.random() * 0.3,
        },
      ])
    }, 900)
    return () => clearInterval(t)
  }, [])

  const login = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      e.stopPropagation()
      setCaught(true)
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
    },
    [openSignIn],
  )

  return (
    <div className="scene" onClick={() => setCredits((c) => c + 1)}>
      <style>{css}</style>

      {/* ---- Sky ---- */}
      <div className="sky" />
      <div className="sun">
        <div className="sun-core" />
        <div className="sun-rays" />
      </div>

      <div className="cloud c1" />
      <div className="cloud c2" />
      <div className="cloud c3" />

      {BIRDS.map((b) => (
        <div
          key={b.id}
          className="bird"
          style={{
            top: `${b.top}%`,
            animationDuration: `${b.dur}s`,
            animationDelay: `${b.delay}s`,
          }}
        >
          <span className="wing wl" />
          <span className="wing wr" />
        </div>
      ))}

      {/* ---- Depth stack: 4 mountain layers + haze between each ---- */}
      <div className="range r4" />
      <div className="haze h4" />
      <div className="range r3" />
      <div className="haze h3" />
      <div className="range r2" />
      <div className="haze h2" />

      {/* ---- The sea, far below at the foot of the mountains ---- */}
      <div className="sea">
        <div className="glitter" />
        <div className="sailboat">
          <span className="sail" />
          <span className="hull" />
        </div>
      </div>

      {/* ---- Nearest ridge, framing the valley down to the sea ---- */}
      <div className="range r1" />

      {/* ---- Foreground cliffs: we are standing IN the mountains ---- */}
      <div className="cliff cliff-left" />
      <div className="cliff cliff-right" />

      {/* ---- Waterfall tumbling from the near ridge toward the sea ---- */}
      <div className="waterfall">
        <div className="fall-stream" />
        <div className="fall-mist" />
      </div>

      {/* ---- THE PIGGY SHRINE ---- */}
      <div className="shrine">
        <div className="shrine-rays" />
        <div className="piggy">
          <span className="pig-ear" />
          <span className="pig-eye" />
          <span className="pig-snout" />
          <span className="pig-slot" />
          <span className="pig-leg p1" />
          <span className="pig-leg p2" />
        </div>
        <div className="shrine-base">
          <span className="shrine-text blink">INSERT COIN</span>
        </div>
        {coins.map((c) => (
          <span
            key={c.id}
            className="coin"
            style={
              {
                '--drift': `${c.drift}px`,
                animationDuration: `${c.dur}s`,
                animationDelay: `${c.delay}s`,
              } as CSSProperties
            }
          >
            $
          </span>
        ))}
      </div>

      {/* ---- Goats on the ledges ---- */}
      {GOATS.map((g) => (
        <div
          key={g.id}
          className={`goat ${g.dir === -1 ? 'reverse' : ''}`}
          style={
            {
              top: `${g.top}%`,
              animationDuration: `${g.dur}s`,
              animationDelay: `${g.delay}s`,
              '--scale': g.scale,
            } as CSSProperties
          }
        >
          <span className="goat-body" />
          <span className="goat-head" />
          <span className="goat-horn" />
          <span className="goat-leg g1" />
          <span className="goat-leg g2" />
        </div>
      ))}

      {/* ---- Marquee ---- */}
      <div className="marquee">
        <div className="marquee-inner">
          {'☆ WELCOME TO FINANCE QUEST — SUMMIT EDITION ☆ YOUR MONEY LIVES 2400M ABOVE SEA LEVEL ☆ 4 LOGIN BUTTONS, 0 EXCUSES ☆ THE PIGGY SHRINE DEMANDS TRIBUTE ☆ FORECAST: PIXELS WITH A CHANCE OF COINS ☆ '.repeat(
            2,
          )}
        </div>
      </div>

      {/* ---- Title ---- */}
      <header className="hero">
        <div className="press-start blink">▼ PICK A BUTTON, ANY BUTTON ▼</div>
        <h1 className="title">
          FINANCE<span className="title-alt">QUEST</span>
          <sup className="tm">™</sup>
        </h1>
        <p className="subtitle">SUMMIT EDITION — THE BUTTONS WANDER, BUT THEY DON'T RUN</p>
      </header>

      {/* ---- Scoreboard ---- */}
      <div className="scoreboard">
        <span>ALTITUDE&nbsp;:&nbsp;2400M</span>
        <span>CREDITS&nbsp;:&nbsp;{String(credits).padStart(3, '0')}</span>
        <span>GOATS&nbsp;:&nbsp;{String(GOATS.length).padStart(2, '0')}</span>
      </div>

      {/* ---- Drifting login buttons ---- */}
      {!caught &&
        LOGIN_BUTTONS.map((b) => (
          <button key={b.id} className={`login ${b.cls}`} onClick={login}>
            <span className="login-note">{b.note}</span>
            {b.label}
          </button>
        ))}

      {caught && (
        <div className="welcome">
          ★ ACCESS GRANTED ★<br />
          WELCOME, PLAYER 1<br />
          <span className="welcome-sub">the piggy shrine is counting your coins... 99%</span>
        </div>
      )}

      {/* ---- Confetti ---- */}
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

      {/* ---- Footer ---- */}
      <footer className="footer">
        <div className="construction">⚠ SUMMIT UNDER CONSTRUCTION SINCE 1997 ⚠</div>
        <div className="counter">
          VISITORS&nbsp;:
          {'001337'.split('').map((d, i) => (
            <span key={i} className="digit">
              {d}
            </span>
          ))}
        </div>
        <div className="badge">Best viewed in Netscape Navigator 4.0 — 800×600</div>
      </footer>

      {/* ---- CRT overlay ---- */}
      <div className="scanlines" />
      <div className="vignette" />
    </div>
  )
}

const css = `
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

.scene {
  position: relative;
  width: 100%;
  min-height: 100vh;
  overflow: hidden;
  font-family: 'Press Start 2P', 'Courier New', monospace;
  color: #fff;
  cursor: crosshair;
  user-select: none;
  background: #ffb37e;
  image-rendering: pixelated;
}

/* ================= SKY ================= */
.sky {
  position: absolute; inset: 0;
  background: linear-gradient(
    #1a2a5e 0%,
    #4a5fc4 16%,
    #9a7fd4 30%,
    #ff9e7e 44%,
    #ffd9a0 54%
  );
}

.sun {
  position: absolute;
  left: 44%; top: 20%;
  width: 110px; height: 110px;
  z-index: 1;
}
.sun-core {
  position: absolute; inset: 18px;
  background: #ffe600;
  box-shadow:
    0 0 0 7px #ff6b00,
    0 0 60px rgba(255,180,0,0.9);
  border-radius: 4px;
}
.sun-rays {
  position: absolute; inset: -14px;
  background:
    conic-gradient(#ffe60077 0 10deg, transparent 10deg 45deg,
      #ffe60077 45deg 55deg, transparent 55deg 90deg,
      #ffe60077 90deg 100deg, transparent 100deg 135deg,
      #ffe60077 135deg 145deg, transparent 145deg 180deg,
      #ffe60077 180deg 190deg, transparent 190deg 225deg,
      #ffe60077 225deg 235deg, transparent 235deg 270deg,
      #ffe60077 270deg 280deg, transparent 280deg 315deg,
      #ffe60077 315deg 325deg, transparent 325deg 360deg);
  animation: sunSpin 26s linear infinite;
}
@keyframes sunSpin { to { transform: rotate(360deg); } }

.cloud {
  position: absolute;
  z-index: 2;
  width: 110px; height: 28px;
  background: #ffe8f0;
  box-shadow:
    -22px 10px 0 0 #ffe8f0,
    26px 10px 0 0 #ffe8f0,
    0 -13px 0 -6px #ffe8f0;
  border-radius: 6px;
  opacity: 0.9;
  animation: cloudDrift linear infinite;
}
.cloud.c1 { top: 6%;  animation-duration: 60s; }
.cloud.c2 { top: 14%; animation-duration: 85s; animation-delay: -35s; transform: scale(0.65); }
.cloud.c3 { top: 24%; animation-duration: 110s; animation-delay: -70s; transform: scale(1.3); opacity: 0.55; }
@keyframes cloudDrift {
  from { left: -180px; }
  to   { left: 110%; }
}

.bird {
  position: absolute;
  z-index: 2;
  width: 22px; height: 10px;
  animation-name: birdFly;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  pointer-events: none;
}
.wing {
  position: absolute;
  width: 11px; height: 4px;
  background: #1a1a2e;
  top: 3px;
}
.wing.wl { left: 0; transform-origin: right center; animation: flapL 0.5s steps(2) infinite; }
.wing.wr { right: 0; transform-origin: left center; animation: flapR 0.5s steps(2) infinite; }
@keyframes flapL { 50% { transform: rotate(-35deg); } }
@keyframes flapR { 50% { transform: rotate(35deg); } }
@keyframes birdFly {
  from { left: -40px; }
  to   { left: 105%; }
}

/* ================= MOUNTAIN DEPTH STACK ================= */
.range {
  position: absolute;
  left: -3%; right: -3%;
  pointer-events: none;
}
.range.r4 {
  top: 28%; height: 26%;
  background: #c9a0d8;
  clip-path: polygon(
    0% 100%, 0% 60%, 7% 35%, 15% 62%, 24% 22%, 33% 58%,
    43% 38%, 52% 68%, 62% 25%, 72% 60%, 82% 38%, 91% 66%, 100% 45%, 100% 100%
  );
  z-index: 1;
  opacity: 0.8;
}
.range.r3 {
  top: 33%; height: 30%;
  background: #9a6fc0;
  clip-path: polygon(
    0% 100%, 0% 55%, 9% 25%, 19% 60%, 30% 12%, 40% 55%,
    50% 30%, 61% 65%, 71% 18%, 81% 58%, 92% 35%, 100% 62%, 100% 100%
  );
  z-index: 3;
}
.range.r3::after {
  content: "";
  position: absolute; inset: 0;
  background: linear-gradient(#fff 0 7%, transparent 7%);
  clip-path: inherit;
  opacity: 0.6;
}
.range.r2 {
  top: 40%; height: 34%;
  background: #6a4a9e;
  clip-path: polygon(
    0% 100%, 0% 65%, 8% 30%, 17% 68%, 27% 15%, 38% 62%,
    47% 40%, 57% 75%, 68% 22%, 79% 65%, 89% 42%, 100% 70%, 100% 100%
  );
  z-index: 5;
}
.range.r2::after {
  content: "";
  position: absolute; inset: 0;
  background: linear-gradient(#ffe8f0 0 6%, transparent 6%);
  clip-path: inherit;
  opacity: 0.7;
}
.range.r1 {
  top: 52%; height: 48%;
  background: linear-gradient(#3d2a5e, #241a3e 70%);
  clip-path: polygon(
    0% 100%, 0% 30%, 10% 12%, 20% 38%, 30% 22%, 38% 55%,
    45% 85%, 55% 85%, 62% 52%, 71% 20%, 80% 42%, 90% 10%, 100% 32%, 100% 100%
  );
  z-index: 7;
}

.haze {
  position: absolute;
  left: 0; right: 0;
  pointer-events: none;
  background: linear-gradient(rgba(255,217,160,0), rgba(255,217,160,0.55), rgba(255,217,160,0));
}
.haze.h4 { top: 46%; height: 9%;  z-index: 2; }
.haze.h3 { top: 54%; height: 9%;  z-index: 4; opacity: 0.8; }
.haze.h2 { top: 63%; height: 10%; z-index: 6; opacity: 0.7; }

/* ================= THE SEA, AT THE FOOT ================= */
.sea {
  position: absolute;
  left: 0; right: 0;
  top: 76%;
  bottom: 0;
  background: linear-gradient(#ff9e7e 0%, #d16a9e 12%, #3f6fd5 30%, #1b3a9e 100%);
  z-index: 6;
  overflow: hidden;
}
.glitter {
  position: absolute;
  left: 38%; right: 34%;
  top: 0; bottom: 0;
  background:
    repeating-linear-gradient(0deg,
      rgba(255,230,0,0.55) 0 3px, transparent 3px 9px);
  filter: blur(0.5px);
  animation: glitterShift 2.5s steps(4) infinite;
  opacity: 0.8;
}
@keyframes glitterShift { 50% { transform: translateX(8px); opacity: 0.5; } }

.sailboat {
  position: absolute;
  left: 46%; top: 42%;
  animation: sail 40s linear infinite alternate;
}
.sail {
  display: block;
  width: 0; height: 0;
  border-left: 10px solid #fff;
  border-top: 16px solid transparent;
  margin-left: 4px;
}
.hull {
  display: block;
  width: 26px; height: 7px;
  background: #a8352a;
  clip-path: polygon(0 0, 100% 0, 82% 100%, 18% 100%);
}
@keyframes sail { to { transform: translateX(90px); } }

/* ================= FOREGROUND CLIFFS ================= */
.cliff {
  position: absolute;
  bottom: -2%;
  width: 30%;
  height: 62%;
  background: linear-gradient(#1a1028, #0d0818);
  z-index: 8;
  pointer-events: none;
}
.cliff-left {
  left: -4%;
  clip-path: polygon(0 0, 34% 0, 55% 18%, 42% 32%, 68% 48%, 50% 62%, 78% 80%, 60% 100%, 0 100%);
}
.cliff-right {
  right: -4%;
  clip-path: polygon(66% 0, 100% 0, 100% 100%, 40% 100%, 55% 78%, 30% 60%, 52% 44%, 34% 28%, 58% 14%);
}
.cliff::after {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0; height: 8px;
  background: repeating-linear-gradient(90deg, #3f9e35 0 8px, transparent 8px 14px);
  opacity: 0.9;
}

/* ================= WATERFALL ================= */
.waterfall {
  position: absolute;
  left: 47.5%;
  top: 62%;
  width: 5%;
  height: 15%;
  z-index: 7;
  pointer-events: none;
}
.fall-stream {
  position: absolute; inset: 0;
  background:
    repeating-linear-gradient(0deg,
      rgba(180,230,255,0.95) 0 8px, rgba(80,160,255,0.85) 8px 16px);
  background-size: 100% 32px;
  animation: fallFlow 0.7s linear infinite;
  border-left: 2px solid rgba(255,255,255,0.6);
  border-right: 2px solid rgba(255,255,255,0.6);
}
@keyframes fallFlow { to { background-position: 0 32px; } }
.fall-mist {
  position: absolute;
  left: -40%; right: -40%; bottom: -6px;
  height: 14px;
  background: rgba(255,255,255,0.75);
  border-radius: 50%;
  animation: mistPuff 1.6s ease-in-out infinite;
}
@keyframes mistPuff { 50% { transform: scaleX(1.25); opacity: 0.5; } }

/* ================= THE PIGGY SHRINE ================= */
.shrine {
  position: absolute;
  right: 13%;
  top: 41%;
  z-index: 9;
  width: 120px;
  pointer-events: none;
}
.shrine-rays {
  position: absolute;
  left: 50%; top: 26px;
  width: 150px; height: 150px;
  transform: translate(-50%, -50%);
  background:
    conic-gradient(rgba(255,230,0,0.5) 0 8deg, transparent 8deg 45deg,
      rgba(255,230,0,0.5) 45deg 53deg, transparent 53deg 90deg,
      rgba(255,230,0,0.5) 90deg 98deg, transparent 98deg 135deg,
      rgba(255,230,0,0.5) 135deg 143deg, transparent 143deg 180deg,
      rgba(255,230,0,0.5) 180deg 188deg, transparent 188deg 225deg,
      rgba(255,230,0,0.5) 225deg 233deg, transparent 233deg 270deg,
      rgba(255,230,0,0.5) 270deg 278deg, transparent 278deg 315deg,
      rgba(255,230,0,0.5) 315deg 323deg, transparent 323deg 360deg);
  animation: sunSpin 14s linear infinite reverse;
  border-radius: 50%;
}
.piggy {
  position: relative;
  width: 74px; height: 46px;
  margin: 0 auto;
  background: #ffd700;
  border: 4px solid #b8860b;
  border-radius: 40% 45% 45% 40%;
  box-shadow: 0 0 26px rgba(255,215,0,0.9), inset -6px -6px 0 rgba(184,134,11,0.5);
  animation: piggyBounce 1.4s ease-in-out infinite;
}
@keyframes piggyBounce {
  0%, 100% { transform: translateY(0) rotate(-2deg); }
  50% { transform: translateY(-6px) rotate(2deg); }
}
.pig-ear {
  position: absolute;
  top: -10px; right: 12px;
  width: 0; height: 0;
  border-left: 8px solid transparent;
  border-right: 8px solid transparent;
  border-bottom: 12px solid #ffd700;
  filter: drop-shadow(0 -2px 0 #b8860b);
}
.pig-eye {
  position: absolute;
  top: 12px; right: 16px;
  width: 7px; height: 7px;
  background: #1a1a2e;
  border-radius: 2px;
  animation: pigBlink 3.2s steps(1) infinite;
}
@keyframes pigBlink { 92% { transform: scaleY(1); } 96% { transform: scaleY(0.15); } }
.pig-snout {
  position: absolute;
  top: 16px; right: -12px;
  width: 16px; height: 14px;
  background: #ffbf00;
  border: 3px solid #b8860b;
  border-radius: 4px;
}
.pig-slot {
  position: absolute;
  top: -3px; left: 26px;
  width: 20px; height: 5px;
  background: #1a1a2e;
  border-radius: 2px;
}
.pig-leg {
  position: absolute;
  bottom: -10px;
  width: 9px; height: 12px;
  background: #ffd700;
  border: 3px solid #b8860b;
  border-top: none;
}
.pig-leg.p1 { left: 12px; }
.pig-leg.p2 { right: 12px; }

.shrine-base {
  margin-top: 16px;
  text-align: center;
  background: #1a1a2e;
  border: 3px solid #ffe600;
  box-shadow: 0 0 16px rgba(255,230,0,0.5), 4px 4px 0 #b8860b;
  padding: 7px 4px;
}
.shrine-text {
  font-size: 8px;
  color: #ffe600;
  letter-spacing: 1px;
}
.coin {
  position: absolute;
  top: -8px; left: 50%;
  font-size: 13px;
  color: #ffe600;
  text-shadow: 0 0 8px #ffb700, 1px 1px 0 #7a5500;
  animation: coinErupt ease-out forwards;
}
@keyframes coinErupt {
  0%  { transform: translate(0, 0) scale(1) rotate(0deg); opacity: 1; }
  40% { transform: translate(calc(var(--drift) * 0.6), -55px) scale(1.15) rotate(180deg); opacity: 1; }
  100%{ transform: translate(var(--drift), 30px) scale(0.6) rotate(360deg); opacity: 0; }
}

/* ================= GOATS ================= */
.goat {
  position: absolute;
  z-index: 8;
  width: 42px; height: 28px;
  animation-name: goatWalk;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  transform: scale(var(--scale));
  pointer-events: none;
}
.goat.reverse { animation-name: goatWalkReverse; }
.goat-body {
  position: absolute;
  left: 4px; top: 6px;
  width: 30px; height: 14px;
  background: #f0ead6;
  border-radius: 5px;
  box-shadow: inset -4px -3px 0 rgba(160,140,110,0.5);
  animation: bob 0.7s steps(2) infinite;
}
.goat-head {
  position: absolute;
  right: 0; top: 0;
  width: 10px; height: 12px;
  background: #f0ead6;
  border-radius: 3px;
  box-shadow: inset -2px -2px 0 rgba(160,140,110,0.5);
  animation: bob 0.7s steps(2) infinite;
}
.goat-horn {
  position: absolute;
  right: 2px; top: -6px;
  width: 3px; height: 8px;
  background: #8a6a3a;
  border-radius: 2px;
  transform: rotate(-24deg);
  box-shadow: -5px 0 0 #8a6a3a;
}
.goat.reverse .goat-head { right: auto; left: 0; }
.goat.reverse .goat-horn { right: auto; left: 2px; transform: rotate(24deg); }
.goat-leg {
  position: absolute;
  bottom: 0;
  width: 4px; height: 9px;
  background: #cabb9a;
  animation: trot 0.45s steps(2) infinite;
}
.goat-leg.g1 { left: 10px; }
.goat-leg.g2 { left: 26px; animation-delay: 0.22s; }
@keyframes bob { 50% { transform: translateY(2px); } }
@keyframes trot { 50% { transform: translateY(-3px); } }
@keyframes goatWalk {
  0%   { left: -60px; }
  10%  { transform: scale(var(--scale)) translateY(0); }
  12%  { transform: scale(var(--scale)) translateY(-14px); }
  14%  { transform: scale(var(--scale)) translateY(0); }
  46%  { transform: scale(var(--scale)) translateY(0); }
  48%  { transform: scale(var(--scale)) translateY(-14px); }
  50%  { transform: scale(var(--scale)) translateY(0); }
  82%  { transform: scale(var(--scale)) translateY(0); }
  84%  { transform: scale(var(--scale)) translateY(-14px); }
  86%  { transform: scale(var(--scale)) translateY(0); }
  100% { left: 105%; }
}
@keyframes goatWalkReverse {
  0%   { left: 105%; transform: scale(var(--scale)) scaleX(-1) translateY(0); }
  30%  { transform: scale(var(--scale)) scaleX(-1) translateY(-14px); }
  32%  { transform: scale(var(--scale)) scaleX(-1) translateY(0); }
  64%  { transform: scale(var(--scale)) scaleX(-1) translateY(-14px); }
  66%  { transform: scale(var(--scale)) scaleX(-1) translateY(0); }
  100% { left: -60px; transform: scale(var(--scale)) scaleX(-1) translateY(0); }
}

/* ================= MARQUEE ================= */
.marquee {
  position: relative;
  z-index: 12;
  overflow: hidden;
  background: #ff2079;
  color: #0a0020;
  font-size: 10px;
  padding: 8px 0;
  border-bottom: 3px solid #ffe600;
  white-space: nowrap;
}
.marquee-inner { display: inline-block; animation: scroll 28s linear infinite; }
@keyframes scroll { to { transform: translateX(-50%); } }

/* ================= HERO ================= */
.hero {
  position: relative;
  text-align: center;
  margin-top: 3vh;
  z-index: 12;
  pointer-events: none;
}
.press-start { font-size: 10px; color: #fff; letter-spacing: 2px; margin-bottom: 16px; text-shadow: 2px 2px 0 #1a2a5e; }
.blink { animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }

.title {
  font-size: clamp(24px, 6vw, 56px);
  color: #ffe600;
  letter-spacing: 4px;
  text-shadow:
    3px 3px 0 #ff2079,
    6px 6px 0 #1a2a5e,
    0 0 24px rgba(255,230,0,0.6);
}
.title-alt { color: #00f0ff; }
.tm { font-size: 0.3em; color: #ff2079; vertical-align: super; }

.subtitle {
  margin-top: 12px;
  font-size: clamp(7px, 1.4vw, 11px);
  color: #fff;
  letter-spacing: 1px;
  text-shadow: 2px 2px 0 #1a2a5e;
}

/* ================= SCOREBOARD ================= */
.scoreboard {
  position: relative;
  z-index: 12;
  display: flex;
  justify-content: center;
  gap: 22px;
  flex-wrap: wrap;
  margin-top: 14px;
  font-size: 9px;
  color: #0a0020;
  pointer-events: none;
}
.scoreboard span {
  background: rgba(255,255,255,0.88);
  border: 2px solid #1a2a5e;
  padding: 5px 8px;
  box-shadow: 3px 3px 0 rgba(26,42,94,0.55);
}

/* ================= LOGIN BUTTONS ================= */
.login {
  position: absolute;
  z-index: 10;
  font-family: inherit;
  font-size: 10px;
  padding: 13px 16px;
  color: #0a0020;
  background: #ffe600;
  border: 4px solid #fff;
  box-shadow: 5px 5px 0 #ff2079, 0 0 24px rgba(255,230,0,0.45);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}
.login:hover {
  background: #7cff00;
  box-shadow: 5px 5px 0 #1a2a5e, 0 0 30px rgba(124,255,0,0.6);
}
.login:active { transform: translate(3px, 3px); box-shadow: 2px 2px 0 #ff2079; }
.login:focus-visible { outline: 4px dashed #ff2079; outline-offset: 3px; }
.login-note { font-size: 12px; }

/* Wandering paths — smooth loops, no fleeing */
.drift-peak   { animation: pathPeak 26s ease-in-out infinite; }
.drift-ledge  { animation: pathLedge 20s ease-in-out infinite; }
.drift-valley { animation: pathValley 24s ease-in-out infinite; }
.drift-sea    { animation: pathSea 30s ease-in-out infinite; }

@keyframes pathPeak {
  0%   { left: 8%;  top: 33%; }
  30%  { left: 22%; top: 29%; }
  60%  { left: 34%; top: 36%; }
  100% { left: 8%;  top: 33%; }
}
@keyframes pathLedge {
  0%   { left: 60%; top: 48%; }
  50%  { left: 74%; top: 55%; }
  100% { left: 60%; top: 48%; }
}
@keyframes pathValley {
  0%   { left: 14%; top: 58%; }
  33%  { left: 26%; top: 65%; }
  66%  { left: 18%; top: 70%; }
  100% { left: 14%; top: 58%; }
}
@keyframes pathSea {
  0%   { left: 40%; top: 84%; }
  25%  { left: 52%; top: 88%; }
  50%  { left: 58%; top: 82%; }
  75%  { left: 46%; top: 90%; }
  100% { left: 40%; top: 84%; }
}

/* ================= WELCOME ================= */
.welcome {
  position: absolute;
  left: 50%; top: 42%;
  transform: translateX(-50%);
  z-index: 13;
  text-align: center;
  font-size: clamp(12px, 2.4vw, 20px);
  color: #fff;
  text-shadow: 3px 3px 0 #ff2079, 0 0 16px rgba(255,230,0,0.9);
  animation: blink 1.2s steps(1) infinite;
  pointer-events: none;
  line-height: 2;
}
.welcome-sub { font-size: 0.55em; color: #ffe600; }

/* ================= CONFETTI ================= */
.confetti {
  position: absolute;
  top: -20px;
  z-index: 14;
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

/* ================= FOOTER ================= */
.footer {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  z-index: 12;
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
  padding: 6px 10px;
  font-weight: bold;
  color: #fff;
  text-shadow: 1px 1px 0 #000;
}
.counter { color: #fff; display: flex; align-items: center; gap: 3px; text-shadow: 1px 1px 0 #000; }
.digit {
  background: #000;
  border: 1px solid #00f0ff;
  padding: 3px 4px;
  color: #7cff00;
}
.badge { color: #fff; text-shadow: 1px 1px 0 #1a2a5e; }

/* ================= CRT ================= */
.scanlines {
  position: absolute; inset: 0;
  z-index: 20;
  pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,0.14) 0 2px, transparent 2px 4px);
  mix-blend-mode: multiply;
}
.vignette {
  position: absolute; inset: 0;
  z-index: 20;
  pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 58%, rgba(0,0,0,0.5) 100%);
}

/* ================= Accessibility ================= */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
  .login { position: static; display: inline-flex; margin: 8px; }
}
`
