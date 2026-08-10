export type Track = {
  query: string
  title: string
  url: string
  duration: number | null
  channel: string
}

export type Status = {
  state: "idle" | "loading" | "playing" | "paused"
  title: string | null
  channel: string
  url: string
  position: number
  duration: number | null
  volume: number
  repeat: string
  shuffle: boolean
  speed: number
  current_index: number
  queue_len: number
  queue: Track[]
  fav: boolean
  error: string | null
  sleep_remaining: number | null
}

export class PinRequired extends Error {}

let pin = ""
export function setPin(value: string) {
  pin = value
  localStorage.setItem("tune_pin", value)
}
export function loadPin(): string {
  pin = localStorage.getItem("tune_pin") || ""
  return pin
}

export async function api<T>(verb: string, arg = ""): Promise<T> {
  let url = `/api/${verb}?pin=${encodeURIComponent(pin)}`
  if (arg) url += `&arg=${encodeURIComponent(arg)}`
  const r = await fetch(url)
  if (r.status === 401) throw new PinRequired()
  const j = await r.json()
  if (!j.ok) throw new Error(j.error || "request failed")
  return j.data as T
}

export const fetchStatus = () => api<Status>("status")
export const send = (verb: string, arg = "") => api<{ title?: string }>(verb, arg)

export function videoId(url: string): string | null {
  const m = (url || "").match(/[?&]v=([\w-]{11})/)
  return m ? m[1] : null
}
export const thumb = (url: string) => {
  const v = videoId(url)
  return v ? `https://i.ytimg.com/vi/${v}/default.jpg` : ""
}
export const art = (url: string) => {
  const v = videoId(url)
  return v ? `https://i.ytimg.com/vi/${v}/hqdefault.jpg` : ""
}
export function fmt(sec: number | null | undefined): string {
  const s = Math.max(0, Math.floor(sec || 0))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}
