import { useCallback, useEffect, useRef, useState } from "react"
import { Disc3, Flame, Volume2, Pause, Play, RotateCcw, SkipForward, Sliders, Zap, Music2, Radio } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Slider } from "@/components/ui/slider"
import { art, fmt, type Status } from "@/lib/api"
import { cn } from "@/lib/utils"

interface DjSetProps {
  status: Status
  onAct: (verb: string, arg?: string) => void
}

export function DjSet({ status, onAct }: DjSetProps) {
  const [crossfade, setCrossfade] = useState(0) // -100 (Deck A) to 100 (Deck B)
  const [volA, setVolA] = useState(100)
  const [volB, setVolB] = useState(100)
  const [eqHiA, setEqHiA] = useState(0)
  const [eqMidA, setEqMidA] = useState(0)
  const [eqLowA, setEqLowA] = useState(0)
  const [eqHiB, setEqHiB] = useState(0)
  const [eqMidB, setEqMidB] = useState(0)
  const [eqLowB, setEqLowB] = useState(0)
  const [pitchA, setPitchA] = useState(0) // % speed adjustment (-16 to +16)
  const [pitchB, setPitchB] = useState(0)
  const [scratchingA, setScratchingA] = useState(false)
  const [scratchingB, setScratchingB] = useState(false)
  const [bpm, setBpm] = useState(128.0)
  const [activeFx, setActiveFx] = useState<string | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)

  const playing = status.state === "playing"
  const currentTrack = status.queue[status.current_index] || {
    title: status.title || "Deck A (Master Track)",
    channel: status.channel || "DJ Deck A",
    url: status.url,
    duration: status.duration || 0,
  }
  const nextTrack = status.queue[status.current_index + 1] || null

  const pos = status.position || 0
  const dur = Math.max(1, Math.round(status.duration || 0))

  useEffect(() => {
    const baseBpm = 124.0 + ((status.current_index * 3.5) % 14)
    setBpm(Number((baseBpm * (status.speed || 1.0)).toFixed(1)))
  }, [status.current_index, status.speed])

  // Realistic Web Audio API vinyl scratch sound generator
  const triggerScratchSound = useCallback((pitchMultiplier = 1.0) => {
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        )()
      }
      const ctx = audioCtxRef.current
      if (ctx.state === "suspended") {
        ctx.resume()
      }

      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      const filter = ctx.createBiquadFilter()

      osc.type = "sawtooth"
      filter.type = "bandpass"
      filter.frequency.setValueAtTime(1100 * pitchMultiplier, ctx.currentTime)
      filter.Q.setValueAtTime(4.0, ctx.currentTime)

      const now = ctx.currentTime
      osc.frequency.setValueAtTime(250 * pitchMultiplier, now)
      osc.frequency.exponentialRampToValueAtTime(1500 * pitchMultiplier, now + 0.08)
      osc.frequency.exponentialRampToValueAtTime(160 * pitchMultiplier, now + 0.22)

      gain.gain.setValueAtTime(0.4, now)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25)

      osc.connect(filter)
      filter.connect(gain)
      gain.connect(ctx.destination)

      osc.start(now)
      osc.stop(now + 0.26)
    } catch (e) {
      console.warn("Web Audio Scratch failed:", e)
    }
  }, [])

  const handleScratchA = () => {
    setScratchingA(true)
    triggerScratchSound(1.0)
    onAct("dj", "scratch")
    setTimeout(() => setScratchingA(false), 350)
  }

  const handleScratchB = () => {
    setScratchingB(true)
    triggerScratchSound(1.2)
    onAct("dj", "scratch")
    setTimeout(() => setScratchingB(false), 350)
  }

  const triggerFx = (name: string) => {
    setActiveFx(name)
    triggerScratchSound(name === "bass" ? 0.7 : 1.1)
    if (name === "drop") {
      onAct("dj", "effect")
    } else if (name === "bass") {
      onAct("eq", "bass")
    } else if (name === "crossfade") {
      setCrossfade(100)
      setTimeout(() => {
        onAct("next")
        setCrossfade(0)
      }, 700)
    } else if (name === "stutter") {
      onAct("seek", String(Math.max(0, pos - 2)))
    } else if (name === "mix") {
      onAct("dj", "")
    }
    setTimeout(() => setActiveFx(null), 1200)
  }

  return (
    <Card className="relative overflow-hidden border-2 border-emerald-500/50 bg-zinc-950 p-4 text-emerald-400 backdrop-blur-2xl shadow-2xl shadow-emerald-500/30 font-mono select-none">
      {/* Top Console Branding & System Bar */}
      <div className="mb-4 flex flex-col md:flex-row items-center justify-between border-b-2 border-emerald-500/30 pb-3 gap-2">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-xl bg-emerald-500/20 border border-emerald-500/40 shadow-inner">
            <Disc3 className={cn("size-6 text-emerald-400", playing && "animate-spin text-emerald-300")} />
          </div>
          <div>
            <div className="flex items-center gap-2 text-base md:text-lg font-black tracking-widest text-emerald-400">
              <span>PRO DJ SYSTEM</span>
              <span className="text-xs font-bold px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-300">
                PIONEER / TECHNICS DUAL DECK
              </span>
            </div>
            <div className="text-[11px] font-semibold text-emerald-300/70">
              REAL-TIME BEATMATCHING · 2-CHANNEL MIXER · AUDIO FX DROPS
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 rounded-xl bg-zinc-900 px-3 py-1.5 border border-emerald-500/30">
            <Radio className="size-4 text-emerald-400 animate-pulse" />
            <span className="text-xs font-black text-emerald-300">BEAT GRID: {bpm} BPM</span>
          </div>
          <Button
            size="sm"
            variant="destructive"
            className="h-8 px-4 text-xs font-black shadow-lg"
            onClick={() => onAct("dj", "off")}
          >
            ⏹️ EXIT DJ MODE
          </Button>
        </div>
      </div>

      {/* Main DJ Console Grid: DECK A (Left) | MIXER (Center) | DECK B (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-stretch">
        
        {/* ---------------- DECK A (LEFT PLAYER) ---------------- */}
        <div className="lg:col-span-5 flex flex-col items-center rounded-2xl border-2 border-emerald-500/40 bg-zinc-900/90 p-4 relative shadow-2xl">
          <div className="mb-3 flex w-full items-center justify-between border-b border-emerald-500/20 pb-2">
            <div className="flex items-center gap-2">
              <span className="size-3 rounded-full bg-emerald-500 shadow-sm shadow-emerald-400 animate-pulse" />
              <span className="text-sm font-black text-emerald-300 tracking-wider">DECK A · MASTER</span>
            </div>
            <span className={cn("text-xs font-extrabold px-2.5 py-0.5 rounded border", playing ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" : "bg-zinc-800 text-zinc-400 border-zinc-700")}>
              {playing ? "▶ PLAYING" : "⏸ PAUSED"}
            </span>
          </div>

          {/* Large 12" Vinyl Platter with Pitch Strobe Dots */}
          <div
            onClick={handleScratchA}
            className={cn(
              "relative size-44 md:size-52 rounded-full bg-zinc-950 p-3 shadow-2xl border-8 border-zinc-800 cursor-pointer transition-transform active:scale-95 group",
              scratchingA && "rotate-12 scale-105 shadow-emerald-500/60 border-emerald-500/80",
            )}
          >
            {/* Outer Pitch Strobe Rim */}
            <div className="absolute inset-1 rounded-full border-2 border-dashed border-zinc-700/60" />

            {/* Grooved Vinyl Platter */}
            <div
              className={cn(
                "relative size-full rounded-full border border-zinc-700/60 bg-[radial-gradient(circle,_#18181b_25%,_#09090b_75%)] flex items-center justify-center shadow-inner",
                playing && !scratchingA && "animate-[spin_2.8s_linear_infinite]",
              )}
            >
              {/* Vinyl Groove Rings */}
              <div className="absolute inset-3 rounded-full border border-zinc-800" />
              <div className="absolute inset-6 rounded-full border border-zinc-800/80" />
              <div className="absolute inset-9 rounded-full border border-zinc-800/60" />
              <div className="absolute inset-12 rounded-full border border-zinc-800/40" />

              {/* Center Album Art Sticker */}
              <img
                src={art(currentTrack.url) || undefined}
                alt=""
                className="size-16 md:size-20 rounded-full object-cover border-2 border-emerald-400 shadow-xl"
              />
              <div className="absolute size-4 rounded-full bg-zinc-950 border-2 border-zinc-700 shadow-md" />
            </div>

            {/* DJ Tonearm */}
            <div
              className={cn(
                "absolute -top-2 -right-2 h-24 w-1.5 origin-top bg-gradient-to-b from-zinc-300 to-zinc-500 rounded-full transition-transform duration-500 shadow-2xl",
                playing ? "rotate-[32deg]" : "rotate-0",
              )}
            >
              <div className="absolute -bottom-1 -left-1.5 size-4 rounded bg-emerald-400 shadow-lg shadow-emerald-400 border border-emerald-300" />
            </div>
          </div>
          <div className="mt-1 text-[10px] font-bold text-emerald-400/70">CLICK PLATTER TO SCRATCH ⚡</div>

          {/* Deck A Track Title & Meta */}
          <div className="mt-3 w-full text-center">
            <div className="truncate text-base font-extrabold text-emerald-300">
              {currentTrack.title || "Deck A Master Track"}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {currentTrack.channel || "Pioneer CDJ-3000 Channel 1"}
            </div>
          </div>

          {/* Deck A Progress Bar & Timers */}
          <div className="mt-3 w-full">
            <Slider
              value={[Math.min(pos, dur)]}
              min={0}
              max={dur}
              step={1}
              onValueCommit={([v]) => act("seek", String(v))}
              className="accent-emerald-400"
            />
            <div className="mt-1 flex justify-between text-xs tabular-nums font-bold text-emerald-400/80">
              <span>{fmt(pos)}</span>
              <span>-{fmt(Math.max(0, dur - pos))}</span>
            </div>
          </div>

          {/* Deck A Controls & Vertical Pitch Fader */}
          <div className="mt-4 flex w-full items-center justify-between gap-3">
            <div className="flex flex-1 gap-2">
              <Button
                size="sm"
                variant="secondary"
                className="h-10 px-3 font-extrabold text-xs border border-emerald-500/30"
                onClick={() => onAct("seek", "0")}
              >
                CUE
              </Button>
              <Button
                size="sm"
                variant={playing ? "default" : "secondary"}
                className="h-10 flex-1 font-extrabold text-xs bg-emerald-500 text-zinc-950 hover:bg-emerald-400 shadow-lg"
                onClick={() => onAct("toggle")}
              >
                {playing ? <Pause className="mr-1.5 size-4 fill-current" /> : <Play className="mr-1.5 size-4 fill-current" />}
                {playing ? "PAUSE" : "PLAY"}
              </Button>
            </div>

            {/* Deck A Pitch Fader Slider */}
            <div className="flex flex-col items-center bg-zinc-950 p-2 rounded-xl border border-zinc-800 text-[10px] font-bold">
              <span className="text-emerald-400">PITCH</span>
              <input
                type="range"
                min="-16"
                max="16"
                value={pitchA}
                onChange={(e) => {
                  const val = Number(e.target.value)
                  setPitchA(val)
                  onAct("speed", String(1 + val / 100))
                }}
                className="w-16 h-2 accent-emerald-400 cursor-pointer my-1"
              />
              <span className="text-emerald-300">{pitchA >= 0 ? `+${pitchA}%` : `${pitchA}%`}</span>
            </div>
          </div>
        </div>

        {/* ---------------- CENTER MIXER CONSOLE ---------------- */}
        <div className="lg:col-span-2 flex flex-col items-center justify-between rounded-2xl border-2 border-emerald-500/50 bg-zinc-900/95 p-3 shadow-2xl">
          <div className="w-full text-center text-xs font-black tracking-widest text-emerald-400 border-b border-emerald-500/30 pb-2">
            2-CHANNEL DJ MIXER
          </div>

          {/* 3-Band Equalizer Rotary Controls */}
          <div className="my-2 grid grid-cols-2 gap-2 w-full text-center text-[10px] font-bold">
            <div className="flex flex-col gap-1.5 items-center bg-zinc-950 p-2 rounded-xl border border-zinc-800">
              <span className="text-emerald-400">CH A EQ</span>
              <label className="text-[9px] text-muted-foreground">HI: {eqHiA}dB</label>
              <input type="range" min="-12" max="12" value={eqHiA} onChange={(e) => setEqHiA(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
              <label className="text-[9px] text-muted-foreground">MID: {eqMidA}dB</label>
              <input type="range" min="-12" max="12" value={eqMidA} onChange={(e) => setEqMidA(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
              <label className="text-[9px] text-muted-foreground">LOW: {eqLowA}dB</label>
              <input type="range" min="-12" max="12" value={eqLowA} onChange={(e) => setEqLowA(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
            </div>

            <div className="flex flex-col gap-1.5 items-center bg-zinc-950 p-2 rounded-xl border border-zinc-800">
              <span className="text-emerald-400">CH B EQ</span>
              <label className="text-[9px] text-muted-foreground">HI: {eqHiB}dB</label>
              <input type="range" min="-12" max="12" value={eqHiB} onChange={(e) => setEqHiB(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
              <label className="text-[9px] text-muted-foreground">MID: {eqMidB}dB</label>
              <input type="range" min="-12" max="12" value={eqMidB} onChange={(e) => setEqMidB(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
              <label className="text-[9px] text-muted-foreground">LOW: {eqLowB}dB</label>
              <input type="range" min="-12" max="12" value={eqLowB} onChange={(e) => setEqLowB(Number(e.target.value))} className="w-12 h-1.5 accent-emerald-400 cursor-pointer" />
            </div>
          </div>

          {/* Stereo Dual LED VU Meters */}
          <div className="w-full bg-zinc-950 p-2 rounded-xl border border-zinc-800">
            <div className="mb-1 flex justify-between text-[9px] font-bold text-emerald-400">
              <span>VU L</span>
              <span>VU R</span>
            </div>
            <div className="flex h-16 justify-between gap-2 px-2">
              {/* Left VU Meter Tower */}
              <div className="flex flex-col-reverse gap-0.5 w-3/8 h-full">
                {Array.from({ length: 12 }).map((_, i) => {
                  const active = playing && (i < 7 || (i < 10 && Math.random() > 0.3) || Math.random() > 0.7)
                  return (
                    <div
                      key={i}
                      className={cn(
                        "h-1 rounded-xs transition-colors duration-75",
                        active
                          ? i > 9
                            ? "bg-red-500 shadow-sm shadow-red-500"
                            : i > 7
                            ? "bg-yellow-400"
                            : "bg-emerald-400"
                          : "bg-zinc-800/40",
                      )}
                    />
                  )
                })}
              </div>

              {/* Right VU Meter Tower */}
              <div className="flex flex-col-reverse gap-0.5 w-3/8 h-full">
                {Array.from({ length: 12 }).map((_, i) => {
                  const active = playing && (i < 7 || (i < 10 && Math.random() > 0.4) || Math.random() > 0.65)
                  return (
                    <div
                      key={i}
                      className={cn(
                        "h-1 rounded-xs transition-colors duration-75",
                        active
                          ? i > 9
                            ? "bg-red-500 shadow-sm shadow-red-500"
                            : i > 7
                            ? "bg-yellow-400"
                            : "bg-emerald-400"
                          : "bg-zinc-800/40",
                      )}
                    />
                  )
                })}
              </div>
            </div>
          </div>

          {/* Channel Faders & Main Crossfader */}
          <div className="mt-2 w-full bg-zinc-950 p-2.5 rounded-xl border border-zinc-800 text-center">
            <div className="mb-1 text-[10px] font-extrabold text-emerald-300">CROSSFADER</div>
            <input
              type="range"
              min="-100"
              max="100"
              value={crossfade}
              onChange={(e) => setCrossfade(Number(e.target.value))}
              className="h-3 w-full cursor-pointer appearance-none rounded bg-zinc-900 border border-emerald-500/40 accent-emerald-400"
            />
            <div className="mt-1 flex justify-between text-[9px] font-bold text-muted-foreground">
              <span>DECK A</span>
              <span>CENTER</span>
              <span>DECK B</span>
            </div>
          </div>
        </div>

        {/* ---------------- DECK B (RIGHT PLAYER) ---------------- */}
        <div className="lg:col-span-5 flex flex-col items-center rounded-2xl border-2 border-emerald-500/40 bg-zinc-900/90 p-4 relative shadow-2xl">
          <div className="mb-3 flex w-full items-center justify-between border-b border-emerald-500/20 pb-2">
            <div className="flex items-center gap-2">
              <span className="size-3 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400 animate-pulse" />
              <span className="text-sm font-black text-emerald-300 tracking-wider">DECK B · CUED NEXT</span>
            </div>
            <span className="text-xs font-extrabold px-2.5 py-0.5 rounded border bg-emerald-500/10 text-emerald-300 border-emerald-500/30">
              {nextTrack ? "READY TO DROP" : "EMPTY QUEUE"}
            </span>
          </div>

          {/* Large 12" Vinyl Platter with Pitch Strobe Dots */}
          <div
            onClick={handleScratchB}
            className={cn(
              "relative size-44 md:size-52 rounded-full bg-zinc-950 p-3 shadow-2xl border-8 border-zinc-800 cursor-pointer transition-transform active:scale-95 group",
              scratchingB && "rotate-12 scale-105 shadow-emerald-500/60 border-emerald-500/80",
            )}
          >
            {/* Outer Pitch Strobe Rim */}
            <div className="absolute inset-1 rounded-full border-2 border-dashed border-zinc-700/60" />

            {/* Grooved Vinyl Platter */}
            <div
              className={cn(
                "relative size-full rounded-full border border-zinc-700/60 bg-[radial-gradient(circle,_#18181b_25%,_#09090b_75%)] flex items-center justify-center shadow-inner",
                nextTrack && playing && !scratchingB && "animate-[spin_3.5s_linear_infinite]",
              )}
            >
              <div className="absolute inset-3 rounded-full border border-zinc-800" />
              <div className="absolute inset-6 rounded-full border border-zinc-800/80" />
              <div className="absolute inset-9 rounded-full border border-zinc-800/60" />
              <div className="absolute inset-12 rounded-full border border-zinc-800/40" />

              {/* Center Album Art Sticker */}
              <img
                src={nextTrack ? art(nextTrack.url) || undefined : undefined}
                alt=""
                className="size-16 md:size-20 rounded-full object-cover border-2 border-emerald-400 shadow-xl bg-secondary"
              />
              <div className="absolute size-4 rounded-full bg-zinc-950 border-2 border-zinc-700 shadow-md" />
            </div>

            {/* DJ Tonearm */}
            <div className="absolute -top-2 -right-2 h-24 w-1.5 origin-top bg-gradient-to-b from-zinc-300 to-zinc-500 rounded-full rotate-0 shadow-2xl">
              <div className="absolute -bottom-1 -left-1.5 size-4 rounded bg-zinc-600 border border-zinc-500" />
            </div>
          </div>
          <div className="mt-1 text-[10px] font-bold text-emerald-400/70">CLICK PLATTER TO SCRATCH ⚡</div>

          {/* Deck B Track Title & Meta */}
          <div className="mt-3 w-full text-center">
            <div className="truncate text-base font-extrabold text-emerald-300">
              {nextTrack ? nextTrack.title : "No Cued Track in Queue"}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {nextTrack ? (nextTrack.channel || "Pioneer CDJ-3000 Channel 2") : "Click DJ Session below to load tracks"}
            </div>
          </div>

          {/* Deck B Beat Sync Banner */}
          <div className="mt-3 w-full rounded-xl bg-zinc-950 p-2.5 text-center text-xs font-extrabold text-emerald-300 border border-zinc-800 shadow-inner">
            {nextTrack ? `⚡ AUTOMATIC BEAT MATCHED @ ${bpm} BPM` : "SMART QUEUE READY TO AUTO-SYNC"}
          </div>

          {/* Deck B Controls & Vertical Pitch Fader */}
          <div className="mt-4 flex w-full items-center justify-between gap-3">
            <Button
              size="sm"
              variant="default"
              className="h-10 flex-1 font-extrabold text-xs bg-emerald-500 text-zinc-950 hover:bg-emerald-400 shadow-lg"
              onClick={() => onAct("next")}
            >
              <SkipForward className="mr-1.5 size-4" /> DROP DECK B NOW ⏩
            </Button>

            {/* Deck B Pitch Fader Slider */}
            <div className="flex flex-col items-center bg-zinc-950 p-2 rounded-xl border border-zinc-800 text-[10px] font-bold">
              <span className="text-emerald-400">PITCH</span>
              <input
                type="range"
                min="-16"
                max="16"
                value={pitchB}
                onChange={(e) => setPitchB(Number(e.target.value))}
                className="w-16 h-2 accent-emerald-400 cursor-pointer my-1"
              />
              <span className="text-emerald-300">{pitchB >= 0 ? `+${pitchB}%` : `${pitchB}%`}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Heavy-Duty DJ Drops & Audio FX Control Board */}
      <div className="mt-4 rounded-2xl border-2 border-emerald-500/50 bg-zinc-900/95 p-4 shadow-2xl">
        <div className="mb-3 flex items-center justify-between text-xs font-black tracking-widest text-emerald-300">
          <div className="flex items-center gap-2">
            <Flame className="size-4 text-emerald-400" />
            <span>INSTANT DJ SOUND DROPS & AUDIO FX TRIGGER BOARD</span>
          </div>
          <span className="text-[10px] text-emerald-400/80">REAL-TIME MPV FILTER ENGINES</span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2.5">
          <Button
            size="sm"
            variant="secondary"
            className={cn(
              "h-11 text-xs font-black border-2 border-emerald-500/40 active:scale-95 transition-all shadow-md",
              activeFx === "scratch" && "bg-emerald-500 text-zinc-950 border-emerald-400",
            )}
            onClick={handleScratchA}
          >
            <Flame className="mr-1.5 size-4 text-emerald-400" /> ⚡ SCRATCH
          </Button>

          <Button
            size="sm"
            variant="secondary"
            className={cn(
              "h-11 text-xs font-black border-2 border-emerald-500/40 active:scale-95 transition-all shadow-md",
              activeFx === "bass" && "bg-emerald-500 text-zinc-950 border-emerald-400",
            )}
            onClick={() => triggerFx("bass")}
          >
            <Zap className="mr-1.5 size-4 text-emerald-400" /> 🔥 BASS DROP
          </Button>

          <Button
            size="sm"
            variant="secondary"
            className={cn(
              "h-11 text-xs font-black border-2 border-emerald-500/40 active:scale-95 transition-all shadow-md",
              activeFx === "drop" && "bg-emerald-500 text-zinc-950 border-emerald-400",
            )}
            onClick={() => triggerFx("drop")}
          >
            <Sliders className="mr-1.5 size-4 text-emerald-400" /> 🎚️ FILTER DROP
          </Button>

          <Button
            size="sm"
            variant="secondary"
            className={cn(
              "h-11 text-xs font-black border-2 border-emerald-500/40 active:scale-95 transition-all shadow-md",
              activeFx === "stutter" && "bg-emerald-500 text-zinc-950 border-emerald-400",
            )}
            onClick={() => triggerFx("stutter")}
          >
            <RotateCcw className="mr-1.5 size-4 text-emerald-400" /> 🔄 STUTTER CUE
          </Button>

          <Button
            size="sm"
            variant="default"
            className="h-11 text-xs font-black bg-emerald-500 text-zinc-950 hover:bg-emerald-400 active:scale-95 transition-all shadow-lg"
            onClick={() => triggerFx("crossfade")}
          >
            ⏩ FADE NEXT
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-11 text-xs font-black border-2 border-emerald-500/60 text-emerald-400 hover:bg-emerald-500/20 active:scale-95 transition-all shadow-md"
            onClick={() => triggerFx("mix")}
          >
            🎲 DJ SESSION
          </Button>
        </div>
      </div>
    </Card>
  )
}
