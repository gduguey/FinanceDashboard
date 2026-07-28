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

// Each login button drifts along its own path — none of them flee the mouse.
const LOGIN_BUTTONS: LoginButtonSpec[] = [
  { id: 'cloud', label: 'SE CONNECTER', cls: 'drift-cloud', note: '☁' },
  { id: 'raft', label: 'LOGIN ICI ?', cls: 'drift-raft', note: '~' },
  { id: 'balloon', label: 'OU LÀ !', cls: 'drift-balloon', note: '◯' },
  { id: 'field', label: 'ENTRÉE VIP', cls: 'drift-field', note: '✿' },
]

interface SheepSpec {
  id: number
  top: number
  dur: number
  delay: number
  dir: 1 | -1
  scale: number
}

const SHEEP: SheepSpec[] = [
  { id: 1, top: 76, dur: 26, delay: 0, dir: 1, scale: 1 },
  { id: 2, top: 80, dur: 34, delay: -12, dir: -1, scale: 0.85 },
  { id: 3, top: 84, dur: 22, delay: -6, dir: 1, scale: 1.15 },
  { id: 4, top: 78, dur: 40, delay: -20, dir: -1, scale: 0.7 },
]

interface BirdSpec {
  id: number
  top: number
  dur: number
  delay: number
}

const BIRDS: BirdSpec[] = [
  { id: 1, top: 10, dur: 18, delay: 0 },
  { id: 2, top: 16, dur: 24, delay: -8 },
  { id: 3, top: 7, dur: 30, delay: -15 },
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
}

// "Bergerie des Finances" — the French pastoral sheep-farm themed landing page.
// See landing-pages/index.ts to make this the active one.
export function BergeriePastoralLanding() {
  const { openSignIn } = useClerk()
  const [confetti, setConfetti] = useState<ConfettiPiece[]>([])
  const [credits, setCredits] = useState(0)
  const [coins, setCoins] = useState<Coin[]>([])

  // The bergerie's chimney puffs € coins instead of smoke
  useEffect(() => {
    const t = setInterval(() => {
      const id = Date.now() + Math.random()
      setCoins((c) => [...c.slice(-6), { id, drift: (Math.random() - 0.5) * 40, dur: 2.6 + Math.random() * 1.2 }])
    }, 1400)
    return () => clearInterval(t)
  }, [])

  const login = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      e.stopPropagation()
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

      {/* Pixel clouds */}
      <div className="cloud c1" />
      <div className="cloud c2" />
      <div className="cloud c3" />

      {/* Birds */}
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

      {/* ---- Mountains ---- */}
      <div className="mountains back" />
      <div className="mountains front" />

      {/* ---- Water ---- */}
      <div className="water">
        <div className="shimmer" />
        <div className="fish">
          <span className="fish-body" />
        </div>
      </div>

      {/* ---- Grass ---- */}
      <div className="grass" />
      <div className="flowers">
        <span>✿</span>
        <span>❀</span>
        <span>✿</span>
        <span>❀</span>
        <span>✿</span>
      </div>

      {/* ---- La Bergerie des Finances ---- */}
      <div className="bergerie">
        <div className="chimney" />
        {coins.map((c) => (
          <span
            key={c.id}
            className="coin"
            style={{ '--drift': `${c.drift}px`, animationDuration: `${c.dur}s` } as CSSProperties}
          >
            €
          </span>
        ))}
        <div className="roof" />
        <div className="house-body">
          <div className="window">
            <span>€</span>
          </div>
          <div className="door" />
        </div>
        <div className="house-sign">BERGERIE DES FINANCES</div>
      </div>

      {/* ---- Sheep ---- */}
      {SHEEP.map((s) => (
        <div
          key={s.id}
          className={`sheep ${s.dir === -1 ? 'reverse' : ''}`}
          style={
            {
              top: `${s.top}%`,
              animationDuration: `${s.dur}s`,
              animationDelay: `${s.delay}s`,
              '--scale': s.scale,
            } as CSSProperties
          }
        >
          <span className="wool" />
          <span className="sheep-head" />
          <span className="leg l1" />
          <span className="leg l2" />
        </div>
      ))}

      {/* ---- Marquee ---- */}
      <div className="marquee">
        <div className="marquee-inner">
          {'☆ BIENVENUE À LA BERGERIE DES FINANCES ☆ TES SOUS BROUTENT PAISIBLEMENT ☆ 4 BOUTONS LOGIN, 0 EXCUSE ☆ MÉTÉO : PIXEL AVEC ÉCLAIRCIES ☆ TON PEL VA BIEN ☆ '.repeat(
            2,
          )}
        </div>
      </div>

      {/* ---- Title ---- */}
      <header className="hero">
        <div className="press-start blink">▼ CHOISIS UN BOUTON, N'IMPORTE LEQUEL ▼</div>
        <h1 className="title">
          FINANCE<span className="title-alt">QUEST</span>
          <sup className="tm">™</sup>
        </h1>
        <p className="subtitle">ÉDITION PASTORALE — LES BOUTONS NE FUIENT PLUS, PROMIS</p>
      </header>

      {/* ---- Scoreboard ---- */}
      <div className="scoreboard">
        <span>MOUTONS&nbsp;:&nbsp;{String(SHEEP.length).padStart(2, '0')}</span>
        <span>CREDITS&nbsp;:&nbsp;{String(credits).padStart(3, '0')}</span>
        <span>NIVEAU&nbsp;:&nbsp;PRAIRIE-1</span>
      </div>

      {/* ---- Drifting login buttons (they wander, they don't flee) ---- */}
      {LOGIN_BUTTONS.map((b) => (
        <button key={b.id} className={`login ${b.cls}`} onClick={login}>
          <span className="login-note">{b.note}</span>
          {b.label}
        </button>
      ))}

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
        <div className="construction">⚠ PRAIRIE EN CONSTRUCTION DEPUIS 1997 ⚠</div>
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
  background: #7ec8e3;
  image-rendering: pixelated;
}

