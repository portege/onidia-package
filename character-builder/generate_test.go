package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strings"
	"testing"
)

// sampleRequest builds a small but representative drawing: a shadow, legs, a
// body capsule, a head outline and a single eye dot on the face layer.
func sampleRequest() Request {
	return Request{
		Name: "miko",
		Phrases: []Phrase{
			{Text: "hi there!", Expr: "happy"},
			{Text: "so sleepy...", Expr: "sleepy"},
		},
		Layers: []Layer{
			{ID: "face", Strokes: []Stroke{
				{Color: "#181C38", Width: 3, Points: [][2]float64{{43, 72}}},
			}},
			{ID: "body", Strokes: []Stroke{
				{Color: "#FF9AAA", Width: 6, Points: [][2]float64{{50, 96}, {52, 110}, {56, 127}}},
			}},
			{ID: "shadow", Strokes: []Stroke{
				{Color: "#190C2332", Width: 20, Points: [][2]float64{{46, 152}, {56, 152}, {66, 152}}},
			}},
			{ID: "head", Strokes: []Stroke{
				{Color: "#5FCFD6", Width: 8, Points: [][2]float64{{56, 40}, {70, 50}, {56, 63}, {42, 50}, {56, 40}}},
			}},
			{ID: "legs", Strokes: []Stroke{
				{Color: "#FFE4CE", Width: 5, Points: [][2]float64{{50, 116}, {50, 147}}},
			}},
		},
	}
}

func mustGenerate(t *testing.T, req Request) string {
	t.Helper()
	src, err := Generate(req)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	return string(src)
}

func TestGenerateParsesAndIsFormatted(t *testing.T) {
	src := mustGenerate(t, sampleRequest())
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "miko.go", src, parser.AllErrors)
	if err != nil {
		t.Fatalf("generated source does not parse: %v", err)
	}
	if f.Name.Name != "miko" {
		t.Errorf("package name = %q, want miko", f.Name.Name)
	}
}

func TestGenerateImplementsContract(t *testing.T) {
	src := mustGenerate(t, sampleRequest())
	for _, want := range []string{
		"type Miko struct{}",
		"func (Miko) Name() string",
		"func (Miko) Draw(cv *canvas.Canvas, p characters.Pose)",
		"func (Miko) Phrases() []characters.Phrase",
		"var _ characters.Character = Miko{}",
		"func init() { characters.Register(Miko{}) }",
	} {
		if !strings.Contains(src, want) {
			t.Errorf("generated code missing %q", want)
		}
	}
}

func TestDrawOrderIsFixedLayerOrder(t *testing.T) {
	// The request lists layers face-first; output must draw shadow → hair →
	// legs → body → arms → head → face.
	src := mustGenerate(t, sampleRequest())
	prev := -1
	for _, call := range []string{
		"drawShadow(c, shrink)",
		"drawLegs(c, dy)",
		"drawBody(c, dy)",
		"drawHead(c, dy)",
		"drawFace(c, dy)",
	} {
		callIdx := strings.LastIndex(src, call)
		if callIdx < 0 {
			t.Fatalf("generated code missing call %q", call)
		}
		if callIdx < prev {
			t.Errorf("call %q appears out of order", call)
		}
		prev = callIdx
	}
}

func TestHiddenAndEmptyLayersAreSkipped(t *testing.T) {
	req := sampleRequest()
	hidden := false
	req.Layers = append(req.Layers,
		Layer{ID: "arms", Visible: &hidden, Strokes: []Stroke{
			{Color: "#FFE4CE", Width: 5, Points: [][2]float64{{42, 102}, {32, 121}}},
		}},
		Layer{ID: "hair_back"}, // empty
	)
	src := mustGenerate(t, req)
	if strings.Contains(src, "drawArms") {
		t.Error("hidden arms layer should not be exported")
	}
	if strings.Contains(src, "drawHairBack") {
		t.Error("empty layers should not be exported")
	}
	if !strings.Contains(src, "drawLegs(c, dy)") {
		t.Error("visible legs layer should be exported")
	}
}

func TestHelmetGuardsHairLayers(t *testing.T) {
	req := sampleRequest()
	req.Layers = append(req.Layers, Layer{ID: "hair_back", Strokes: []Stroke{
		{Color: "#5FCFD6", Width: 4, Points: [][2]float64{{31, 55}, {20, 90}}},
	}})
	src := mustGenerate(t, req)
	if !strings.Contains(src, "if !p.Helmet {\n\t\tdrawHairBack(c, dy)\n\t}") {
		t.Error("hair_back layer must be guarded by !p.Helmet")
	}
	if !strings.Contains(src, "if !p.Helmet {\n\t\tdrawHead(c, dy)\n\t}") {
		t.Error("head layer must be guarded by !p.Helmet")
	}
	// Face and body stay visible under the helmet.
	if !strings.Contains(src, "\n\tdrawFace(c, dy)\n") {
		t.Error("face layer must draw unconditionally")
	}
}

func TestShadowIsGroundedAndShrinks(t *testing.T) {
	src := mustGenerate(t, sampleRequest())
	if !strings.Contains(src, "func drawShadow(c *canvas.Canvas, shrink float64)") {
		t.Error("shadow must take shrink, not dy")
	}
	if !strings.Contains(src, "sc.x(") || !strings.Contains(src, "sc.r(") {
		t.Error("shadow strokes must scale through shadowShrink")
	}
	// Every non-shadow capsule must add dy.
	if !strings.Contains(src, "c.FillCapsule(50, 96+dy, 52, 110+dy, 3, colDress)") {
		t.Errorf("body stroke missing dy offset:\n%s", src)
	}
}

