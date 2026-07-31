"""The operator app — the phone-sized VisionGuard for guards and committee.

Separate from the console on purpose. The console is for sitting down and
reviewing footage; this is for someone standing at a gate holding a cheap
Android in one hand. So: bottom navigation within thumb reach, tap targets you
can hit without looking, and three jobs only — triage an alert, check the gate
register, tell residents something.

The look: a deep violet night with slow aurora blooms behind it, glass cards
floating on top, and one polished-metal slab per screen carrying the number
that matters. The metal gradient runs light -> saturated -> deep -> light
again, and a sheen travels across it, which is what separates it from a flat
fade. Everything else stays quiet so the three signal hues — needs attention,
watch, settled — still read at arm's length.

Movement is kept to things that mean something: numbers count up when they
change, sparklines are drawn from real timestamps, the unread badge breathes.
All of it stands down under prefers-reduced-motion.

Served as a PWA (installable, works from the home screen) rather than a native
app: no app-store review between a fix and the guard having it.
"""
from __future__ import annotations

import io
import json

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#08050f">
<title>VisionGuard Operator</title>
<link rel="manifest" href="/operator/manifest.webmanifest">
<link rel="icon" href="/operator/icon-192.png">
<link rel="apple-touch-icon" href="/operator/icon-192.png">
<style>
/* Manrope, served from this app rather than a font CDN — a guard at a gate
   with no signal must not drop to the system font mid-shift. One variable
   file covers every weight the app uses. SIL OFL, app/static/manrope-OFL.txt */
