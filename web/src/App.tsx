import { useCallback, useEffect, useState } from "react"
import {
  ChevronDown,
  ChevronUp,
  Heart,
  ListMusic,
  Loader2,
  Mic,
  Pause,
  Play,
  Search,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  api,
  art,
  fetchStatus,
  fmt,
  loadPin,
  PinRequired,
  send,
  setPin,
  thumb,
  type Status,
  type Track,
} from "@/lib/api"
import { DjSet } from "@/components/DjSet"
import { cn } from "@/lib/utils"

const DEMON_SLAYER_STATUS = {
  playing: "⚔️ TOTAL FOCUS",
  paused: "🗡️ SHEATHED",
  loading: "⚡ TRAINING",
  idle: "👺 STANDBY",
} as const

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pinPrompt, setPinPrompt] = useState(false)
  const [pinValue, setPinValue] = useState("")
  const [dragging, setDragging] = useState(false)
  const [seekPos, setSeekPos] = useState(0)

  useEffect(() => {
    loadPin()
    refresh()
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchStatus())
      setError(null)
    } catch (e) {
      if (e instanceof PinRequired) setPinPrompt(true)
      else if (e instanceof Error) setError(e.message)
    }
  }, [])

  const act = useCallback(
    async (verb: string, arg = "") => {
      try {
        if (verb === "dj") {
          const nextDjMode = arg !== "off"
          setStatus((prev) => (prev ? { ...prev, dj_mode: nextDjMode } : prev))
        }
        await send(verb, arg)
        await refresh()
      } catch (e) {
        if (e instanceof Error) setError(e.message)
      }
    },
    [refresh],
  )

  const savePin = async () => {
    setPin(pinValue)
    setPinPrompt(false)
    setError(null)
    await refresh()
  }

  const pos = dragging ? seekPos : status?.position || 0
  const dur = Math.max(1, Math.round(status?.duration || 0))
  const playing = status?.state === "playing" || status?.state === "loading"
  const dsStatus = DEMON_SLAYER_STATUS[status?.state as keyof typeof DEMON_SLAYER_STATUS] || "👺 STANDBY"

  return (
    <div className={cn("mx-auto flex min-h-dvh flex-col px-4 pb-16 pt-6 font-mono selection:bg-emerald-500/30 transition-all duration-300", status?.dj_mode ? "max-w-6xl" : "max-w-md")}>
      {/* header */}
      <header className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex size-9 items-center justify-center rounded-xl bg-emerald-500/20 text-base shadow-sm shadow-emerald-500/30">
            ⚔️
          </div>
          <div className="flex flex-col">
            <span className="text-lg font-bold tracking-tight text-emerald-400">Skye Player</span>
            <span className="text-[10px] font-semibold tracking-widest text-emerald-300/80">DEMON SLAYER CORPS</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={status?.dj_mode ? "default" : "outline"}
            className={cn(
              "h-7 gap-1 px-2 text-[11px] font-extrabold border-emerald-500/40 transition-all",
              status?.dj_mode
                ? "bg-emerald-500 text-zinc-950 hover:bg-emerald-400 shadow-md shadow-emerald-500/30"
                : "text-emerald-400 hover:bg-emerald-500/10"
            )}
            onClick={() => act("dj", status?.dj_mode ? "off" : "")}
          >
            🎧 {status?.dj_mode ? "DJ ON" : "DJ MODE"}
          </Button>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className={cn(
                "size-2 rounded-full",
                playing ? "animate-pulse bg-emerald-400 shadow-sm shadow-emerald-400" : status ? "bg-muted-foreground/50" : "animate-pulse bg-muted-foreground/50",
              )}
            />
            <span className="text-[11px] font-semibold text-emerald-300">{dsStatus}</span>
          </div>
        </div>
      </header>

      {error && (
        <div className="mb-3 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {!status ? (
        <div className="space-y-4">
          <Skeleton className="mx-auto size-36 rounded-2xl" />
          <Skeleton className="mx-auto h-5 w-40" />
          <Skeleton className="mx-auto h-4 w-28" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="mx-auto h-16 w-72 rounded-full" />
        </div>
      ) : status.dj_mode ? (
        <div className="mb-4">
          <DjSet status={status} onAct={act} />
        </div>
      ) : (
        <>

          {/* now playing */}
          <Card className="border-emerald-500/30 bg-card/60 p-6 pt-8 backdrop-blur-xl shadow-lg shadow-emerald-500/10">
            <div className="relative mx-auto w-fit">
              <div className="absolute -inset-10 rounded-full bg-emerald-500/20 blur-3xl" />
              <img
                src={art(status.url) || undefined}
                alt=""
                className="relative size-36 rounded-2xl border border-emerald-500/30 object-cover shadow-2xl"
              />
            </div>
            <div className="mt-6 text-center">
              <div className="truncate text-lg font-semibold tracking-tight">
                {status.title || "nothing playing"}
              </div>
              <div className="mt-0.5 truncate text-sm text-muted-foreground">
                {status.channel ||
                  (status.speed && status.speed !== 1 ? `${status.speed}×` : "") ||
                  " "}
              </div>
            </div>

            <div className="mt-5">
              <Slider
                value={[Math.min(pos, dur)]}
                min={0}
                max={dur}
                step={1}
                disabled={!status.duration}
                onValueChange={([v]) => {
                  setDragging(true)
                  setSeekPos(v)
                }}
                onValueCommit={([v]) => {
                  act("seek", String(v))
                  setDragging(false)
                }}
              />
              <div className="mt-1.5 flex justify-between text-[11px] tabular-nums text-muted-foreground">
                <span>{fmt(pos)}</span>
                <span>{fmt(status.duration)}</span>
              </div>
            </div>

            <div className="mt-5 flex items-center justify-center gap-6">
              <Button
                variant="ghost"
                size="icon"
                className="size-13"
                onClick={() => act("prev")}
                aria-label="Previous"
              >
                <SkipBack className="size-6" />
              </Button>
              <Button
                size="icon"
                className="size-17 rounded-full shadow-lg shadow-primary/30"
                onClick={() => act("toggle")}
                aria-label="Play / pause"
              >
                {playing ? (
                  <Pause className="size-7 fill-current" />
                ) : (
                  <Play className="ml-0.5 size-7 fill-current" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-13"
                onClick={() => act("next")}
                aria-label="Next"
              >
                <SkipForward className="size-6" />
              </Button>
            </div>

            <div className="mt-5 flex items-center justify-center gap-3">
              <Button variant="secondary" size="icon" onClick={() => act("volume", "-5")} aria-label="Volume down">
                <VolumeX className="size-4" />
              </Button>
              <Button
                variant={status.fav ? "default" : "secondary"}
                size="icon"
                onClick={() => act("fav")}
                aria-label="Favorite"
              >
                <Heart className={cn("size-4", status.fav && "fill-current")} />
              </Button>
              <Button variant="secondary" size="icon" onClick={() => act("volume", "+5")} aria-label="Volume up">
                <Volume2 className="size-4" />
              </Button>
            </div>
          </Card>

          <MoodRadio
            onAct={act}
            mood={status.mood ?? null}
            lang={status.mood_lang ?? null}
            artist={status.mood_artist ?? null}
          />

          <div className="mt-4">
            <Tabs defaultValue="queue">
              <TabsList className="w-full">
                <TabsTrigger value="queue" className="flex-1">
                  <ListMusic className="size-4" />
                  Up Next · {status.queue_len}
                </TabsTrigger>
                <TabsTrigger value="lyrics" className="flex-1">
                  <Mic className="size-4" />
                  Lyrics
                </TabsTrigger>
                <TabsTrigger value="search" className="flex-1">
                  <Search className="size-4" />
                  Search
                </TabsTrigger>
              </TabsList>
              <QueueTab status={status} onAct={act} />
              <div className="mt-3">
                <TabsContent value="lyrics">
                  <LyricsTab status={status} />
                </TabsContent>
              </div>
              <SearchTab />
            </Tabs>
          </div>
        </>
      )}

      {/* pin overlay */}
      {pinPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm">
          <Card className="w-full max-w-xs p-6 text-center">
            <div className="text-base font-semibold">PIN required</div>
            <Input
              autoFocus
              type="password"
              inputMode="numeric"
              className="mt-4 text-center"
              placeholder="enter PIN"
              value={pinValue}
              onChange={(e) => setPinValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && savePin()}
            />
            <Button className="mt-4 w-full" onClick={savePin}>
              Unlock
            </Button>
          </Card>
        </div>
      )}
    </div>
  )
}

