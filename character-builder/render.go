// render.go — a Go → image backend for the character builder.
//
// The drawing editor is a stroke tool, so hand-porting a real ONIDIA character
// into strokes is inherently lossy (the preview drifts from the sprite). To let
// users trace a pixel-exact reference, this file renders an actual registered
// character to a PNG through the real ONIDIA engine: characters.Character.Draw
// onto a supersampled canvas, then box-downsample — the same path
// onidia/pet.go's renderer uses (SSFactor is inlined because internal/fx is a
// different module and not importable from here).
//
// The built-in characters self-register via init(), so blank-importing their
// packages makes characters.Get("onidia") / characters.Names() work. They only
// depend on canvas/characters/image — no X11 — so this builds and runs
// headless anywhere.

package main

import (
	"encoding/json"
	"image"
	"image/png"
	"log"
	"net/http"
	"strconv"

	"github.com/portege/onidia/canvas"
	"github.com/portege/onidia/characters"

	// blank imports: each package registers itself in characters.Register.
	_ "github.com/portege/onidia/characters/kama"
	_ "github.com/portege/onidia/characters/onidia"
)

// ssFactor mirrors internal/fx.SSFactor (supersampling). Kept local because the
// fx package is internal to the onidia module and cannot be imported here.
const ssFactor = 2

// idlePose is a calm standing frame (small bob + a touch of tail sway), the
// pose ONIDIA's own -debug dump uses as its baseline. Actions are left off so
// the reference is just the character, ready to trace.
func idlePose(expr characters.Expr) characters.Pose {
	return characters.Pose{Bob: -0.9, TailSway: 4, Expr: expr}
}

// renderCharacter rasterizes a registered character at the given output width
// (logical units scaled to pixels) and returns the downsampled RGBA image.
func renderCharacter(name string, expr characters.Expr, outW int) (*image.NRGBA, error) {
	ch, err := characters.Get(name)
	if err != nil {
		return nil, err
	}
	outH := int(characters.BaseH/characters.BaseW*float64(outW) + 0.5)
	if outH < 48 {
		outH = 48
	}
	src := image.NewNRGBA(image.Rect(0, 0, outW*ssFactor, outH*ssFactor))
	cv := canvas.Canvas{Pix: src}
	cv.K = float64(outW*ssFactor) / characters.BaseW
	ch.Draw(&cv, idlePose(expr))

	dst := image.NewNRGBA(image.Rect(0, 0, outW, outH))
	return downsample(src, dst, ssFactor, false), nil
}

// downsample box-averages ss×ss blocks (premultiplied), optionally mirroring.
// A faithful copy of onidia/pet.go's downsampleInto.
func downsample(src, dst *image.NRGBA, ss int, flip bool) *image.NRGBA {
	sw := src.Bounds().Dx()
	dw, dh := dst.Bounds().Dx(), dst.Bounds().Dy()
	n := ss * ss
	for oy := 0; oy < dh; oy++ {
		sy0 := oy * ss
		for ox := 0; ox < dw; ox++ {
			sx0 := ox * ss
			if flip {
				sx0 = sw - (ox+1)*ss
			}
			var rs, gs, bs, as int
			for yy := 0; yy < ss; yy++ {
				row := src.PixOffset(sx0, sy0+yy)
				for xx := 0; xx < ss; xx++ {
					i := row + xx*4
					a := int(src.Pix[i+3])
					rs += int(src.Pix[i+0]) * a
					gs += int(src.Pix[i+1]) * a
					bs += int(src.Pix[i+2]) * a
					as += a
				}
			}
			j := dst.PixOffset(ox, oy)
			if as == 0 {
				dst.Pix[j+3] = 0
				continue
			}
			dst.Pix[j+0] = uint8(rs / as)
			dst.Pix[j+1] = uint8(gs / as)
			dst.Pix[j+2] = uint8(bs / as)
			dst.Pix[j+3] = uint8(as / n)
		}
	}
	return dst
}

// renderExpr resolves a mood name (or empty → neutral) to an Expr.
func renderExpr(mood string) characters.Expr {
	if e, ok := characters.MoodExpr(mood); ok {
		return e
	}
	return characters.ExprNeutral
}

// handleRender writes a PNG of the requested character.
//
//	GET /api/render?character=onidia&expr=happy&w=112
func handleRender(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("character")
	if name == "" {
		name = "onidia"
	}
	expr := renderExpr(r.URL.Query().Get("expr"))
	outW := 112
	if v := r.URL.Query().Get("w"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 34 && n <= 1024 {
			outW = n
		}
	}
	img, err := renderCharacter(name, expr, outW)
	if err != nil {
		httpError(w, http.StatusBadRequest, err)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "no-cache")
	if err := png.Encode(w, img); err != nil {
		log.Printf("encode png: %v", err)
	}
}

// handleCharacters lists the registered character names as JSON.
func handleCharacters(w http.ResponseWriter, r *http.Request) {
	names := characters.Names()
	if names == nil {
		names = []string{}
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(names)
}
