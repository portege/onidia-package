# 🎨 ONIDIA Character Builder

A standalone drawing editor that turns freehand canvas strokes into a
complete **Go character package** for the [ONIDIA desktop pet](../onidia) —
no image assets, because ONIDIA draws every pixel procedurally at runtime.

This folder is fully self-contained (own Go module, stdlib only) and does
**not** modify any existing ONIDIA code.

---

## 1. What builds an ONIDIA character (analysis)

Characters live in `onidia/characters/<name>/` behind the
`characters.Character` interface (`Name`, `Draw`, `Phrases`), registered from
`init()` and drawn on a fixed **112 × 160 logical grid**
(`BaseW/BaseH`, `Cx = 56`, `GroundY = 156` — see `characters/characters.go`).

### 7 core body parts (draw order in `drawPet`, `characters/onidia/draw_body.go`)

| # | Part | Drawn by | What it contains |
|---|------|----------|------------------|
| 1 | **Ground shadow** | `drawPet` | ellipse that never bobs, shrinks on `Hop` |
| 2 | **Back hair** | `drawTwinTails` | twin tails behind the body (skipped under `Helmet`) |
| 3 | **Legs** | `drawLegs` | two legs + shoes, walk swing / foot lift |
| 4 | **Body** | `drawBody` | neck, dress, frill, collar |
| 5 | **Arms** | `drawArms` | two arms with puff sleeves |
| 6 | **Head** | `drawHead` | back hair mass, face circle, bangs, fringe, side locks, ahoge, ties |
| 7 | **Face** | `drawFace` | eyes, brows, nose, mouth, blush — assembled per expression |

### Plus 8 conditional action overlays (pose flags)

peace hand, dance arms, eat arms, sneeze arms, parachute grip arms,
six-seven arms, skateboard arms, wave arm.

### Supporting pieces per character

- **8 files** per package: `<name>.go` (registration), `grid.go`, `palette.go`,
  `draw_body.go`, `draw_head.go`, `face.go`, `face_parts.go`, `phrases.go`
- **16 shared expressions** (`Expr*`) switched inside `drawFace`
- **~24 palette roles** (`colHair`, `colSkin`, `colDress`, …)
- **~10 canvas primitives** (`FillCapsule`, `FillCircle`, `FillEllipse`,
  `FillPoly`, `FillTaper` + outlined variants — `canvas/canvas.go`)

**The builder exports the 7 core parts.** Action overlays and per-expression
faces are hand-written extensions (see Limitations).

---

## 2. Run the editor

```sh
cd character-builder
go run .                    # listens on http://localhost:8090
# or: BUILDER_ADDR=:3000 go run .
```

Open http://localhost:8090, then:

1. Pick a **layer** (body part) on the left — the layer stack matches the 7
   parts above, bottom-to-top.
2. Draw with the **pen** (brush size `[` `]`, `B`/`E` switch tools,
   `Ctrl+Z` undo). The grid shows the ONIDIA landmarks: vertical centre line
   `Cx=56`, ground line `y=156`, frame `112×160`.
3. Colour with the **ONIDIA palette presets** (exported colours keep their
   semantic names like `colHair`) or any custom `#RRGGBB`/`#RRGGBBAA`.
4. Optional: **Load sample** to fill every layer with a demo figure, then
   edit the **chatter phrases** (`mood: text` per line). The **Reference**
   toggle (on by default) draws the **real** registered character — rendered
   server-side by the ONIDIA engine (Go → image) — faintly behind your strokes
   as a pixel-exact tracing guide; pick which character from the dropdown.
5. **Export Go code →** — the server converts your strokes into gofmt-ed Go
   source, then copy or download it.

Hidden layers are left out of the export; the **Preview** toggle shows the
final sprite without grid or layer dimming. **Save/Load** stores the whole
project as JSON.

## 3. Install the exported character in ONIDIA

The export modal prints the same instructions; in short:

```sh
mkdir -p onidia/characters/pixie
# save the downloaded file as onidia/characters/pixie/pixie.go
```

Then add **one line** to `onidia/main.go`'s import block (this is ONIDIA's
documented plug-in mechanism, see `characters/_template/doc.go`):

```go
_ "github.com/portege/onidia/characters/pixie"
```

Build and run it:

```sh
cd onidia && go build . && ./desktop-pet -character pixie
```

## 4. How strokes become Go code

The editor POSTs JSON to `POST /api/generate`:

```json
{ "name": "pixie",
  "phrases": [{ "text": "hi!", "expr": "happy" }],
  "layers": [{ "id": "body", "visible": true,
               "strokes": [{ "color": "#FF9AAA", "width": 8,
                             "points": [[46,100],[66,106]] }] }] }
```

`generate.go` maps it onto the ONIDIA contract:

- **Stroke → primitive**: each polyline becomes a chain of
  `canvas.FillCapsule` calls (radius = brush width/2); a single tap becomes
  `canvas.FillCircle`. Coordinates are logical grid units.
- **Layers → `draw*` functions** emitted in the fixed order
  shadow → hair_back → legs → body → arms → head → face, composed by
  `drawPet` exactly like the hand-written characters.
- **Pose contract honoured**: figure strokes add `dy = p.Bob`; the shadow is
  grounded and shrinks on `p.Hop`; hair/back-head layers are guarded by
  `!p.Helmet` (robot event).
- **Palette**: colours are deduplicated; known ONIDIA preset colours export
  with their semantic var names (`colHair`, `colDress`, …), custom ones
  become `col1`, `col2`, …
- **Phrases**: `mood: text` lines become `characters.Phrase` entries using
  the 16 shared `Expr*` constants.
- The result is run through `go/format` + `go/parser` before it is returned,
  so what you download is always valid, formatted Go.

## 5. Go → image reference (`render.go`)

Hand-porting a real character into strokes is lossy, so the editor ships a
second engine: it renders an actual registered ONIDIA character to a PNG
through the **real** `characters.Character.Draw` on a supersampled canvas, then
box-downsamples — the same raster path as `onidia/pet.go` (`SSFactor` is
inlined because `internal/fx` is a different module). The image is drawn
faintly behind the grid as a tracing guide.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/characters` | list registered character names (`["kama","onidia"]`) |
| `GET /api/render?character=onidia&expr=happy&w=224` | PNG of that character/face |

The built-in characters self-register via `init()` (blank-imported in
`render.go`) and depend only on `canvas`/`characters`/`image` — no X11 — so the
builder stays headless. To trace a newly built character, install it in ONIDIA
and blank-import its package in `render.go`.

## 6. Limitations

- The **face is static**: exported characters draw one face; blink, talking
  and the 16 expressions are not yet generated (hand-written characters
  switch on `Pose.Expr` inside `face.go`).
- **Action overlays** (skip, juggle, dance, guitar, …) are not generated;
  the pet still performs every behaviour — the arms just keep their base pose.
- No mirroring/orientation variants: one drawing is used for both
  directions (the renderer flips the frame, so keep the design flip-safe).

## 7. Development

```sh
go test ./...     # generator + API tests
go vet ./...
```

*File map:* `generate.go` (JSON → Go source) · `render.go` (Go → image
reference) · `main.go` (HTTP server) · `static/` (editor UI: `index.html`,
`app.js`, `style.css`, `sample.js`) · `generate_test.go` (tests). The generated
code was compile-verified against the real `github.com/portege/onidia` module.