function MoodRadio({
  onAct,
  mood,
  lang,
  artist,
}: {
  onAct: (v: string, a?: string) => void
  mood: string | null
  lang: string | null
  artist: string | null
}) {
  const [radio, setRadio] = useState("")
  const moods = ["focus", "chill", "energetic", "sad", "party"]
  const refine = [lang, artist].filter(Boolean).join(" · ")
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <span>Mood</span>
        {mood && (
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-primary">
            🎧 {mood}
            {refine ? ` · ${refine}` : ""}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {moods.map((m) => (
          <Button
            key={m}
            size="sm"
            variant={mood === m ? "default" : "secondary"}
            onClick={() => onAct("mood", m)}
          >
            {m}
          </Button>
        ))}
        <Button size="sm" variant="secondary" onClick={() => onAct("discover")}>
          ✨ Discover
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onAct("dj")}>
          🎧 DJ Mode
        </Button>
      </div>
      <div className="mt-2 flex gap-2">
        <Input
          className="h-9"
          placeholder="radio: artist / song / genre"
          value={radio}
          onChange={(e) => setRadio(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && radio.trim()) {
              onAct("radio", radio.trim())
              setRadio("")
            }
          }}
        />
        <Button
          size="sm"
          className="h-9 shrink-0"
          disabled={!radio.trim()}
          onClick={() => {
            onAct("radio", radio.trim())
            setRadio("")
          }}
        >
          📻
        </Button>
      </div>
    </div>
  )
}