@font-face{
  font-family:"Manrope"; font-style:normal; font-weight:200 800;
  font-display:swap; src:url(/operator/font.woff2) format("woff2");
}
:root{
  /* Near-black with a violet cast, so the metal and the signal hues are the
     only things on screen with real saturation. */
  --night:#08050f; --night-2:#100a1e;
  --glass:rgba(255,255,255,.045); --glass-2:rgba(255,255,255,.075);
  --edge:rgba(255,255,255,.085); --edge-lit:rgba(255,255,255,.19);
  --text:#f4f1ff; --soft:#c8bfe8; --muted:#8d84b0;

  /* The metallic: a violet run through light, saturated, deep and back to
     light again. Six stops, not two — that turn from dark back to pale is
     what makes it read as polished metal rather than a flat fade. */
  --metal:linear-gradient(145deg,#dcd2ff 0%,#ab8dff 17%,#7f56f0 42%,
                          #5a2fd0 64%,#8b63f5 86%,#c3aeff 100%);
  --metal-soft:linear-gradient(150deg,#8f6bf2 0%,#5f34d4 55%,#7c53ee 100%);
  --sheen:linear-gradient(104deg,transparent 34%,rgba(255,255,255,.42) 47%,
                          transparent 60%);

  --alert:#ff6183; --caution:#ffb45c; --calm:#4ade9e;

  --lift:0 18px 44px -20px rgba(88,44,208,.95), inset 0 1px 0 var(--edge-lit);
  --lift-2:0 26px 60px -22px rgba(88,44,208,1), inset 0 1px 0 rgba(255,255,255,.3);
  --r:26px; --r-sm:18px; --tap:56px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{
  background:var(--night); color:var(--text);
  font-family:"Manrope",-apple-system,BlinkMacSystemFont,system-ui,
              "Segoe UI",Roboto,sans-serif;
  font-size:16px; line-height:1.5; letter-spacing:-.012em;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(112px + env(safe-area-inset-bottom));
  overflow-x:hidden;
}
:focus-visible{outline:2px solid #b49bff; outline-offset:3px; border-radius:10px}

/* --- the light in the room --- *
 * Four violet blooms that drift and breathe, plus one wide band of light
 * crossing the screen on a very long cycle. Only transform and opacity are
 * animated: those the compositor can handle on its own, which keeps a cheap
 * Android from dropping frames while a guard is scrolling the list. */
.aurora{position:fixed; inset:0; z-index:-1; overflow:hidden; background:var(--night)}
.aurora span{position:absolute; border-radius:50%; filter:blur(80px);
  will-change:transform, opacity}
.aurora span:nth-child(1){width:78vw; height:78vw; top:-26vw; left:-22vw; opacity:.34;
  background:radial-gradient(circle,#5f2fc4,transparent 68%);
  animation:drift1 26s ease-in-out infinite, breathe 13s ease-in-out infinite}
.aurora span:nth-child(2){width:62vw; height:62vw; top:24vh; right:-26vw; opacity:.34;
  background:radial-gradient(circle,#33208a,transparent 70%);
  animation:drift2 32s ease-in-out infinite, breathe 17s ease-in-out infinite -4s}
.aurora span:nth-child(3){width:70vw; height:70vw; bottom:-24vh; left:-14vw; opacity:.22;
  background:radial-gradient(circle,#7346e0,transparent 72%);
  animation:drift3 38s ease-in-out infinite, breathe 21s ease-in-out infinite -9s}
.aurora span:nth-child(4){width:46vw; height:46vw; top:52vh; left:8vw; opacity:.17;
  background:radial-gradient(circle,#b07cff,transparent 70%);
  animation:drift4 44s ease-in-out infinite, breathe 15s ease-in-out infinite -6s}
/* the pass of light — one slow sweep every 24s, barely there */
.aurora::after{
  content:""; position:absolute; top:-60%; left:-60%; width:220%; height:220%;
  background:linear-gradient(102deg,transparent 42%,rgba(178,140,255,.09) 50%,
             transparent 58%);
  animation:pass 24s linear infinite; will-change:transform;
}
@keyframes drift1{50%{transform:translate(9vw,7vh) scale(1.12)}}
@keyframes drift2{50%{transform:translate(-8vw,-6vh) scale(1.16)}}
@keyframes drift3{50%{transform:translate(7vw,-8vh) scale(1.1)}}
@keyframes drift4{33%{transform:translate(-11vw,6vh) scale(1.2)}
                  66%{transform:translate(6vw,-9vh) scale(.92)}}
@keyframes breathe{50%{opacity:.62}}
@keyframes pass{from{transform:translateX(-42%)} to{transform:translateX(42%)}}

header{
  position:sticky; top:0; z-index:10;
  background:linear-gradient(var(--night) 55%,rgba(8,5,15,0));
  padding:calc(18px + env(safe-area-inset-top)) 20px 14px;
  display:flex; align-items:center; justify-content:space-between; gap:12px;
}
.wordmark{font-size:15px; font-weight:640; letter-spacing:.01em}
.wordmark span{
  background:var(--metal); -webkit-background-clip:text; background-clip:text;
  -webkit-text-fill-color:transparent; font-weight:700;
}
.who{
  font:inherit; font-size:13px; font-weight:560; color:var(--soft);
  background:var(--glass); border:1px solid var(--edge); border-radius:999px;
  padding:8px 16px; cursor:pointer; backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); transition:transform .16s, background .16s;
}
.who:active{transform:scale(.96); background:var(--glass-2)}

main{padding:2px 20px 24px; max-width:560px; margin:0 auto}
.view{display:none} .view.on{display:block}
.view.on > *{animation:enter .5s cubic-bezier(.16,.8,.3,1) backwards}
.view.on > *:nth-child(2){animation-delay:.05s}
.view.on > *:nth-child(3){animation-delay:.1s}
.view.on > *:nth-child(4){animation-delay:.15s}
.view.on > *:nth-child(5){animation-delay:.2s}
@keyframes enter{from{opacity:0; transform:translateY(16px)} to{opacity:1; transform:none}}

.eyebrow{margin:6px 0 4px; font-size:12.5px; font-weight:600; color:var(--muted);
         letter-spacing:.05em; text-transform:uppercase}
h1{font-size:30px; line-height:1.15; font-weight:700; letter-spacing:-.028em;
   margin:0 0 10px; text-wrap:balance}
h1 em{
  font-style:normal;
  background:var(--metal); -webkit-background-clip:text; background-clip:text;
  -webkit-text-fill-color:transparent;
}
.hint{color:var(--muted); font-size:14.5px; margin:0 0 24px; max-width:44ch}
h2{font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
   color:var(--muted); margin:30px 0 13px; display:flex;
   justify-content:space-between; align-items:center}

/* --- the hero pair: one metal slab, two glass tiles --- */
.heroes{display:grid; grid-template-columns:1.06fr 1fr; gap:12px; margin-bottom:6px}
.hero{
  position:relative; overflow:hidden; border-radius:var(--r);
  background:var(--metal); border:1px solid var(--edge-lit); box-shadow:var(--lift-2);
  padding:18px 18px 16px; min-height:196px;
  display:flex; flex-direction:column; justify-content:space-between;
}
/* the sheen: one pass of light travelling across the metal */
.hero::after{
  content:""; position:absolute; inset:-40%; background:var(--sheen);
  transform:translateX(-70%) rotate(6deg); animation:sweep 7s ease-in-out infinite;
  pointer-events:none;
}
@keyframes sweep{0%,62%{transform:translateX(-75%) rotate(6deg)}
                 88%,100%{transform:translateX(75%) rotate(6deg)}}
.hero .cap{font-size:12px; font-weight:620; letter-spacing:.06em;
           text-transform:uppercase; color:rgba(255,255,255,.82); position:relative; z-index:2}
.hero .fig{position:relative; z-index:2}
.hero .fig b{display:block; font-size:58px; font-weight:700; line-height:.92;
             letter-spacing:-.035em; font-variant-numeric:tabular-nums;
             text-shadow:0 4px 20px rgba(40,10,90,.45)}
.hero .fig small{display:block; margin-top:4px; font-size:12.5px; font-weight:560;
                 color:rgba(255,255,255,.8)}
.hero .wave{position:absolute; left:0; right:0; bottom:0; height:96px; z-index:1;
            opacity:.85}

.tiles{display:grid; gap:12px}
.tile{
  border-radius:var(--r-sm); padding:14px 15px; position:relative; overflow:hidden;
  background:var(--glass); border:1px solid var(--edge); box-shadow:var(--lift);
  backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
  display:flex; flex-direction:column; justify-content:space-between;
}
.tile .cap{font-size:10.5px; font-weight:640; letter-spacing:.08em;
           text-transform:uppercase; color:var(--muted)}
.tile b{display:block; margin-top:6px; font-size:30px; font-weight:700;
        line-height:1; letter-spacing:-.028em; font-variant-numeric:tabular-nums}
.tile.warn b{color:var(--caution)} .tile.bad b{color:var(--alert)}
.tile.zero b{color:var(--soft)}
.spark{display:block; width:100%; height:26px; margin-top:8px; overflow:visible}

/* --- list cards: the severity IS the panel --- *
 * Each alert sits on its own sheet of tinted glass rather than wearing a
 * coloured stripe, so the state is legible from the shape of the whole card
 * at arm's length. The tint stays under 20% — enough to name the colour,
 * light enough that white text keeps its contrast on top of it. */
.card{
  position:relative; overflow:hidden; border-radius:var(--r);
  padding:17px 18px 18px; margin-bottom:12px;
  background:var(--glass); border:1px solid var(--edge); box-shadow:var(--lift);
  backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
}
/* the lit corner — where the glass catches the light */
.card::before{
  content:""; position:absolute; inset:0; z-index:0; pointer-events:none;
  border-radius:inherit;
  background:radial-gradient(130% 90% at 8% -10%,
             rgba(255,255,255,.10), transparent 60%);
}
.card > *{position:relative; z-index:1}

.card.sev-HIGH{
  background:linear-gradient(152deg,rgba(255,97,131,.23),rgba(255,97,131,.115) 48%,
             rgba(255,97,131,.075));
  border-color:rgba(255,97,131,.34);
  box-shadow:0 20px 46px -24px rgba(255,60,100,.6),
             inset 0 1px 0 rgba(255,196,209,.26);
}
.card.sev-HIGH::before{background:radial-gradient(130% 90% at 8% -10%,
  rgba(255,150,175,.24), transparent 62%)}

.card.sev-MEDIUM{
  background:linear-gradient(152deg,rgba(255,180,92,.22),rgba(255,180,92,.105) 48%,
             rgba(255,180,92,.07));
  border-color:rgba(255,180,92,.32);
  box-shadow:0 20px 46px -24px rgba(255,160,60,.5),
             inset 0 1px 0 rgba(255,222,180,.24);
}
.card.sev-MEDIUM::before{background:radial-gradient(130% 90% at 8% -10%,
  rgba(255,206,150,.22), transparent 62%)}

.card.sev-LOW{background:var(--glass); border-color:var(--edge)}

/* handled, and the gate's departed vehicles: green, but quieter than the two
   states that still want a person */
.card.done{
  background:linear-gradient(152deg,rgba(74,222,158,.15),rgba(74,222,158,.07) 48%,
             rgba(74,222,158,.045));
  border-color:rgba(74,222,158,.24);
  box-shadow:0 14px 34px -26px rgba(40,200,140,.45),
             inset 0 1px 0 rgba(180,245,215,.16);
}
.card.done::before{background:radial-gradient(130% 90% at 8% -10%,
  rgba(150,240,200,.15), transparent 62%)}

.row{display:flex; justify-content:space-between; align-items:baseline; gap:12px}
.kind{font-size:17px; font-weight:640; letter-spacing:-.025em}
.kind.plate{letter-spacing:.06em; font-variant-numeric:tabular-nums}
.when{color:var(--muted); font-size:12.5px; font-weight:520; white-space:nowrap;
      font-variant-numeric:tabular-nums}
.desc{margin:6px 0 0; font-size:15px; color:var(--soft); line-height:1.48}
.meta{margin:11px 0 0; color:var(--muted); font-size:13px;
      display:flex; align-items:center; gap:9px; flex-wrap:wrap}
.pill{
  display:inline-block; padding:4px 11px; border-radius:999px;
  font-size:10px; font-weight:700; letter-spacing:.09em; text-transform:uppercase;
  background:rgba(255,255,255,.07); border:1px solid var(--edge); color:var(--soft);
}
.pill.high{color:var(--alert); border-color:rgba(255,97,131,.42);
           background:rgba(255,97,131,.13)}
.pill.medium{color:var(--caution); border-color:rgba(255,180,92,.4);
             background:rgba(255,180,92,.12)}
.pill.ok{color:var(--calm); border-color:rgba(74,222,158,.36);
         background:rgba(74,222,158,.11)}

/* --- actions --- */
.actions{display:grid; grid-template-columns:1fr 1fr; gap:11px; margin-top:16px}
button.act{
  min-height:var(--tap); border-radius:16px; cursor:pointer; font:inherit;
  font-size:15px; font-weight:620; letter-spacing:-.01em; color:var(--text);
  background:rgba(255,255,255,.07); border:1px solid var(--edge);
  transition:transform .14s cubic-bezier(.3,1.4,.5,1), background .16s, box-shadow .16s;
}
button.act.real{color:#ff8ba3; border-color:rgba(255,97,131,.36)}
button.act.real:active{background:rgba(255,97,131,.2); box-shadow:0 0 22px -4px rgba(255,97,131,.6)}
button.act.false{color:#7ceab8; border-color:rgba(74,222,158,.32)}
button.act.false:active{background:rgba(74,222,158,.18); box-shadow:0 0 22px -4px rgba(74,222,158,.55)}
button.act:active{transform:scale(.955)}
button.act[disabled]{opacity:.4}
.verdict{margin:14px 0 0; font-size:14px; font-weight:600; color:var(--calm);
         display:flex; align-items:center; gap:9px}
.verdict.real{color:var(--alert)}
.verdict::before{content:""; width:7px; height:7px; border-radius:50%; flex:none;
  background:currentColor; box-shadow:0 0 12px currentColor}
a.clip{display:inline-block; margin-top:13px; font-size:14px; font-weight:560;
       color:#c3aeff; text-decoration:none;
       border-bottom:1px solid rgba(195,174,255,.35); padding-bottom:1px}

/* --- fields --- */
label{display:block; font-size:10.5px; font-weight:700; letter-spacing:.12em;
      text-transform:uppercase; color:var(--muted); margin:22px 0 9px}
input,textarea,select{
  width:100%; font:inherit; color:var(--text); appearance:none;
  background:rgba(255,255,255,.04); border:1px solid var(--edge);
  border-radius:var(--r-sm); padding:15px 17px;
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  transition:border-color .18s, box-shadow .18s;
}
input:focus,textarea:focus,select:focus{
  outline:none; border-color:rgba(171,141,255,.75);
  box-shadow:0 0 0 4px rgba(140,99,245,.18)}
input::placeholder,textarea::placeholder{color:#7f76a3}
textarea{min-height:118px; resize:vertical; line-height:1.5}
select{background-image:linear-gradient(45deg,transparent 50%,var(--soft) 50%),
                       linear-gradient(135deg,var(--soft) 50%,transparent 50%);
       background-position:calc(100% - 22px) 25px,calc(100% - 17px) 25px;
       background-size:5px 5px,5px 5px; background-repeat:no-repeat}
#q{margin-bottom:20px}
button.primary{
  position:relative; overflow:hidden; width:100%; min-height:var(--tap);
  margin-top:26px; cursor:pointer; border-radius:var(--r-sm);
  border:1px solid var(--edge-lit); font:inherit; font-size:16px; font-weight:640;
  color:#fff; background:var(--metal); box-shadow:var(--lift-2);
  transition:transform .14s cubic-bezier(.3,1.4,.5,1);
}
button.primary::after{
  content:""; position:absolute; inset:-40%; background:var(--sheen);
  transform:translateX(-70%) rotate(8deg); animation:sweep 6s ease-in-out infinite;
}
button.primary:active{transform:scale(.985)}
button.primary[disabled]{opacity:.55}

.toast{
  position:fixed; left:20px; right:20px;
  bottom:calc(122px + env(safe-area-inset-bottom));
  max-width:520px; margin:0 auto; z-index:30;
  background:rgba(20,13,36,.93); border:1px solid var(--edge-lit);
  border-radius:var(--r-sm); padding:15px 18px; font-size:14.5px; font-weight:540;
  color:var(--text); box-shadow:var(--lift-2);
  backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px);
  display:flex; align-items:center; gap:12px;
  opacity:0; visibility:hidden; transform:translateY(16px) scale(.97);
  transition:opacity .22s ease, transform .38s cubic-bezier(.16,.9,.3,1),
             visibility 0s linear .38s;
}
.toast::before{content:""; width:8px; height:8px; border-radius:50%; flex:none;
  background:var(--calm); box-shadow:0 0 14px var(--calm)}
.toast.err::before{background:var(--alert); box-shadow:0 0 14px var(--alert)}
.toast.on{opacity:1; visibility:visible; transform:none; transition-delay:0s}
.empty{color:var(--muted); text-align:center; padding:44px 20px; font-size:15px;
  background:rgba(255,255,255,.028); border:1px solid var(--edge);
  border-radius:var(--r)}

/* --- floating nav bar --- */
nav{
  position:fixed; left:16px; right:16px; z-index:20; max-width:520px;
  margin:0 auto; bottom:calc(16px + env(safe-area-inset-bottom));
  display:grid; grid-template-columns:repeat(3,1fr); gap:4px; padding:6px;
  border-radius:24px; background:rgba(14,9,26,.92); border:1px solid var(--edge);
  box-shadow:0 20px 46px -16px rgba(0,0,0,.7);
  backdrop-filter:saturate(160%) blur(26px);
  -webkit-backdrop-filter:saturate(160%) blur(26px);
}
nav button{
  position:relative; overflow:hidden; background:none; border:0; cursor:pointer;
  min-height:52px; border-radius:19px; font:inherit; font-size:12.5px;
  font-weight:620; color:var(--muted);
  display:flex; align-items:center; justify-content:center; gap:7px;
  transition:color .18s ease;
}
nav button.on{color:#fff; background:var(--metal-soft);
              box-shadow:0 10px 22px -10px rgba(120,70,240,.95),
                         inset 0 1px 0 var(--edge-lit)}
nav .badge{
  min-width:20px; height:20px; padding:0 6px; border-radius:999px; flex:none;
  background:var(--alert); color:#2a0a16; font-size:11px; font-weight:750;
  line-height:20px; box-shadow:0 0 14px rgba(255,97,131,.7);
  animation:pulse 2.4s ease-in-out infinite;
}
@keyframes pulse{50%{box-shadow:0 0 22px rgba(255,97,131,1)}}

@media (prefers-reduced-motion:reduce){
  /* :nth-child(n) is here to match the specificity of the rules above that
     set these animations — .aurora span alone loses to .aurora span:nth-child(1)
     and the background keeps moving for someone who asked it not to. */
  .aurora span:nth-child(n){animation:none}
  .aurora::after,.hero::after,button.primary::after,nav .badge{animation:none}
  .view.on > *{animation:none}
  .toast{transition:opacity .01s, visibility 0s}
}
</style>
</head>
<body>
<div class="aurora" aria-hidden="true"><span></span><span></span><span></span><span></span></div>

<header>
  <div class="wordmark">Vision<span>Guard</span></div>
  <button class="who" id="who">set name</button>
</header>

<main>
  <section class="view on" id="v-alerts">
    <p class="eyebrow" id="greeting">Good morning</p>
    <h1 id="h-alerts">Checking the <em>cameras</em></h1>
    <div class="heroes">
      <article class="hero">
        <span class="cap">To check</span>
        <svg class="wave" viewBox="0 0 200 88" preserveAspectRatio="none"
             aria-hidden="true">
          <path d="M0 58 C34 30 56 74 92 50 C126 28 152 62 200 38 L200 88 L0 88 Z"
                fill="rgba(255,255,255,.22)"/>
          <path d="M0 58 C34 30 56 74 92 50 C126 28 152 62 200 38"
                fill="none" stroke="rgba(255,255,255,.7)" stroke-width="2"/>
        </svg>
        <span class="fig"><b id="s-untriaged">0</b><small>need a look</small></span>
      </article>
      <div class="tiles">
        <article class="tile">
          <span class="cap">Last 24 hours</span>
          <b id="s-today">0</b>
          <svg class="spark" id="spark-alerts" viewBox="0 0 100 26"
               preserveAspectRatio="none" aria-hidden="true"></svg>
        </article>
        <article class="tile">
          <span class="cap">False alarms</span>
          <b id="s-false">0</b>
        </article>
      </div>
    </div>
    <h2>Detections</h2>
    <p class="hint">Mark each one once you have looked. Your answer teaches the
      system to stop raising the ones that were never anything.</p>
    <div id="alerts"></div>
  </section>

  <section class="view" id="v-gate">
    <p class="eyebrow">Automatic register</p>
    <h1 id="h-gate">Who is <em>inside</em></h1>
    <div class="heroes">
      <article class="hero">
        <span class="cap">Inside now</span>
        <svg class="wave" viewBox="0 0 200 88" preserveAspectRatio="none"
             aria-hidden="true">
          <path d="M0 46 C40 66 64 26 100 44 C138 62 164 32 200 52 L200 88 L0 88 Z"
                fill="rgba(255,255,255,.22)"/>
          <path d="M0 46 C40 66 64 26 100 44 C138 62 164 32 200 52"
                fill="none" stroke="rgba(255,255,255,.7)" stroke-width="2"/>
        </svg>
        <span class="fig"><b id="s-inside">0</b><small>vehicles</small></span>
      </article>
      <div class="tiles">
        <article class="tile warn">
          <span class="cap">Visitors</span>
          <b id="s-visitors">0</b>
          <svg class="spark" id="spark-gate" viewBox="0 0 100 26"
               preserveAspectRatio="none" aria-hidden="true"></svg>
        </article>
        <article class="tile bad">
          <span class="cap">Overstaying</span>
          <b id="s-over">0</b>
        </article>
      </div>
    </div>
    <h2>Gate register</h2>
    <input id="q" placeholder="Search a plate" autocomplete="off"
           inputmode="latin" aria-label="Search a plate">
    <div id="visits"></div>
  </section>

  <section class="view" id="v-notices">
    <p class="eyebrow">Announcements</p>
    <h1>Tell the <em>members</em></h1>
    <p class="hint">Goes to every resident who has connected Telegram. Anything
      you send is kept on record below.</p>
    <div>
      <label for="n-title">Subject</label>
      <input id="n-title" maxlength="80" placeholder="Water supply cut">
      <label for="n-body">Message</label>
      <textarea id="n-body" maxlength="900" placeholder="Tomorrow, 10am to 1pm."></textarea>
      <label for="n-aud">Send to</label>
      <select id="n-aud">
        <option value="all">Everyone</option>
        <option value="flat">One flat</option>
      </select>
      <div id="flat-wrap" style="display:none">
        <label for="n-flat">Flat number</label>
        <input id="n-flat" placeholder="B-402">
      </div>
      <button class="primary" id="send">Send message</button>
    </div>
    <h2>Sent</h2>
    <div id="notices"></div>
  </section>
</main>

<nav>
  <button class="on" data-view="alerts">Alerts<span class="badge" id="nav-badge"
    style="display:none">0</span></button>
  <button data-view="gate">Gate</button>
  <button data-view="notices">Messages</button>
</nav>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const api = p => fetch(p, {cache:"no-store"}).then(r => r.json());
const form = (p, d) => fetch(p, {method:"POST", body:new URLSearchParams(d)})
  .then(async r => { if(!r.ok) throw new Error((await r.json()).detail || r.status);
                     return r.json(); });

let who = localStorage.getItem("vg_operator") || "";
function renderWho(){ $("#who").textContent = who || "set name"; }
$("#who").onclick = () => {
  const n = prompt("Your name — it is recorded against what you mark.", who);
  if(n !== null){ who = n.trim(); localStorage.setItem("vg_operator", who); renderWho(); }
};
renderWho();

let toastTimer;
function toast(msg, bad){
  const t = $("#toast");
  t.textContent = msg; t.classList.toggle("err", !!bad); t.classList.add("on");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
}

const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function ago(ts){
  const s = Math.max(0, Date.now()/1000 - ts);
  if(s < 60) return "just now";
  if(s < 3600) return Math.floor(s/60) + " min ago";
  if(s < 86400) return Math.floor(s/3600) + " hr ago";
  return new Date(ts*1000).toLocaleDateString([], {day:"numeric", month:"short"});
}
const clock = ts => new Date(ts*1000).toLocaleTimeString([],
  {hour:"2-digit", minute:"2-digit"});

// UNAUTHORIZED_VEHICLE -> "Unauthorized vehicle". Done here rather than with
// text-transform, which would also re-case anything a person typed.
const sentence = s => {
  const t = String(s || "").replace(/_/g, " ").toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

// A zero is good news on every one of these tiles, so it should not be the
// colour that means "look at me". Numbers count up rather than snapping, so a
// refresh that changes something is visible from across a desk.
function stat(sel, n){
  const el = $(sel);
  el.parentElement.classList.toggle("zero", !n);
  const from = parseInt(el.textContent, 10) || 0;
  if(from === n || REDUCED){ el.textContent = n; return; }
  const t0 = performance.now(), ms = 520;
  (function step(t){
    const k = Math.min(1, (t - t0) / ms);
    el.textContent = Math.round(from + (n - from) * (1 - Math.pow(1 - k, 3)));
    if(k < 1) requestAnimationFrame(step);
  })(t0);
}

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Activity over the last 24 hours, drawn from the timestamps we already have
// rather than a second request. Decoration that happens to be true.
function sparkline(sel, times){
  const el = $(sel);
  if(!el) return;
  const now = Date.now() / 1000, buckets = new Array(24).fill(0);
  for(const t of times){
    const h = Math.floor((now - t) / 3600);
    if(h >= 0 && h < 24) buckets[23 - h]++;
  }
  const peak = Math.max(1, ...buckets);
  const pts = buckets.map((v, i) =>
    [i * (100 / 23), 24 - (v / peak) * 21]);
  const line = pts.map(([x, y], i) =>
    (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1)).join(" ");
  const last = pts[pts.length - 1];
  el.innerHTML = `
    <defs><linearGradient id="${sel.slice(1)}-g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#c3aeff" stop-opacity=".55"/>
      <stop offset="1" stop-color="#c3aeff" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${line} L100 26 L0 26 Z" fill="url(#${sel.slice(1)}-g)"/>
    <path d="${line}" fill="none" stroke="#d3c4ff" stroke-width="1.4"
          stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.2"
            fill="#fff"/>`;
}

/* ------------------------------------------------------------- alerts */
async function loadAlerts(){
  let rows = [];
  try { rows = await api("/api/events?limit=60"); }
  catch(e){ $("#alerts").innerHTML = '<p class="empty">Cannot reach the server.</p>';
            return; }
  const dayAgo = Date.now()/1000 - 86400;
  const untriaged = rows.filter(r => !r.verdict).length;
  stat("#s-untriaged", untriaged);
  stat("#s-today", rows.filter(r => r.ts > dayAgo).length);
  stat("#s-false", rows.filter(r => r.verdict === "false_alarm").length);
  sparkline("#spark-alerts", rows.map(r => r.ts));
  $("#h-alerts").innerHTML = untriaged
    ? `${untriaged} ${untriaged === 1 ? "detection needs" : "detections need"}
       <em>your eye</em>`
    : "Everything here is <em>settled</em>";
  const badge = $("#nav-badge");
  badge.style.display = untriaged ? "inline-block" : "none";
  badge.textContent = untriaged;

  if(!rows.length){
    $("#alerts").innerHTML = '<p class="empty">Nothing has been detected yet.</p>';
    return;
  }
  // What still needs a person comes first; within that, newest first. The
  // server orders by row id, which is not the same thing once an old clip is
  // analysed after a live one.
  rows.sort((a, b) => (!!a.verdict - !!b.verdict) || (b.ts - a.ts));
  $("#alerts").innerHTML = rows.map(r => {
    const sev = (r.severity || "LOW").toUpperCase();
    const done = !!r.verdict;
    const clip = r.clip_id && !r.clip_deleted
      ? `<a class="clip" href="/clips/${r.clip_id}" target="_blank"
            rel="noopener">Watch the clip</a>` : "";
    const body = done
      ? `<p class="verdict ${r.verdict === "real" ? "real" : ""}">
           ${r.verdict === "real" ? "Confirmed real" : "Marked a false alarm"}
           ${r.verdict_by && r.verdict_by !== "operator"
              ? "by " + esc(r.verdict_by) : ""}</p>`
      : `<div class="actions">
           <button class="act real" data-id="${r.id}" data-v="real">It is real</button>
           <button class="act false" data-id="${r.id}" data-v="false_alarm">False alarm</button>
         </div>`;
    return `<article class="card sev-${sev} ${done ? "done" : ""}">
      <div class="row">
        <span class="kind">${esc(sentence(r.event_type))}</span>
        <span class="when">${ago(r.ts)}</span>
      </div>
      <p class="desc">${esc(r.description || "")}</p>
      <p class="meta"><span class="pill ${sev.toLowerCase()}">${sev}</span>
        &nbsp;${esc(r.camera || "")}${r.plate ? " &middot; " + esc(r.plate) : ""}</p>
      ${r.ai_summary ? `<p class="meta">AI: ${esc(r.ai_summary)}</p>` : ""}
      ${clip}${body}
    </article>`;
  }).join("");
}

$("#alerts").addEventListener("click", async e => {
  const b = e.target.closest("button.act");
  if(!b) return;
  const card = b.closest(".card");
  card.querySelectorAll("button.act").forEach(x => x.disabled = true);
  try {
    await form(`/api/events/${b.dataset.id}/feedback`,
               {verdict:b.dataset.v, user_name:who});
    toast(b.dataset.v === "real" ? "Marked real. Thank you."
                                 : "Marked a false alarm. Thank you.");
    loadAlerts();
  } catch(err){
    card.querySelectorAll("button.act").forEach(x => x.disabled = false);
    toast("Could not save that — try again.", true);
  }
});

/* --------------------------------------------------------------- gate */
async function loadGate(){
  let open = [], over = [], rows = [];
  const q = $("#q").value.trim();
  try {
    [open, over, rows] = await Promise.all([
      api("/api/visits/open"), api("/api/visits/overstays"),
      api("/api/visits?limit=100" + (q ? "&plate=" + encodeURIComponent(q) : ""))]);
  } catch(e){ $("#visits").innerHTML = '<p class="empty">Cannot reach the server.</p>';
              return; }
  stat("#s-inside", open.length);
  stat("#s-visitors", open.filter(v => !v.registered).length);
  stat("#s-over", over.length);
  sparkline("#spark-gate", rows.filter(v => !v.registered).map(v => v.entry_ts));
  $("#h-gate").innerHTML = over.length
    ? `${over.length} ${over.length === 1 ? "vehicle has" : "vehicles have"}
       <em>overstayed</em>`
    : open.length ? `${open.length} ${open.length === 1 ? "vehicle" : "vehicles"}
       <em>inside</em> now`
    : "The society is <em>empty</em>";
  const overIds = new Set(over.map(v => v.id));

  if(!rows.length){
    $("#visits").innerHTML = `<p class="empty">${q ? "No visit for that plate."
      : "No vehicle has passed the gate yet."}</p>`;
    return;
  }
  $("#visits").innerHTML = rows.map(v => {
    const inside = !v.exit_ts, late = overIds.has(v.id);
    const tag = late ? '<span class="pill high">Overstaying</span>'
      : v.registered ? '<span class="pill ok">Resident</span>'
                     : '<span class="pill medium">Visitor</span>';
    const owner = v.owner_name
      ? esc(v.owner_name) + (v.flat_number ? " &middot; " + esc(v.flat_number) : "")
      : "not in the registry";
    // Only what a guard might act on is tinted. A resident parked at home is
    // inside all night and must not look like a warning.
    const tone = late ? "sev-HIGH"
      : !inside ? "done" : v.registered ? "sev-LOW" : "sev-MEDIUM";
    return `<article class="card ${tone}">
      <div class="row">
        <span class="kind plate">${esc(v.plate)}</span>
        <span class="when">${inside ? "in " + clock(v.entry_ts)
                                    : clock(v.entry_ts) + " – " + clock(v.exit_ts)}</span>
      </div>
      <p class="meta">${tag} ${owner}</p>
      <p class="meta">${inside ? "Still inside, since " + ago(v.entry_ts)
                               : "Left " + ago(v.exit_ts)}</p>
    </article>`;
  }).join("");
}
let qTimer;
$("#q").addEventListener("input", () => {
  clearTimeout(qTimer); qTimer = setTimeout(loadGate, 300);
});

/* ------------------------------------------------------------ notices */
$("#n-aud").onchange = e => {
  $("#flat-wrap").style.display = e.target.value === "flat" ? "block" : "none";
};
$("#send").onclick = async () => {
  const title = $("#n-title").value.trim(), body = $("#n-body").value.trim();
  if(!title || !body){ toast("A subject and a message are needed.", true); return; }
  const aud = $("#n-aud").value, flat = $("#n-flat").value.trim();
  if(aud === "flat" && !flat){ toast("Which flat?", true); return; }
  $("#send").disabled = true;
  try {
    const r = await form("/api/notices",
      {title, body, author:who || "committee", audience:aud, flat_number:flat});
    toast(r.recipients
      ? `Sent to ${r.recipients} ${r.recipients === 1 ? "resident" : "residents"}.`
      : "Saved. No resident has connected Telegram yet, so nothing was delivered.");
    $("#n-title").value = ""; $("#n-body").value = "";
    loadNotices();
  } catch(err){ toast("Could not send that — try again.", true); }
  $("#send").disabled = false;
};

async function loadNotices(){
  let rows = [];
  try { rows = await api("/api/notices?limit=30"); } catch(e){ return; }
  $("#notices").innerHTML = rows.length ? rows.map(n => `
    <article class="card done">
      <div class="row"><span class="kind">${esc(n.title)}</span>
        <span class="when">${ago(n.ts)}</span></div>
      <p class="desc">${esc(n.body)}</p>
      <p class="meta">${n.audience === "flat" ? "Flat " + esc(n.flat_number) : "Everyone"}
        &middot; ${n.recipients} delivered &middot; ${esc(n.author)}</p>
    </article>`).join("")
    : '<p class="empty">Nothing sent yet.</p>';
}

/* ------------------------------------------------------------- routing */
const loaders = {alerts:loadAlerts, gate:loadGate, notices:loadNotices};
let current = "alerts";
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  current = b.dataset.view;
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("on", v.id === "v-" + current));
  loaders[current]();
});

const hour = new Date().getHours();
$("#greeting").textContent = "Good " +
  (hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening");

loadAlerts(); loadNotices();
// A guard leaves this open on a desk. Refresh the view they are looking at,
// but only while the phone is awake and showing it.
setInterval(() => { if(!document.hidden) loaders[current](); }, 15000);
document.addEventListener("visibilitychange", () => {
  if(!document.hidden) loaders[current]();
});

if("serviceWorker" in navigator){
  navigator.serviceWorker.register("/operator/sw.js").catch(() => {});
}
</script>
</body>
</html>
"""

MANIFEST = {
    "name": "VisionGuard Operator",
    "short_name": "Operator",
    "description": "Alerts, the gate register and messages to members.",
    "start_url": "/operator",
    "scope": "/operator",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#08050f",
    "theme_color": "#08050f",
    "icons": [
        {"src": "/operator/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/operator/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}

# Network-first with a cached shell: a guard on a dead gate-side connection
# still gets the app open (and an honest "cannot reach the server"), rather
# than a browser error page. Alert data is never served from cache — stale
# alerts are worse than none.
SW = """
const SHELL = "vg-operator-shell-v2";
self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(["/operator", "/operator/font.woff2"]))
              .then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if(e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if(e.request.mode === "navigate"){
    e.respondWith(fetch(e.request)
      .then(r => { caches.open(SHELL).then(c => c.put("/operator", r.clone())); return r; })
      .catch(() => caches.match("/operator")));
  }
});
"""


def icon_png(size: int) -> bytes:
    """The home-screen icon, drawn rather than shipped as a binary blob so it
    stays editable and the repo stays free of build artifacts."""
    import cv2
    import numpy as np

    # The same metal as the app, on the diagonal: pale, saturated, deep, pale.
    # A straight two-stop fade looks like plastic at icon size.
    stops = [(0.00, (255, 210, 220)), (0.30, (240, 141, 171)),
             (0.58, (208, 47, 90)), (0.82, (245, 99, 139)),
             (1.00, (255, 174, 195))]                                  # BGR
    d = (np.add.outer(np.arange(size), np.arange(size)) /
         (2 * (size - 1)))                                             # 0..1
    img = np.zeros((size, size, 3))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = (d >= p0) & (d <= p1)
        k = ((d - p0) / (p1 - p0))[m][:, None]
        img[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    img = img.astype(np.uint8)

    c, r = size // 2, int(size * 0.32)
    pale = (255, 252, 248)
    cv2.circle(img, (c, c), r, pale, max(2, size // 26), lineType=cv2.LINE_AA)
    # a chevron inside the ring — a watch mark, not a letter
    d = int(r * 0.46)
    pts = np.array([[c - d, c - int(d * 0.55)], [c, c + int(d * 0.75)],
                    [c + d, c - int(d * 0.55)]], dtype=np.int32)
    cv2.polylines(img, [pts], False, pale, max(2, size // 22),
                  lineType=cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    if not ok:                                     # pragma: no cover
        raise RuntimeError("icon encode failed")
    return io.BytesIO(buf.tobytes()).getvalue()


def manifest_json() -> str:
    return json.dumps(MANIFEST)
