# UI Port Workflow — Porting rendering from external projects

When porting a rendering/UI subsystem from another open-source project into yours, use this class-level workflow. Validated on omnisense (porting Cardputer-CSI-Human-Detector PPI radar + 3D raycaster).

## Prerequisites (step 0)
Before any porting, check the target project's graphics API:
- Does the target have pixel/line/circle/fill primitives? If not, add them first.
- Is there an offscreen canvas (M5Canvas sprite)? Without one, PPI sweep and 3D raycaster will flicker.
- What's the RAM budget? 240×135 RGB565 = ~64KB. Measure `heap_caps_get_largest_free_block()`.
- Memory bottleneck pattern: always add canvas AFTER primitives and BEFORE view rendering.

## Step ordering
1. **Graphics primitives** — screen.h: pixel, line, circle, fill_circle, fill_triangle, vline_gradient + canvas (prerequisite for all views)
2. **View router** — dispatch by Tab key, keep existing views untouched
3. **Fake sensor** — inject data upstream of main pipeline (exercises real fusion/state machine)
4. **Palettes** — color themes consumed by all views
5. **Blips** — host-testable core module (spawn/merge/lifecycle)
6. **Primary view** — PPI radar (sweep + phosphor + blips + rings)
7. **Status enrichment** — motion graph + pills (adds to existing view, no rewrite)
8. **Bonus view** — 3D raycaster (canvas-required, skipped if unavailable)
9. **Secondary hardware** — external display, camera OUI (deferred, requires HW audit)

## Integration pattern
- Every step independently deliverable: tests green + firmware build + visual verification
- Source project's rendering code is ported as-is; only the DATA source changes (your data structures → their visual representation)
- Map your sensor data to their visual fields in a thin adapter layer

## Key risks
- Canvas allocation failure → fallback to direct draw (frame flickers, trail reduced)
- RAM: 64KB canvas + Bluedroid + WiFi STA → test `largest_free_block` early
- Font: Font0 is ASCII-only; all labels without accents