function QueueTab({ status, onAct }: { status: Status; onAct: (v: string, a?: string) => void }) {
  if (!status.queue.length) {
    return (
      <div className="py-12 text-center text-sm text-muted-foreground">
        Up Next is empty — play or search for a song.
      </div>
    )
  }
  return (
    <ScrollArea className="mt-3 h-[42vh]">
      <div className="space-y-0.5 pr-2">
        {status.queue.map((t, i) => (
          <QueueRow
            key={`${t.url}-${i}`}
            track={t}
            index={i}
            isCurrent={i === status.current_index}
            queueLen={status.queue_len}
            onAct={onAct}
          />
        ))}
      </div>
    </ScrollArea>
  )
}

function QueueRow({
  track,
  index,
  isCurrent,
  queueLen,
  onAct,
}: {
  track: Track
  index: number
  isCurrent: boolean
  queueLen: number
  onAct: (v: string, a?: string) => void
}) {
  return (
    <div className={cn("flex items-center gap-3 rounded-xl px-2 py-1.5", isCurrent && "bg-primary/10")}>
      <img
        src={thumb(track.url) || undefined}
        alt=""
        className="size-10 shrink-0 rounded-lg bg-secondary object-cover"
      />
      <div className="min-w-0 flex-1">
        <div className={cn("truncate text-sm", isCurrent && "font-semibold text-primary")}>
          {isCurrent && <span className="mr-1">▶</span>}
          {track.title}
        </div>
        {track.channel && (
          <div className="truncate text-xs text-muted-foreground">{track.channel}</div>
        )}
      </div>
      <div className="flex shrink-0 items-center">
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-muted-foreground"
          disabled={index === 0}
          onClick={() => onAct("move", `${index} ${index - 1}`)}
          aria-label="Move up"
        >
          <ChevronUp className="size-4" />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-muted-foreground"
          disabled={index === queueLen - 1}
          onClick={() => onAct("move", `${index + 2} ${index + 1}`)}
          aria-label="Move down"
        >
          <ChevronDown className="size-4" />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="size-7 text-muted-foreground"
          onClick={() => onAct("remove", String(index + 1))}
          aria-label="Remove"
        >
          <X className="size-4" />
        </Button>
      </div>
    </div>
  )
}

