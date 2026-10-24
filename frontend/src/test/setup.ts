import "@testing-library/jest-dom/vitest"
import { afterEach, vi } from "vitest"
import { cleanup } from "@testing-library/react"

afterEach(() => cleanup())

// jsdom lacks these browser APIs that React Flow, Radix and charts rely on
class RO { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal("ResizeObserver", RO)
class DOMMatrixRO { m22 = 1; constructor(_?: string) {} }
vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixRO)
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (q: string) => ({ matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }),
})
Element.prototype.scrollIntoView = vi.fn()
Element.prototype.hasPointerCapture = vi.fn(() => false) as never
Element.prototype.releasePointerCapture = vi.fn()