/* ================= SKY ================= */
.sky {
  position: absolute; inset: 0;
  background: linear-gradient(
    #2a4d8f 0%,
    #4a7bc4 18%,
    #7ec8e3 42%,
    #ffd9a0 58%,
    #ffb37e 66%
  );
}

.sun {
  position: absolute;
  left: 12%; top: 9%;
  width: 90px; height: 90px;
  z-index: 1;
}
.sun-core {
  position: absolute; inset: 15px;
  background: #ffe600;
  box-shadow:
    0 0 0 6px #ffb700,
    0 0 40px rgba(255,230,0,0.8);
  border-radius: 4px;
}
.sun-rays {
  position: absolute; inset: -12px;
  background:
    conic-gradient(#ffe60088 0 10deg, transparent 10deg 45deg,
      #ffe60088 45deg 55deg, transparent 55deg 90deg,
      #ffe60088 90deg 100deg, transparent 100deg 135deg,
      #ffe60088 135deg 145deg, transparent 145deg 180deg,
      #ffe60088 180deg 190deg, transparent 190deg 225deg,
      #ffe60088 225deg 235deg, transparent 235deg 270deg,
      #ffe60088 270deg 280deg, transparent 280deg 315deg,
      #ffe60088 315deg 325deg, transparent 325deg 360deg);
  animation: sunSpin 24s linear infinite;
}
@keyframes sunSpin { to { transform: rotate(360deg); } }

/* Pixel clouds */
.cloud {
  position: absolute;
  z-index: 1;
  width: 110px; height: 30px;
  background: #fff;
  box-shadow:
    -22px 10px 0 0 #fff,
    26px 10px 0 0 #fff,
    0 -14px 0 -6px #fff,
    -40px 14px 0 -8px #e8f4ff,
    46px 14px 0 -8px #e8f4ff;
  border-radius: 6px;
  opacity: 0.95;
  animation: cloudDrift linear infinite;
}
.cloud.c1 { top: 8%;  animation-duration: 55s; }
.cloud.c2 { top: 18%; animation-duration: 75s; animation-delay: -30s; transform: scale(0.7); }
.cloud.c3 { top: 27%; animation-duration: 95s; animation-delay: -60s; transform: scale(1.25); }
@keyframes cloudDrift {
  from { left: -180px; }
  to   { left: 110%; }
}

/* Birds : two flapping pixel wings */
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
  background: #2a2a3e;
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

/* ================= MOUNTAINS ================= */
.mountains {
  position: absolute;
  left: -2%; right: -2%;
  pointer-events: none;
}
.mountains.back {
  top: 30%;
  height: 26%;
  background: #5b4a8a;
  clip-path: polygon(
    0% 100%, 0% 55%, 8% 30%, 16% 60%, 25% 15%, 33% 55%,
    42% 35%, 50% 70%, 60% 20%, 69% 58%, 78% 32%, 88% 65%,
    95% 40%, 100% 60%, 100% 100%
  );
  z-index: 1;
}
.mountains.front {
  top: 36%;
  height: 24%;
  background: #7a5fae;
  clip-path: polygon(
    0% 100%, 0% 70%, 10% 40%, 20% 75%, 30% 30%, 41% 68%,
    52% 45%, 63% 80%, 72% 35%, 83% 70%, 92% 50%, 100% 75%, 100% 100%
  );
  z-index: 2;
}
.mountains.front::after {
  content: "";
  position: absolute; inset: 0;
  background: linear-gradient(#fff 0 8%, transparent 8%);
  clip-path: inherit;
  opacity: 0.5;
}

/* ================= WATER ================= */
.water {
  position: absolute;
  left: 0; right: 0;
  top: 55%;
  height: 16%;
  background: linear-gradient(#3fa9f5, #1b6fd1);
  z-index: 3;
  overflow: hidden;
}
.shimmer {
  position: absolute; inset: 0;
  background:
    repeating-linear-gradient(90deg,
      rgba(255,255,255,0.35) 0 14px, transparent 14px 46px);
  background-size: 200% 100%;
  mix-blend-mode: overlay;
  animation: shimmerMove 5s steps(12) infinite;
  opacity: 0.6;
}
@keyframes shimmerMove { to { background-position: 200% 0; } }

/* A fish jumping out of the water on loop */
.fish {
  position: absolute;
  left: 20%;
  bottom: 40%;
  animation: fishJump 7s ease-in-out infinite;
}
.fish-body {
  display: block;
  width: 18px; height: 10px;
  background: #ff6b00;
  border-radius: 6px 2px 2px 6px;
  box-shadow: 16px 0 0 -3px #ff2079;
}
@keyframes fishJump {
  0%, 60%, 100% { transform: translateY(60px) rotate(0deg); opacity: 0; }
  66% { transform: translateY(-26px) rotate(-30deg); opacity: 1; }
  72% { transform: translateY(-38px) rotate(0deg); opacity: 1; }
  78% { transform: translateY(-26px) rotate(30deg); opacity: 1; }
  84% { transform: translateY(60px) rotate(50deg); opacity: 0; }
}

/* ================= GRASS ================= */
.grass {
  position: absolute;
  left: 0; right: 0;
  top: 69%;
  bottom: 0;
  background:
    linear-gradient(#6fce4e, #3f9e35 55%, #2c7a28);
  z-index: 4;
}
.grass::before {
  content: "";
  position: absolute; left: 0; right: 0; top: -6px; height: 6px;
  background: repeating-linear-gradient(90deg, #6fce4e 0 10px, transparent 10px 16px);
}
.flowers {
  position: absolute;
  z-index: 5;
  top: 72%;
  width: 100%;
  display: flex;
  justify-content: space-around;
  font-size: 12px;
  color: #ff2079;
  pointer-events: none;
}
.flowers span:nth-child(2n) { color: #ffe600; transform: translateY(14px); }
.flowers span:nth-child(3n) { color: #fff; }

/* ================= BERGERIE ================= */
.bergerie {
  position: absolute;
  right: 9%;
  top: 56%;
  z-index: 6;
  width: 130px;
  pointer-events: none;
}
.roof {
  width: 0; height: 0;
  margin: 0 auto;
  border-left: 75px solid transparent;
  border-right: 75px solid transparent;
  border-bottom: 44px solid #a8352a;
  position: relative;
  filter: drop-shadow(0 3px 0 #7a2019);
}
.chimney {
  position: absolute;
  top: -18px; right: 22px;
  width: 16px; height: 30px;
  background: #7a4a2b;
  border-top: 4px solid #5c3620;
  z-index: 1;
}
.coin {
  position: absolute;
  top: -22px; right: 22px;
  font-size: 11px;
  color: #ffe600;
  text-shadow: 0 0 6px #ffb700, 1px 1px 0 #7a5500;
  animation: coinRise ease-out forwards;
  z-index: 2;
}
@keyframes coinRise {
  from { transform: translate(0, 0) scale(1); opacity: 1; }
  to   { transform: translate(var(--drift), -70px) scale(0.5); opacity: 0; }
}
.house-body {
  width: 118px;
  height: 66px;
  margin: 0 auto;
  background: #f2e3c4;
  border: 4px solid #b08d57;
  border-top: none;
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  padding-bottom: 0;
}
.window {
  width: 30px; height: 28px;
  margin-bottom: 16px;
  background: #7ec8e3;
  border: 3px solid #b08d57;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: #1b6fd1;
  text-shadow: 0 0 6px #00f0ff;
  animation: windowGlow 2.4s steps(2) infinite;
}
@keyframes windowGlow { 50% { background: #ffe600; color: #a8352a; } }
.door {
  width: 26px; height: 40px;
  background: #8a5a2b;
  border: 3px solid #5c3620;
  border-bottom: none;
}
.house-sign {
  margin-top: 8px;
  text-align: center;
  font-size: 7px;
  color: #fff;
  background: #2c7a28;
  border: 2px solid #ffe600;
  padding: 4px 2px;
  text-shadow: 1px 1px 0 #000;
}

/* ================= SHEEP ================= */
.sheep {
  position: absolute;
  z-index: 6;
  width: 46px; height: 30px;
  animation-name: sheepWalk;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  transform: scale(var(--scale));
  pointer-events: none;
}
.sheep.reverse { animation-name: sheepWalkReverse; }
.wool {
  position: absolute;
  left: 4px; top: 0;
  width: 36px; height: 20px;
  background: #fdfdfd;
  border-radius: 10px;
  box-shadow:
    -5px 4px 0 -2px #fdfdfd,
    5px -3px 0 -2px #fdfdfd,
    12px 3px 0 -3px #eee;
  animation: bob 0.8s steps(2) infinite;
}
.sheep-head {
  position: absolute;
  right: -2px; top: 4px;
  width: 12px; height: 10px;
  background: #2a2a3e;
  border-radius: 3px;
  animation: bob 0.8s steps(2) infinite;
}
.sheep.reverse .sheep-head { right: auto; left: -2px; }
.leg {
  position: absolute;
  bottom: 0;
  width: 4px; height: 9px;
  background: #2a2a3e;
  animation: trot 0.5s steps(2) infinite;
}
.leg.l1 { left: 12px; }
.leg.l2 { left: 28px; animation-delay: 0.25s; }
@keyframes bob { 50% { transform: translateY(2px); } }
@keyframes trot { 50% { transform: translateY(-3px); } }
@keyframes sheepWalk {
  from { left: -70px; }
  to   { left: 105%; }
}
@keyframes sheepWalkReverse {
  from { left: 105%; transform: scale(var(--scale)) scaleX(-1); }
  to   { left: -70px; transform: scale(var(--scale)) scaleX(-1); }
}

/* ================= MARQUEE ================= */
.marquee {
  position: relative;
  z-index: 10;
  overflow: hidden;
  background: #ff2079;
  color: #0a0020;
  font-size: 10px;
  padding: 8px 0;
  border-bottom: 3px solid #ffe600;
  white-space: nowrap;
}
.marquee-inner { display: inline-block; animation: scroll 26s linear infinite; }
@keyframes scroll { to { transform: translateX(-50%); } }

/* ================= HERO ================= */
.hero {
  position: relative;
  text-align: center;
  margin-top: 4vh;
  z-index: 10;
  pointer-events: none;
}
.press-start { font-size: 10px; color: #fff; letter-spacing: 2px; margin-bottom: 18px; text-shadow: 2px 2px 0 #2a4d8f; }
.blink { animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }

.title {
  font-size: clamp(24px, 6vw, 56px);
  color: #ffe600;
  letter-spacing: 4px;
  text-shadow:
    3px 3px 0 #a8352a,
    6px 6px 0 #2a4d8f,
    0 0 24px rgba(255,230,0,0.6);
}
.title-alt { color: #7cff00; }
.tm { font-size: 0.3em; color: #ff2079; vertical-align: super; }

.subtitle {
  margin-top: 14px;
  font-size: clamp(7px, 1.4vw, 11px);
  color: #fff;
  letter-spacing: 1px;
  text-shadow: 2px 2px 0 #2a4d8f;
}

/* ================= SCOREBOARD ================= */
.scoreboard {
  position: relative;
  z-index: 10;
  display: flex;
  justify-content: center;
  gap: 26px;
  flex-wrap: wrap;
  margin-top: 18px;
  font-size: 9px;
  color: #0a0020;
  pointer-events: none;
}
.scoreboard span {
  background: rgba(255,255,255,0.85);
  border: 2px solid #2a4d8f;
  padding: 5px 8px;
  box-shadow: 3px 3px 0 rgba(42,77,143,0.5);
}

/* ================= LOGIN BUTTONS ================= */
.login {
  position: absolute;
  z-index: 8;
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
  box-shadow: 5px 5px 0 #2a4d8f, 0 0 30px rgba(124,255,0,0.6);
}
.login:active { transform: translate(3px, 3px); box-shadow: 2px 2px 0 #ff2079; }
.login:focus-visible { outline: 4px dashed #ff2079; outline-offset: 3px; }
.login-note { font-size: 12px; }

/* Each button wanders on its own gentle loop — no fleeing */
.drift-cloud   { animation: pathCloud 26s ease-in-out infinite; }
.drift-raft    { animation: pathRaft 18s ease-in-out infinite; }
.drift-balloon { animation: pathBalloon 22s ease-in-out infinite; }
.drift-field   { animation: pathField 30s ease-in-out infinite; }

@keyframes pathCloud {
  0%   { left: 6%;  top: 34%; }
  25%  { left: 24%; top: 30%; }
  50%  { left: 40%; top: 36%; }
  75%  { left: 20%; top: 40%; }
  100% { left: 6%;  top: 34%; }
}
@keyframes pathRaft {
  0%   { left: 12%; top: 60%; }
  50%  { left: 55%; top: 63%; }
  100% { left: 12%; top: 60%; }
}
@keyframes pathBalloon {
  0%   { left: 62%; top: 26%; }
  33%  { left: 74%; top: 34%; }
  66%  { left: 58%; top: 40%; }
  100% { left: 62%; top: 26%; }
}
@keyframes pathField {
  0%   { left: 30%; top: 82%; }
  25%  { left: 55%; top: 86%; }
  50%  { left: 68%; top: 80%; }
  75%  { left: 45%; top: 88%; }
  100% { left: 30%; top: 82%; }
}

/* ================= CONFETTI ================= */
.confetti {
  position: absolute;
  top: -20px;
  z-index: 12;
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
  z-index: 10;
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
.badge { color: #fff; text-shadow: 1px 1px 0 #2a4d8f; }

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
  background: radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.45) 100%);
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