function SearchTab() {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Track[]>([])
  const [searching, setSearching] = useState(false)

  const doSearch = async () => {
    if (!query.trim() || searching) return
    setSearching(true)
    try {
      const data = await api<{ results: Track[] }>("search", query)
      setResults(data.results)
    } catch (e) {
      if (e instanceof Error) setResults([])
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="mt-3">
      <div className="relative">
        <Search className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="pl-10"
          placeholder="Search songs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
        />
      </div>
      <div className="mt-2">
        {searching && (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> searching…
          </div>
        )}
        {!searching && results.length > 0 && (
          <ScrollArea className="h-[42vh]">
            <div className="space-y-0.5 pr-2">
              {results.map((t) => (
                <SearchRow key={t.url} track={t} />
              ))}
            </div>
          </ScrollArea>
        )}
        {!searching && query && results.length === 0 && (
          <div className="py-10 text-center text-sm text-muted-foreground">
            {query ? "no results" : " "}
          </div>
        )}
      </div>
    </div>
  )
}

function SearchRow({ track }: { track: Track }) {
  return (
    <a
      href={`javascript:void(0)`}
      onClick={() => send("play", track.url).catch(() => {})}
      className="flex items-center gap-3 rounded-xl px-2 py-1.5 active:bg-accent"
    >
      <img
        src={thumb(track.url) || undefined}
        alt=""
        className="size-10 shrink-0 rounded-lg bg-secondary object-cover"
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm">{track.title}</div>
        {track.channel && (
          <div className="truncate text-xs text-muted-foreground">{track.channel}</div>
        )}
      </div>
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Play className="ml-0.5 size-3.5 fill-current" />
      </span>
    </a>
  )
}

function LyricsTab({ status }: { status: Status }) {
  const [lines, setLines] = useState<Array<{ start?: number; end?: number; text: string; synced: boolean }>>([])
  const [note, setNote] = useState<string>("")
  const [loading, setLoading] = useState(false)
  const [curIdx, setCurIdx] = useState(0)

  useEffect(() => {
    if (!status.url) return
    let active = true
    setLoading(true)
    api<{ lines: Array<{ start?: number; end?: number; text: string; synced: boolean }>; note?: string }>("lyrics")
      .then((data) => {
        if (!active) return
        setLines(data.lines || [])
        setNote(data.note || "")
      })
      .catch(() => {
        if (!active) return
        setLines([])
        setNote("lyrics unavailable")
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [status.url])

  const pos = status.position || 0
  const isSynced = lines.some((l) => l.synced && l.start !== undefined && l.start !== null)

  useEffect(() => {
    if (!isSynced || !lines.length) return
    let best = 0
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].start !== undefined && lines[i].start! <= pos) {
        best = i
      } else {
        break
      }
    }
    setCurIdx(best)
  }, [pos, isSynced, lines])

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> loading lyrics…
      </div>
    )
  }

  if (!lines.length) {
    return (
      <div className="py-12 text-center text-sm text-muted-foreground">
        {note || "no lyrics available for this track"}
      </div>
    )
  }

  return (
    <ScrollArea className="mt-3 h-[42vh]">
      <div className="space-y-3 px-1 text-center">
        {lines.map((l, i) => {
          const isActive = isSynced && i === curIdx
          return (
            <div
              key={`${i}-${l.text}`}
              className={cn(
                "py-1.5 text-sm transition-all duration-200",
                isActive
                  ? "scale-105 font-bold text-emerald-400 drop-shadow-[0_0_8px_rgba(52,211,153,0.5)]"
                  : isSynced && i < curIdx
                  ? "text-muted-foreground/35"
                  : "text-muted-foreground/80",
              )}
            >
              {l.text}
            </div>
          )
        })}
      </div>
    </ScrollArea>
  )
}