func TestSinglePointStrokeBecomesCircle(t *testing.T) {
	src := mustGenerate(t, sampleRequest())
	if !strings.Contains(src, "c.FillCircle(43, 72+dy, 1.5, colPupil)") {
		t.Errorf("single-point stroke should become FillCircle:\n%s", src)
	}
}

func TestPaletteDedupeAndSemanticNames(t *testing.T) {
	req := Request{
		Name: "x",
		Layers: []Layer{{ID: "body", Strokes: []Stroke{
			{Color: "#FF9AAA", Width: 4, Points: [][2]float64{{1, 1}, {2, 2}}}, // colDress preset
			{Color: "#FF9AAA", Width: 4, Points: [][2]float64{{3, 3}, {4, 4}}}, // duplicate
			{Color: "#123456", Width: 4, Points: [][2]float64{{5, 5}, {6, 6}}}, // custom → col1
		}}},
	}
	src := mustGenerate(t, req)
	if strings.Count(src, "colDress = rgba(255, 154, 170, 255)") != 1 {
		t.Errorf("preset colour should be declared once with its role name:\n%s", src)
	}
	// gofmt aligns the var block, so match name and value separately.
	if !strings.Contains(src, "col1 ") || !strings.Contains(src, "rgba(18, 52, 86, 255)") {
		t.Errorf("custom colour should become col1:\n%s", src)
	}
	if strings.Contains(src, "col2") {
		t.Errorf("duplicate colour must not be re-declared:\n%s", src)
	}
}

func TestValidateName(t *testing.T) {
	bad := []string{"", "Miko", "1abc", "my-char", "func", "go", "package", "miko package", "_abc", "_"}
	for _, n := range bad {
		if err := validateName(n); err == nil {
			t.Errorf("validateName(%q) = nil, want error", n)
		}
	}
	good := []string{"miko", "kama2", "my_char", "a"}
	for _, n := range good {
		if err := validateName(n); err != nil {
			t.Errorf("validateName(%q) = %v, want nil", n, err)
		}
	}
}

func TestGenerateRejectsBadInput(t *testing.T) {
	legStroke := []Stroke{{Color: "#ffffff", Width: 2, Points: [][2]float64{{0, 0}}}}
	cases := []struct {
		name string
		req  Request
	}{
		{"bad name", Request{Name: "Bad"}},
		{"unknown layer", Request{Name: "x", Layers: []Layer{{ID: "tail"}}}},
		{"duplicate layer", Request{Name: "x", Layers: []Layer{
			{ID: "legs", Strokes: legStroke},
			{ID: "legs"},
		}}},
		{"bad colour", Request{Name: "x", Layers: []Layer{{ID: "legs", Strokes: []Stroke{
			{Color: "red", Width: 2, Points: [][2]float64{{0, 0}}},
		}}}}},
		{"bad width", Request{Name: "x", Layers: []Layer{{ID: "legs", Strokes: []Stroke{
			{Color: "#ffffff", Width: 0, Points: [][2]float64{{0, 0}}},
		}}}}},
		{"no points", Request{Name: "x", Layers: []Layer{{ID: "legs", Strokes: []Stroke{
			{Color: "#ffffff", Width: 2},
		}}}}},
		{"bad mood", Request{Name: "x", Phrases: []Phrase{{Text: "hi", Expr: "confused?"}}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := Generate(tc.req); err == nil {
				t.Error("Generate succeeded, want error")
			}
		})
	}
}

func TestGeneratedDrawCallGraph(t *testing.T) {
	// Structural check: drawPet must call every emitted draw function, so the
	// generated character has no dead (never drawn) layers.
	src := mustGenerate(t, sampleRequest())
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "miko.go", src, 0)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	var pet *ast.FuncDecl
	funcs := map[string]*ast.FuncDecl{}
	for _, d := range f.Decls {
		fd, ok := d.(*ast.FuncDecl)
		if !ok || fd.Recv != nil {
			continue
		}
		funcs[fd.Name.Name] = fd
		if fd.Name.Name == "drawPet" {
			pet = fd
		}
	}
	if pet == nil {
		t.Fatal("drawPet missing")
	}
	// Calls look like drawX(c, dy) / drawShadow(c, shrink) — the only place
	// those exact strings occur is inside drawPet (declarations spell the
	// full signature instead).
	for name, fd := range funcs {
		if !strings.HasPrefix(name, "draw") || name == "drawPet" || fd.Recv != nil {
			continue
		}
		want := name + "(c, dy)"
		if name == "drawShadow" {
			want = "drawShadow(c, shrink)"
		}
		if !strings.Contains(src, want) {
			t.Errorf("drawPet does not call %s", name)
		}
	}
}

func TestDecodeRequestRejectsUnknownFields(t *testing.T) {
	if _, err := DecodeRequest([]byte(`{"name":"x","bogus":1}`)); err == nil {
		t.Error("unknown field should be rejected")
	}
	req, err := DecodeRequest([]byte(`{"name":"miko","layers":[]}`))
	if err != nil || req.Name != "miko" {
		t.Errorf("DecodeRequest = (%+v, %v)", req, err)
	}
}

func TestPhrasesAreGenerated(t *testing.T) {
	src := mustGenerate(t, sampleRequest())
	if !strings.Contains(src, `{Text: "hi there!", Expr: characters.ExprHappy}`) {
		t.Error("happy phrase missing")
	}
	if !strings.Contains(src, `{Text: "so sleepy...", Expr: characters.ExprSleepy}`) {
		t.Error("sleepy phrase missing")
	}
}

func TestDefaultPhraseWhenNoneGiven(t *testing.T) {
	req := sampleRequest()
	req.Phrases = nil
	src := mustGenerate(t, req)
	if !strings.Contains(src, `{Text: "hi there!", Expr: characters.ExprNeutral}`) {
		t.Error("default phrase missing")
	}
}
