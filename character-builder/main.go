// Command character-builder is a small web app: a drawing editor whose canvas
// strokes are exported as a complete ONIDIA character package (Go source).
//
// Run it with `go run .` and open http://localhost:8090 — see README.md.
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
)

// maxBodyBytes caps the /api/generate payload (a huge drawing is a few MB
// at most; anything bigger is a bug or an abuse).
const maxBodyBytes = 8 << 20

// staticDir is where index.html/app.js/style.css live; resolved relative to
// the working directory so `go run .` and the built binary both work when
// started from the character-builder folder.
const staticDir = "static"

func main() {
	addr := ":8090"
	if v := os.Getenv("BUILDER_ADDR"); v != "" {
		addr = v
	}

	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/generate", handleGenerate)
	mux.HandleFunc("GET /api/render", handleRender)
	mux.HandleFunc("GET /api/characters", handleCharacters)
	mux.Handle("/", http.FileServer(http.Dir(staticDir)))

	srv := &http.Server{
		Addr:              addr,
		Handler:           logRequests(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("character builder listening on http://localhost%s", addr)
	log.Printf("open the URL, draw a character, then export Go code")
	if err := srv.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}

// handleGenerate turns the editor's stroke JSON into Go source.
func handleGenerate(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	var req Request
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpError(w, http.StatusBadRequest, fmt.Errorf("invalid JSON: %w", err))
		return
	}
	src, err := Generate(req)
	if err != nil {
		httpError(w, http.StatusBadRequest, err)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if err := json.NewEncoder(w).Encode(map[string]string{
		"code":     string(src),
		"filename": req.Name + ".go",
		"install": fmt.Sprintf(
			"mkdir -p onidia/characters/%s && save as onidia/characters/%[1]s/%[1]s.go, then add _ %q to main.go's import block",
			req.Name, "github.com/portege/onidia/characters/"+req.Name),
	}); err != nil {
		log.Printf("encode response: %v", err)
	}
}

func httpError(w http.ResponseWriter, code int, err error) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
}

// logRequests is a tiny access log middleware.
func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s (%s)", r.Method, r.URL.Path, time.Since(start).Round(time.Millisecond))
	})
}
