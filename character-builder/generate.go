// Package main implements the ONIDIA character builder: a small web app whose
// drawing editor exports strokes as a complete Go character package.
//
// generate.go converts the editor's JSON (layers of freehand strokes on the
// shared 112x160 ONIDIA authoring grid) into gofmt-formatted Go source that
// implements characters.Character, ready to drop into onidia/characters/.
package main

import (
	"encoding/json"
	"fmt"
	"go/format"
	"go/parser"
	"go/token"
	"math"
	"strconv"
	"strings"
)

// ---- request model ---------------------------------------------------------

// Stroke is one freehand pen mark: a polyline with a constant colour and
// brush width (logical units; the drawn radius is Width/2).
type Stroke struct {
	Color  string       `json:"color"`
	Width  float64      `json:"width"`
	Points [][2]float64 `json:"points"`
}

// Layer is one body part of the character, matching the draw order of
// onidia's drawPet (shadow → hair_back → legs → body → arms → head → face).
type Layer struct {
	ID      string   `json:"id"`
	Visible *bool    `json:"visible"` // nil = visible
	Strokes []Stroke `json:"strokes"`
}

func (l Layer) visible() bool { return l.Visible == nil || *l.Visible }

// Phrase is one chatter line plus the mood it is said with.
type Phrase struct {
	Text string `json:"text"`
	Expr string `json:"expr"` // shared mood name, e.g. "happy"
}

// Request is the payload the editor POSTs to /api/generate.
type Request struct {
	Name    string   `json:"name"`
	Phrases []Phrase `json:"phrases"`
	Layers  []Layer  `json:"layers"`
}

// ---- static description of the seven core layers ---------------------------

// layerSpec describes how each editor layer maps into generated code.
type layerSpec struct {
	id       string // editor layer id
	fn       string // generated draw function suffix (draw + fn)
	grounded bool   // drawn without the bob offset: only the shadow
	hair     bool   // hidden while p.Helmet seals the head (robot event)
	order    int    // draw order inside drawPet
	doc      string // comment on the generated function
}

var layerSpecs = []layerSpec{
	{id: "shadow", fn: "Shadow", grounded: true, order: 0,
		doc: "Ground shadow: never bobs with the figure and shrinks while airborne (p.Hop)."},
	{id: "hair_back", fn: "HairBack", hair: true, order: 1,
		doc: "Back hair layer (twin tails etc.): sits behind every other body part."},
	{id: "legs", fn: "Legs", order: 2,
		doc: "Legs layer."},
	{id: "body", fn: "Body", order: 3,
		doc: "Torso/outfit layer."},
	{id: "arms", fn: "Arms", order: 4,
		doc: "Arms layer."},
	{id: "head", fn: "Head", hair: true, order: 5,
		doc: "Head layer (hair mass, face base): hidden while p.Helmet seals the head."},
	{id: "face", fn: "Face", order: 6,
		doc: "Face features layer: drawn last so eyes/mouth sit above everything."},
}

func specFor(id string) (*layerSpec, bool) {
	for i := range layerSpecs {
		if layerSpecs[i].id == id {
			return &layerSpecs[i], true
		}
	}
	return nil, false
}

// ---- colour palette --------------------------------------------------------

// knownColors maps the standard ONIDIA palette roles (see characters/onidia/
// palette.go) onto their Go var names, so picking a preset swatch in the
// editor exports with the same semantic name hand-written characters use.
var knownColors = map[[4]uint8]string{
	{47, 34, 62, 255}:    "colOutlineDark",
	{198, 134, 100, 255}: "colOutlineSkin",
	{25, 12, 35, 50}:     "colShadow",
	{95, 207, 214, 255}:  "colHair",
	{65, 174, 182, 255}:  "colHairShade",
	{165, 236, 239, 255}: "colHairLight",
	{255, 122, 158, 255}: "colTie",
	{255, 228, 206, 255}: "colSkin",
	{235, 165, 140, 140}: "colNose",
	{255, 120, 135, 70}:  "colBlush",
	{126, 188, 255, 205}: "colTear",
	{158, 212, 255, 230}: "colSweat",
	{240, 78, 78, 235}:   "colAnger",
	{253, 253, 255, 255}: "colSclera",
	{54, 62, 110, 255}:   "colIris",
	{24, 28, 56, 255}:    "colPupil",
	{40, 32, 50, 255}:    "colLash",
	{176, 66, 90, 255}:   "colMouth",
	{231, 126, 143, 255}: "colTongue",
	{255, 154, 170, 255}: "colDress",
	{255, 214, 224, 255}: "colFrill",
	{250, 248, 252, 255}: "colCollar",
	{92, 74, 110, 255}:   "colShoe",
}

