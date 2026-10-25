import { createContext, useContext, useState, type ReactNode } from "react"
import { useForm, useWatch } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { FileUp, Loader2, Type } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useIngest, useIngestFile } from "@/api/hooks"
import { ErrorState } from "./states"

export const MAX_CHARS = 1_000_000
export const ingestSchema = z.object({
  source_name: z.string().trim().min(1, "Give the source a name").max(200, "Keep the name under 200 characters"),
  text: z.string().trim().min(1, "Paste some text to ingest").max(MAX_CHARS, `Text is limited to ${MAX_CHARS.toLocaleString()} characters`),
  topic: z.string().trim().max(60).optional(),
  parent_topic: z.string().trim().max(60).optional(),
  pinned: z.boolean(),
})
export type IngestForm = z.infer<typeof ingestSchema>

const Ctx = createContext<{ open: () => void }>({ open: () => {} })
export const useIngestDialog = () => useContext(Ctx)

export function IngestProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <Ctx.Provider value={{ open: () => setOpen(true) }}>
      {children}
      <IngestDialog open={open} onOpenChange={setOpen} />
    </Ctx.Provider>
  )
}

export function IngestDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const ingest = useIngest()
  const upload = useIngestFile()
  const [file, setFile] = useState<File | null>(null)
  const [fileTopic, setFileTopic] = useState("")
  const [share, setShare] = useState(false)  // publish to every user's searches; off = administrators only
  const form = useForm<IngestForm>({
    resolver: zodResolver(ingestSchema),
    defaultValues: { source_name: "", text: "", topic: "", parent_topic: "", pinned: false },
  })
  const busy = ingest.isPending || upload.isPending
  const error = ingest.error ?? upload.error

  const done = (r: { status: string; chunks_created: number; module_ids: string[]; message?: string | null; compression?: { triggered: boolean; state: string } | null }) => {
    if (r.status === "duplicate") toast.info("Already ingested", { description: r.message ?? "Identical content is already stored." })
    else
      toast.success(`Ingested ${r.chunks_created} memories`, {
        description: `Modules: ${r.module_ids.join(", ")}${r.compression?.triggered ? ` · compression ${r.compression.state}` : ""}`,
      })
    form.reset()
    setFile(null)
    setShare(false)
    ingest.reset()
    upload.reset()
    onOpenChange(false)
  }

  const submit = form.handleSubmit((v) => {
    const metadata: Record<string, unknown> = {}
    if (v.topic) metadata.topic = v.topic
    if (v.parent_topic) metadata.parent_topic = v.parent_topic
    ingest.mutate({ source_name: v.source_name, text: v.text, metadata, pinned: v.pinned, ...(share ? { visibility: "shared" as const } : {}) }, { onSuccess: done })
  })
  const text = useWatch({ control: form.control, name: "text" }) ?? ""
  const pinned = useWatch({ control: form.control, name: "pinned" })

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent className="max-h-[92dvh] max-w-[calc(100vw-1.5rem)] gap-4 overflow-y-auto rounded-2xl sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Ingest content</DialogTitle>
          <DialogDescription>Text is chunked, embedded locally and routed into memory modules. Nothing leaves your machine.</DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="text" className="gap-4">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="text"><Type className="size-4" /> Paste text</TabsTrigger>
            <TabsTrigger value="file"><FileUp className="size-4" /> Upload file</TabsTrigger>
          </TabsList>
          <TabsContent value="text">
            <form onSubmit={submit} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <Label htmlFor="source_name">Source name</Label>
                <Input id="source_name" placeholder="django_notes.md" aria-invalid={!!form.formState.errors.source_name} {...form.register("source_name")} />
                {form.formState.errors.source_name && <p role="alert" className="text-xs text-destructive">{form.formState.errors.source_name.message}</p>}
              </div>
              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between">
                  <Label htmlFor="text">Text</Label>
                  <span className="text-xs text-muted-foreground tabular-nums">{text.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}</span>
                </div>
                <Textarea id="text" rows={8} className="max-h-64 font-mono text-xs" placeholder="## Heading&#10;&#10;Paste notes, docs or code…" aria-invalid={!!form.formState.errors.text} {...form.register("text")} />
                {form.formState.errors.text && <p role="alert" className="text-xs text-destructive">{form.formState.errors.text.message}</p>}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="topic">Topic <span className="text-muted-foreground">(module, optional)</span></Label>
                  <Input id="topic" placeholder="django" {...form.register("topic")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="parent_topic">Group <span className="text-muted-foreground">(optional)</span></Label>
                  <Input id="parent_topic" placeholder="web-frameworks" {...form.register("parent_topic")} />
                </div>
              </div>
              <label className="flex items-center justify-between gap-3 rounded-xl border p-3 text-sm">
                <span>
                  <span className="font-medium">Pin these memories</span>
                  <span className="block text-xs text-muted-foreground">Pinned records are never deleted by compression.</span>
                </span>
                <Switch checked={pinned} onCheckedChange={(v) => form.setValue("pinned", v)} aria-label="Pin memories" />
              </label>
              <label className="flex items-center justify-between gap-3 rounded-xl border p-3 text-sm">
                <span>
                  <span className="font-medium">Share with all users</span>
                  <span className="block text-xs text-muted-foreground">Off: only administrators can search this. On: every signed-in user&apos;s questions can find it, labelled “Shared”.</span>
                </span>
                <Switch checked={share} onCheckedChange={setShare} aria-label="Share with all users" />
              </label>
              {error && <ErrorState error={error} />}
              <DialogFooter>
                <Button type="submit" disabled={busy} className="w-full sm:w-auto">
                  {ingest.isPending && <Loader2 className="size-4 animate-spin" />} Ingest
                </Button>
              </DialogFooter>
            </form>
          </TabsContent>
          <TabsContent value="file" className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="file">UTF-8 text, markdown or code file</Label>
              <Input id="file" type="file" accept=".txt,.md,.rst,.json,.py,.ts,.tsx,.js,.yaml,.yml,.toml,.csv,text/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              {file && <p className="text-xs text-muted-foreground">{file.name} · {(file.size / 1024).toFixed(1)} KB</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="file_topic">Topic <span className="text-muted-foreground">(optional)</span></Label>
              <Input id="file_topic" value={fileTopic} onChange={(e) => setFileTopic(e.target.value)} placeholder="django" />
            </div>
            <label className="flex items-center justify-between gap-3 rounded-xl border p-3 text-sm">
              <span>
                <span className="font-medium">Share with all users</span>
                <span className="block text-xs text-muted-foreground">Off: administrators only. On: published to every signed-in user&apos;s searches.</span>
              </span>
              <Switch checked={share} onCheckedChange={setShare} aria-label="Share uploaded file with all users" />
            </label>
            {error && <ErrorState error={error} />}
            <DialogFooter>
              <Button disabled={!file || busy} className="w-full sm:w-auto" onClick={() => file && upload.mutate({ file, topic: fileTopic || undefined, ...(share ? { visibility: "shared" as const } : {}) }, { onSuccess: done })}>
                {upload.isPending && <Loader2 className="size-4 animate-spin" />} Upload & ingest
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
