import { ShieldQuestion } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"

/** Plain statement of what is private and what administrators can see. It must stay in line with docs/USER_ACCOUNTS.md (audit policy). */
export function PrivacyText({ className }: { className?: string }) {
  return (
    <ul className={className ?? "list-disc space-y-2 pl-5 text-sm text-muted-foreground"}>
      <li><b className="text-foreground">Your saved memories are private by default.</b> Only your account can search them; other users never see them in answers or in the graph.</li>
      <li><b className="text-foreground">Questions run inside your own scope:</b> your private memories plus any knowledge an administrator has explicitly published as shared. Shared results are labelled “Shared”.</li>
      <li><b className="text-foreground">Administrators can see operational activity</b> — sign-ins, the questions you ask, what you save or delete, and whether each action succeeded — for running, supporting and auditing the service. They do not see the text of your memories unless they open it deliberately for a support or moderation reason, and each such access is recorded.</li>
      <li>Passwords are stored only as salted hashes. MORPHE does not claim to encrypt your memories at rest.</li>
    </ul>
  )
}

export function PrivacyNotice() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="h-10 justify-start gap-2 rounded-xl text-muted-foreground"><ShieldQuestion className="size-4" />Privacy &amp; activity</Button>
      </DialogTrigger>
      <DialogContent className="max-w-[calc(100%-1.5rem)] rounded-3xl sm:max-w-lg">
        <DialogHeader className="text-left">
          <DialogTitle>Privacy &amp; activity</DialogTitle>
          <DialogDescription>What stays private, and what administrators can see.</DialogDescription>
        </DialogHeader>
        <PrivacyText />
      </DialogContent>
    </Dialog>
  )
}
