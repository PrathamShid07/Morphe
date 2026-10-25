/**
 * Deterministic graph layout built ONLY from the real backend structure: hierarchy edges (source = parent, target = child) define trees;
 * nothing is inferred or invented. Each tree is laid out as a cluster (group on top, children in a grid beneath), clusters are packed into
 * rows sized to the canvas aspect ratio, and modules that have no parent and no children share one "no parent group" cluster.
 * Same input + same aspect bucket => identical positions, so the picture never jumps between refreshes.
 */
import type { GraphEdge, GraphNode } from "@/api/types"

export const NODE_W = 184
export const LEAF_H = 92
export const GROUP_H = 68
const H_GAP = 14
const V_GAP = 44
const ROW_GAP = 14
const PAD = 20
const CLUSTER_GAP = 32

export interface Placed { id: string; x: number; y: number; w: number; h: number }
export interface ClusterBox { id: string; x: number; y: number; w: number; h: number; label: string | null; rootId: string | null; members: number }
export interface GraphLayout { nodes: Map<string, Placed>; clusters: ClusterBox[]; width: number; height: number }

interface Tree { root: string; w: number; h: number; place: (x: number, y: number, out: Map<string, Placed>) => void; size: number }

export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[], aspect = 1.6): GraphLayout {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const children = new Map<string, string[]>(nodes.map((n) => [n.id, []]))
  const hasParent = new Set<string>()
  for (const e of edges) {
    if (e.data.relationType !== "hierarchy") continue
    if (!byId.has(e.source) || !byId.has(e.target) || e.source === e.target || hasParent.has(e.target)) continue  // one parent per module; ignore dangling edges
    children.get(e.source)!.push(e.target)
    hasParent.add(e.target)
  }
  const name = (id: string) => (byId.get(id)?.data.label ?? id).toLowerCase()
  for (const arr of children.values()) arr.sort((a, b) => name(a).localeCompare(name(b)) || a.localeCompare(b))

  const dims = (id: string) => ({ w: NODE_W, h: byId.get(id)!.data.isContainer ? GROUP_H : LEAF_H })
  const seen = new Set<string>()

  const build = (id: string, depthGuard: Set<string>): Tree => {
    seen.add(id)
    const kids = (children.get(id) ?? []).filter((k) => !depthGuard.has(k))  // cycle guard: a malformed graph cannot recurse forever
    const me = dims(id)
    if (kids.length === 0) {
      return { root: id, w: me.w, h: me.h, size: 1, place: (x, y, out) => out.set(id, { id, x, y, w: me.w, h: me.h }) }
    }
    const guard = new Set(depthGuard).add(id)
    const subtrees = kids.map((k) => build(k, guard))
    const allLeaves = subtrees.every((s) => s.size === 1)
    if (allLeaves) {  // children as a grid beneath the group
      const n = subtrees.length
      const cols = n <= 4 ? n : n <= 6 ? 3 : 4
      const rows = Math.ceil(subtrees.length / cols)
      const cw = Math.max(...subtrees.map((s) => s.w))
      const rh = Math.max(...subtrees.map((s) => s.h))
      const gridW = cols * cw + (cols - 1) * H_GAP
      const w = Math.max(me.w, gridW)
      const h = me.h + V_GAP + rows * rh + (rows - 1) * ROW_GAP
      return {
        root: id, w, h, size: 1 + subtrees.length,
        place: (x, y, out) => {
          out.set(id, { id, x: x + (w - me.w) / 2, y, w: me.w, h: me.h })
          const gx = x + (w - gridW) / 2
          subtrees.forEach((s, i) => s.place(gx + (i % cols) * (cw + H_GAP), y + me.h + V_GAP + Math.floor(i / cols) * (rh + ROW_GAP), out))
        },
      }
    }
    const rowW = subtrees.reduce((a, s) => a + s.w, 0) + (subtrees.length - 1) * H_GAP  // deeper trees: subtrees side by side
    const w = Math.max(me.w, rowW)
    const h = me.h + V_GAP + Math.max(...subtrees.map((s) => s.h))
    return {
      root: id, w, h, size: 1 + subtrees.reduce((a, s) => a + s.size, 0),
      place: (x, y, out) => {
        out.set(id, { id, x: x + (w - me.w) / 2, y, w: me.w, h: me.h })
        let cx = x + (w - rowW) / 2
        for (const s of subtrees) { s.place(cx, y + me.h + V_GAP, out); cx += s.w + H_GAP }
      },
    }
  }

  const roots = nodes.filter((n) => !hasParent.has(n.id)).map((n) => n.id)
  const trees: { tree: Tree; label: string | null }[] = []
  const singles: string[] = []
  for (const r of roots) {
    if ((children.get(r) ?? []).length === 0) singles.push(r)
    else trees.push({ tree: build(r, new Set()), label: null })
  }
  for (const n of nodes) if (!seen.has(n.id) && !singles.includes(n.id) && hasParent.has(n.id)) {  // members of a cycle with no root: still shown, as loose modules
    singles.push(n.id)
  }
  const sortedSingles = singles.sort((a, b) => name(a).localeCompare(name(b)) || a.localeCompare(b))
  if (sortedSingles.length === 1) trees.push({ tree: build(sortedSingles[0], new Set()), label: null })
  else if (sortedSingles.length > 1) {
    const subs = sortedSingles.map((id) => ({ id, ...dims(id) }))
    const cols = Math.min(subs.length, 4)
    const rows = Math.ceil(subs.length / cols)
    const rh = Math.max(...subs.map((s) => s.h))
    const w = cols * NODE_W + (cols - 1) * H_GAP
    const h = rows * rh + (rows - 1) * ROW_GAP
    trees.push({
      label: "No parent group", tree: {
        root: "", w, h, size: subs.length, place: (x, y, out) => subs.forEach((s, i) => out.set(s.id, { id: s.id, x: x + (i % cols) * (NODE_W + H_GAP), y: y + Math.floor(i / cols) * (rh + ROW_GAP), w: s.w, h: s.h })),
      },
    })
  }
  trees.sort((a, b) => b.tree.size - a.tree.size || (a.tree.root || "~").localeCompare(b.tree.root || "~"))

  // pack clusters into rows ("shelves"); try every natural row break and keep the packing that will be drawn LARGEST in a canvas of this aspect ratio
  const boxes = trees.map((t) => ({ ...t, bw: t.tree.w + PAD * 2, bh: t.tree.h + PAD * 2 + (t.label ? 22 : 0) }))
  const pack = (targetW: number) => {
    const out = new Map<string, Placed>()
    const clusters: ClusterBox[] = []
    let x = 0, y = 0, rowH = 0, maxX = 0
    for (const b of boxes) {
      if (x > 0 && x + b.bw > targetW) { x = 0; y += rowH + CLUSTER_GAP; rowH = 0 }
      b.tree.place(x + PAD, y + PAD + (b.label ? 22 : 0), out)
      clusters.push({ id: `cluster:${b.tree.root || "ungrouped"}`, x, y, w: b.bw, h: b.bh, label: b.label, rootId: b.tree.root || null, members: b.tree.size })
      x += b.bw + CLUSTER_GAP
      rowH = Math.max(rowH, b.bh)
      maxX = Math.max(maxX, x - CLUSTER_GAP)
    }
    return { nodes: out, clusters, width: maxX, height: y + rowH }
  }
  const widest = boxes.reduce((a, b) => Math.max(a, b.bw), 0)
  const candidates = new Set<number>([widest])
  let run = 0
  for (const b of boxes) { run += b.bw + CLUSTER_GAP; candidates.add(Math.max(widest, run - CLUSTER_GAP)) }
  let best = pack(widest), bestScale = -1
  for (const w of [...candidates].sort((a, b) => a - b)) {
    const l = pack(w)
    const scale = Math.min(Math.max(0.6, aspect) / l.width, 1 / l.height)  // on-screen scale in a canvas of height 1 and width `aspect`
    if (scale > bestScale + 1e-12) { best = l; bestScale = scale }
  }
  return best
}