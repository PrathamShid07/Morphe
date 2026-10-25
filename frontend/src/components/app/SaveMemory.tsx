import { useState, type FormEvent } from "react"
import { toast } from "sonner"
import { BookmarkPlus, ChevronDown, Loader2, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { errorMessage } from "@/components/app/states"
import { useDeleteMyMemory, useMyMemories, useSaveMemory } from "@/api/hooks"
import { timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

const MAX = 20_000

/** "Save to My Memory": an explicit, deliberate action. Nothing is saved automatically. Ownership is decided by the server from the signed-in session. */
export function SaveMemory() {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const [title, setTitle] = useState("")
  const [topic, setTopic] = useState("")
  const [error, setError] = useState<string | null>(null)
  const save = useSaveMemory()
  const mine = useMyMemories(5)
  const del = useDeleteMyMemory()
  const busy = save.isPending

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim() || busy) return
    setError(null)
    save.mutate({ text, title: title.trim() || undefined, topic: topic.trim() || undefined }, {
      onSuccess: (r) => {
        if (r.status === "duplicate") toast.info("You already saved this exact text.")
        else toast.success(`Saved to your private memory (${r.chunks_created} ${r.chunks_created === 1 ? "piece" : "pieces"}${r.new_module_ids.length ? `, ${r.new_module_ids.length} new topic${r.new_module_ids.length === 1 ? "" : "s"}` : ""}).`)
        setText(""); setTitle(""); setTopic("")
      },
      onError: (err) => setError(errorMessage(err).message),
    })
  }

  return (
    <section className="surface space-y-3 p-4" aria-label="Save to my memory">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button type="button" className="flex min-h-11 w-full items-center gap-3 text-left" aria-expanded={open}>
            <span className="tile-icon size-9"><BookmarkPlus className="size-4" /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold">Save to My Memory</span>
              <span className="block text-xs text-muted-foreground">Add something you want MORPHE to remember. It stays private to your account.</span>
            </span>
            <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <form onSubmit={submit} className="mt-3 space-y-3" aria-label="Add a memory">
            <div className="space-y-1.5">
              <Label htmlFor="memory-text">What should MORPHE remember?</Label>
              <Textarea id="memory-text" value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={MAX} placeholder="Paste or type notes, facts, decisions, snippets…" className="min-h-28" />
              <p className="text-right text-xs tabular-nums text-muted-foreground">{text.length.toLocaleString()} / {MAX.toLocaleString()}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="memory-title">Title or source label <span className="text-muted-foreground">(optional)</span></Label>
                <Input id="memory-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} placeholder="e.g. Project Atlas notes" className="h-11 rounded-xl" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="memory-topic">Topic <span className="text-muted-foreground">(optional)</span></Label>
                <Input id="memory-topic" value={topic} onChange={(e) => setTopic(e.target.value)} maxLength={120} placeholder="Leave empty to file it automatically" className="h-11 rounded-xl" />
              </div>
            </div>
            {error && <p role="alert" className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm">{error}</p>}
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={!text.trim() || busy} className="h-11 min-w-36 gap-2 rounded-xl">
                {busy ? <Loader2 className="size-4 animate-spin" /> : <BookmarkPlus className="size-4" />}{busy ? "Saving…" : "Save to My Memory"}
              </Button>
              <p className="text-xs text-muted-foreground">Private by default. Only saved when you press the button.</p>
            </div>
          </form>
        </CollapsibleContent>
      </Collapsible>

      {mine.data && mine.data.total > 0 && (
        <div className="space-y-2 border-t pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recently saved ({mine.data.total})</h3>
          <ul className="space-y-1.5" aria-label="Recently saved memories">
            {mine.data.items.map((m) => (
              <li key={m.memory_id} className="flex items-start gap-2 rounded-xl border px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-2 break-words">{m.text_preview}</p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">{m.module_name ?? "Memory"} · {m.source_name} · {timeAgo(m.created_at)}</p>
                </div>
                <Button type="button" variant="ghost" size="icon" className="size-9 shrink-0 text-muted-foreground" aria-label={`Delete memory: ${m.text_preview.slice(0, 40)}`}
                  disabled={del.isPending} onClick={() => del.mutate(m.memory_id, { onSuccess: () => toast.success("Memory deleted"), onError: (e) => toast.error(errorMessage(e).message) })}>
                  <Trash2 className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