// ---- helpers ---------------------------------------------------------------

// parseColor accepts #RRGGBB or #RRGGBBAA and returns the RGBA components.
func parseColor(s string) ([4]uint8, error) {
	var out [4]uint8
	s = strings.TrimPrefix(strings.TrimSpace(s), "#")
	switch len(s) {
	case 6:
		s += "ff"
	case 8:
	default:
		return out, fmt.Errorf("color %q must be #RRGGBB or #RRGGBBAA", s)
	}
	v, err := strconv.ParseUint(s, 16, 32)
	if err != nil {
		return out, fmt.Errorf("color %q: %w", s, err)
	}
	out[0], out[1], out[2], out[3] = uint8(v>>24), uint8(v>>16), uint8(v>>8), uint8(v)
	return out, nil
}

// fmtF formats a logical coordinate; values arrive pre-rounded to 2 decimals
// from the editor and are rounded again here to keep the output tidy.
func fmtF(v float64) string {
	return strconv.FormatFloat(math.Round(v*100)/100, 'f', -1, 64)
}

// exportedName turns a package name ("my_char") into the exported struct name
// ("MyChar") used for the characters.Character implementation.
func exportedName(pkg string) string {
	var b strings.Builder
	upper := true
	for _, r := range pkg {
		if r == '_' {
			upper = true
			continue
		}
		if upper {
			b.WriteString(strings.ToUpper(string(r)))
			upper = false
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// validateName reports an error unless name is a usable Go package name
// (lowercase letter followed by letters/digits/underscores, not a keyword);
// it also becomes the folder name, so it must not start with "_" — the Go
// toolchain ignores underscore-prefixed folders (see characters/_template).
func validateName(name string) error {
	if name == "" {
		return fmt.Errorf("character name is required")
	}
	if token.Lookup(name) != token.IDENT {
		return fmt.Errorf("name %q is a Go keyword", name)
	}
	if !token.IsIdentifier(name) {
		return fmt.Errorf("name %q is not a valid Go identifier (use lowercase letters, digits and underscores)", name)
	}
	if name != strings.ToLower(name) {
		return fmt.Errorf("name %q must be lowercase (it becomes the package/folder name)", name)
	}
	if first := name[0]; first < 'a' || first > 'z' {
		return fmt.Errorf("name %q must start with a letter a–z (folders starting with _ are ignored by Go)", name)
	}
	return nil
}

// palette assigns each colour a Go var name: preset swatches keep their
// semantic ONIDIA role name, everything else becomes col1, col2, ... in
// order of first use.
type palette struct {
	byColor map[[4]uint8]string
	byName  map[string][4]uint8
	order   []string
}

func newPalette() *palette {
	return &palette{byColor: map[[4]uint8]string{}, byName: map[string][4]uint8{}}
}

func (p *palette) intern(c [4]uint8) string {
	if n, ok := p.byColor[c]; ok {
		return n
	}
	n := ""
	if role, ok := knownColors[c]; ok {
		if _, taken := p.byName[role]; !taken {
			n = role
		}
	}
	if n == "" {
		for i := 1; ; i++ {
			cand := "col" + strconv.Itoa(i)
			if _, taken := p.byName[cand]; !taken {
				n = cand
				break
			}
		}
	}
	p.byColor[c] = n
	p.byName[n] = c
	p.order = append(p.order, n)
	return n
}

// exprNames maps the editor's mood names onto the shared Expr constants.
var exprNames = map[string]string{
	"neutral":     "ExprNeutral",
	"happy":       "ExprHappy",
	"wink":        "ExprWink",
	"sad":         "ExprSad",
	"thinking":    "ExprThinking",
	"anxious":     "ExprAnxious",
	"angry":       "ExprAngry",
	"surprised":   "ExprSurprised",
	"sleepy":      "ExprSleepy",
	"fear":        "ExprFear",
	"disgust":     "ExprDisgust",
	"contempt":    "ExprContempt",
	"confused":    "ExprConfused",
	"skeptical":   "ExprSkeptical",
	"embarrassed": "ExprEmbarrassed",
	"adore":       "ExprAdore",
}

// ---- generator -------------------------------------------------------------

// Generate converts an editor request into gofmt-formatted Go source for one
// character package. It fails with a user-facing error on invalid input.
func Generate(req Request) ([]byte, error) {
	if err := validateName(req.Name); err != nil {
		return nil, err
	}
	type emitLayer struct {
		spec    *layerSpec
		strokes []Stroke
	}
	var toEmit []emitLayer
	seen := map[string]bool{}
	for _, l := range req.Layers {
		spec, ok := specFor(l.ID)
		if !ok {
			return nil, fmt.Errorf("unknown layer %q", l.ID)
		}
		if seen[l.ID] {
			return nil, fmt.Errorf("duplicate layer %q", l.ID)
		}
		seen[l.ID] = true
		if !l.visible() || len(l.Strokes) == 0 {
			continue // hidden or empty layers are not exported
		}
		toEmit = append(toEmit, emitLayer{spec: spec, strokes: l.Strokes})
	}
	// Draw order is the fixed onidia layer order, not the request's order.
	for i := 0; i < len(toEmit); i++ {
		for j := i + 1; j < len(toEmit); j++ {
			if toEmit[j].spec.order < toEmit[i].spec.order {
				toEmit[i], toEmit[j] = toEmit[j], toEmit[i]
			}
		}
	}

	// Validate strokes up front and intern their colours.
	pal := newPalette()
	for _, el := range toEmit {
		for si, s := range el.strokes {
			if len(s.Points) == 0 {
				return nil, fmt.Errorf("%s: stroke %d has no points", el.spec.id, si+1)
			}
			if s.Width <= 0 || s.Width > 128 {
				return nil, fmt.Errorf("%s: stroke %d has invalid width %g", el.spec.id, si+1, s.Width)
			}
			c, err := parseColor(s.Color)
			if err != nil {
				return nil, fmt.Errorf("%s: stroke %d: %w", el.spec.id, si+1, err)
			}
			pal.intern(c)
		}
	}

	// Validate phrases; an empty list gets one friendly default line.
	phrases := req.Phrases
	if len(phrases) == 0 {
		phrases = []Phrase{{Text: "hi there!", Expr: "neutral"}}
	}
	type emitPhrase struct{ text, expr string }
	ep := make([]emitPhrase, 0, len(phrases))
	for i, ph := range phrases {
		if strings.TrimSpace(ph.Text) == "" {
			return nil, fmt.Errorf("phrase %d is empty", i+1)
		}
		ex, ok := exprNames[strings.ToLower(strings.TrimSpace(ph.Expr))]
		if !ok {
			return nil, fmt.Errorf("phrase %d: unknown mood %q (try: neutral, happy, wink, sad, thinking, anxious, angry, surprised, sleepy)", i+1, ph.Expr)
		}
		ep = append(ep, emitPhrase{text: ph.Text, expr: ex})
	}

	var b strings.Builder
	pkg := req.Name
	typeName := exportedName(pkg)

	// ---- header + registration ----
	fmt.Fprintf(&b, `// Package %s registers the character exported by the ONIDIA character
// builder — every pixel is a stroked shape on the shared 112x160 authoring
// grid (characters/characters.go), drawn procedurally at runtime.
//
// Install: copy this file to onidia/characters/%[1]s/%[1]s.go and add the
// blank import _ "github.com/portege/onidia/characters/%[1]s" to main.go.
package %s

import (
	"github.com/portege/onidia/canvas"
	"github.com/portege/onidia/characters"
)

// %[3]s implements characters.Character.
type %[3]s struct{}

func (%[3]s) Name() string { return "%[2]s" }

func (%[3]s) Draw(cv *canvas.Canvas, p characters.Pose) { drawPet(cv, p) }

func (%[3]s) Phrases() []characters.Phrase { return phrases }

// Compile-time proof that %[3]s satisfies the contract.
var _ characters.Character = %[3]s{}

func init() { characters.Register(%[3]s{}) }

// rgba keeps the palette block readable, e.g. rgba(95, 207, 214, 255).
var rgba = characters.RGBA
`, pkg, pkg, typeName)

	// ---- palette ----
	b.WriteString("\n// Palette: every colour used by this character's strokes.\nvar (\n")
	for _, name := range pal.order {
		c := pal.byName[name]
		fmt.Fprintf(&b, "\t%s = rgba(%d, %d, %d, %d)\n", name, c[0], c[1], c[2], c[3])
	}
	b.WriteString(")\n")

	// ---- composition root ----
	b.WriteString(`
// drawPet paints one frame of the character onto the supersampled canvas.
// Every figure coordinate below adds dy (= p.Bob) so the whole body bobs
// together; the ground shadow is grounded and never moves.
func drawPet(c *canvas.Canvas, p characters.Pose) {
	dy := p.Bob
`)
	for _, el := range toEmit {
		switch {
		case el.spec.grounded:
			b.WriteString(`
	// Ground shadow: never bobs; shrinks while airborne (p.Hop).
	shrink := 1.0
	if p.Hop {
		shrink = 0.7
	}
	drawShadow(c, shrink)
`)
		case el.spec.hair:
			fmt.Fprintf(&b, "\n\t// %s\n\tif !p.Helmet {\n\t\tdraw%s(c, dy)\n\t}\n", el.spec.doc, el.spec.fn)
		default:
			fmt.Fprintf(&b, "\n\t// %s\n\tdraw%s(c, dy)\n", el.spec.doc, el.spec.fn)
		}
	}
	b.WriteString("}\n")

	// Shadow shrink helper — only when the shadow layer exists.
	if len(toEmit) > 0 && toEmit[0].spec.grounded {
		b.WriteString(`
// shadowShrink scales shadow points and radii about the blob's own centre so
// the shadow shrinks in place while the buddy hops.
type shadowShrink struct{ ax, ay, k float64 }

func (s shadowShrink) x(v float64) float64 { return s.ax + (v-s.ax)*s.k }
func (s shadowShrink) y(v float64) float64 { return s.ay + (v-s.ay)*s.k }
func (s shadowShrink) r(v float64) float64 { return v * s.k }

`)
	}

	// ---- per-layer draw functions ----
	for _, el := range toEmit {
		writeLayerFunc(&b, el.spec, el.strokes, pal)
	}

	// ---- phrases ----
	b.WriteString("\n// phrases is the random-chatter pool; each line also picks the face it is\n// said with. The bubble font is Latin-only — keep text ASCII.\nvar phrases = []characters.Phrase{\n")
	for _, ph := range ep {
		fmt.Fprintf(&b, "\t{Text: %s, Expr: characters.%s},\n", strconv.Quote(ph.text), ph.expr)
	}
	b.WriteString("}\n")

	src, err := format.Source([]byte(b.String()))
	if err != nil {
		return nil, fmt.Errorf("generated code does not format: %w\n%s", err, b.String())
	}
	if _, err := parser.ParseFile(token.NewFileSet(), req.Name+".go", src, parser.AllErrors); err != nil {
		return nil, fmt.Errorf("generated code does not parse: %w", err)
	}
	return src, nil
}

// writeLayerFunc emits one draw<Layer> function: strokes become chains of
// canvas.FillCapsule (round-capped brush segments); single-point strokes
// become canvas.FillCircle.
func writeLayerFunc(b *strings.Builder, spec *layerSpec, strokes []Stroke, pal *palette) {
	grounded := spec.grounded
	fmt.Fprintf(b, "\n// %s\nfunc draw%s(c *canvas.Canvas, ", spec.doc, spec.fn)
	if grounded {
		b.WriteString("shrink float64) {\n")
		// Anchor = centroid of every shadow point, so the blob shrinks in place.
		var sx, sy, n float64
		for _, s := range strokes {
			for _, p := range s.Points {
				sx += p[0]
				sy += p[1]
				n++
			}
		}
		if n == 0 {
			n = 1
		}
		fmt.Fprintf(b, "\tsc := shadowShrink{ax: %s, ay: %s, k: shrink}\n", fmtF(sx/n), fmtF(sy/n))
	} else {
		b.WriteString("dy float64) {\n")
	}
	for _, s := range strokes {
		c, err := parseColor(s.Color)
		if err != nil {
			c = [4]uint8{} // unreachable: Generate validated colours already
		}
		col := pal.byColor[c]
		r := fmtF(s.Width / 2)
		if len(s.Points) == 1 {
			p := s.Points[0]
			if grounded {
				fmt.Fprintf(b, "\tc.FillCircle(sc.x(%s), sc.y(%s), sc.r(%s), %s)\n", fmtF(p[0]), fmtF(p[1]), r, col)
			} else {
				fmt.Fprintf(b, "\tc.FillCircle(%s, %s+dy, %s, %s)\n", fmtF(p[0]), fmtF(p[1]), r, col)
			}
			continue
		}
		for i := 0; i+1 < len(s.Points); i++ {
			a, q := s.Points[i], s.Points[i+1]
			if grounded {
				fmt.Fprintf(b, "\tc.FillCapsule(sc.x(%s), sc.y(%s), sc.x(%s), sc.y(%s), sc.r(%s), %s)\n",
					fmtF(a[0]), fmtF(a[1]), fmtF(q[0]), fmtF(q[1]), r, col)
			} else {
				fmt.Fprintf(b, "\tc.FillCapsule(%s, %s+dy, %s, %s+dy, %s, %s)\n",
					fmtF(a[0]), fmtF(a[1]), fmtF(q[0]), fmtF(q[1]), r, col)
			}
		}
	}
	b.WriteString("}\n")
}

// DecodeRequest parses the JSON body of /api/generate.
func DecodeRequest(data []byte) (Request, error) {
	var req Request
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		return req, fmt.Errorf("invalid JSON: %w", err)
	}
	return req, nil
}
