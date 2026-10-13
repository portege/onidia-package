// sample.js — the "Load sample" figure, ported 1:1 from Onidia
// (characters/onidia/*.go and its JS twin web/onidia.html) into plain builder
// stroke data: {color, width, points:[[x,y],…]}. No DOM, no state — app.js just
// installs the result into the layers and fills the phrase box.
//
// Outlines are reproduced the way Go's canvas draws them (canvas.go): a dark
// shape of radius/offset r+ow underneath, then the fill at r. Capsules become
// FillCapsule, tapers/ellipses become many-sided polygons, so what exports is
// plain polyline chains on the shared 112x160 grid (CX=56, GroundY=156).
"use strict";

const SAMPLE_ONIDIA = (() => {
  // Palette (onidia/palette.go), written as #RRGGBBAA so the editor — and the
  // generated Go code — recognise the ONIDIA role names (colHair, colSkin…).
  const OUT = "#2F223E";      // colOutlineDark — deep plum outlines
  const OUTSKIN = "#C68664";  // colOutlineSkin — warm skin outline
  const SHADOW = "#190C2332"; // colShadow
  const HAIR = "#5FCFD6";     // colHair — pastel teal
  const SHADE = "#41AEB6";    // colHairShade
  const LIGHT = "#A5ECEF";    // colHairLight
  const TIE = "#FF7A9E";      // colTie
  const SKIN = "#FFE4CE";     // colSkin
  const NOSE = "#EBA58C8C";   // colNose
  const BLUSH = "#FF788746";  // colBlush
  const SCLERA = "#FDFDFF";   // colSclera
  const IRIS = "#363E6E";     // colIris
  const PUPIL = "#181C38";    // colPupil
  const LASH = "#282032";     // colLash
  const MOUTH = "#B0425A";    // colMouth
  const DRESS = "#FF9AAA";    // colDress
  const FRILL = "#FFD6E0";    // colFrill
  const COLLAR = "#FAF8FC";   // colCollar
  const SHOE = "#5C4A6E";     // colShoe

  const CX = 56;
  const TAU = Math.PI * 2;
  const dy = 0; // neutral pose: no bob
  const Y = y => y + dy;

  // -- helpers --
  const r2 = v => Math.round(v * 100) / 100;
  const add = (st, color, width, points) => st.push({ color, width, points });
  const addOne = (st, color, width, x, y) => st.push({ color, width, points: [[x, y]] });
  const arc = (cx, cy, r, a0, a1, n) => {
    const p = [];
    for (let i = 0; i <= n; i++) {
      const a = a0 + (a1 - a0) * i / n;
      p.push([r2(cx + Math.cos(a) * r), r2(cy + Math.sin(a) * r)]);
    }
    return p;
  };
  const disc = (cx, cy, r, n = 32) => arc(cx, cy, r, 0, TAU, n);
  const ellipse = (cx, cy, rx, ry, rot, n = 40) => {
    const cr = Math.cos(rot), sr = Math.sin(rot), p = [];
    for (let i = 0; i <= n; i++) {
      const a = TAU * i / n;
      const x = cx + Math.cos(a) * rx, y = cy + Math.sin(a) * ry;
      p.push([r2(cr * x - sr * y), r2(sr * x + cr * y)]);
    }
    return p;
  };
  const capsule = (x1, y1, x2, y2, r, ow, n = 20) => {
    const ang = Math.atan2(y2 - y1, x2 - x1), R = r + ow;
    const c1 = arc(x1, y1, R, ang + Math.PI / 2, ang - Math.PI / 2, n);
    const c2 = arc(x2, y2, R, ang - Math.PI / 2, ang + Math.PI / 2, n).reverse();
    return c1.concat(c2);
  };
  const taper = (x1, y1, x2, y2, r1, r2, ow, n = 18) => {
    const ang = Math.atan2(y2 - y1, x2 - x1), R1 = r1 + ow, R2 = r2 + ow;
    const c1 = arc(x1, y1, R1, ang + Math.PI / 2, ang - Math.PI / 2, n);
    const c2 = arc(x2, y2, R2, ang - Math.PI / 2, ang + Math.PI / 2, n).reverse();
    return c1.concat(c2);
  };

  // -- layer 1: ground shadow (never bobs; shrinks in place on Hop) --
  const shadow = [];
  // FillEllipse(cx,152, 26, 5) — a squashed blob on the ground. Drawn as a
  // 5-point chain on y=152 so the Go generator's shadowShrink (centred on the
  // blob centroid) shrinks it in place, exactly like the ellipse.
  add(shadow, SHADOW, 10, [[46, 152], [52, 152], [56, 152], [60, 152], [66, 152]]);

  // -- layer 2: back hair (drawTwinTails) --
  const hair_back = [];
  {
    const sway = 0;
    for (const side of [-1, 1]) {
      const ax = CX + side * 25, ay = 55 + dy;
      const ang = side * 22 * Math.PI / 180 + sway;
      const tx = ax + Math.sin(ang) * 38, ty = ay + Math.cos(ang) * 38;
      // TaperOutlined(ax,ay->tx,ty, r1=8, r2=2.6, ow=2.2, colHairShade)
      add(hair_back, SHADE, 4, taper(ax, ay, tx, ty, 8, 2.6, 2.2));
      // glossy streak: FillTaper(ax+side*3, ay+7 -> mix, 2.2->1.0, colHairLight)
      const mx = ax * 0.4 + tx * 0.6, my = ay * 0.4 + ty * 0.6;
      add(hair_back, LIGHT, 4, taper(ax + side * 3, ay + 7, mx, my, 2.2, 1.0, 0));
    }
  }

  // -- layer 3: legs (drawLegs) --
  const legs = [];
  {
    const swing = 0, footY = 147 + dy;
    for (const side of [-1, 1]) {
      const hipX = CX + side * 9, footX = hipX + side * swing, fy = footY;
      // CapsuleOutlined(hip->foot, r=4.6, ow=1.8, colSkin)
      add(legs, SKIN, 2, capsule(hipX, 116 + dy, footX, fy - 2, 4.6, 1.8));
      // EllipseOutlined(foot+1.5, fy+1.5, rx=7.6, ry=4.8, ow=1.8, colShoe)
      add(legs, SHOE, 2, ellipse(footX + 1.5, fy + 1.5, 7.6, 4.8, 0));
    }
  }

  // -- layer 4: body (drawBody): neck, dress, frill, collar --
  const body = [];
  {
    const shY = 96 + dy, hemY = 127 + dy;
    // neck: CapsuleOutlined(cx,86->cx,99, r=4, ow=1.6, colSkin)
    add(body, SKIN, 2, capsule(CX, 86 + dy, CX, 99 + dy, 4, 1.6));
    // dress: trapezoid with a gently sagging hem, PolyOutlined(ow=2.2, colDress)
    const hem = t => [CX + t * 26, Y(hemY + 3 * Math.cos(t * Math.PI / 2))];
    const pts = [[CX - 12, shY], [CX + 12, shY], [CX + 26, hemY]];
    for (let i = 8; i >= -8; i--) pts.push(hem(i / 8));
    add(body, DRESS, 4, pts);
    // frill strip: a lighter band along the hem, FillPoly(colFrill)
    const frill = [];
    for (let i = 8; i >= -8; i--) {
      const t = i / 8;
      frill.push([CX + t * 25, Y(hemY - 8 + 3 * Math.cos(t * Math.PI / 2))]);
    }
    for (let i = -8; i <= 8; i++) frill.push(hem(i / 8));
    add(body, FRILL, 4, frill);
    // collar: small quad, PolyOutlined(ow=1.4, colCollar)
    add(body, COLLAR, 3, [
      [CX - 7, Y(shY + 1)], [CX + 7, Y(shY + 1)],
      [CX + 10, Y(shY + 10)], [CX - 10, Y(shY + 10)],
    ]);
  }

  // -- layer 5: arms (drawArms, idle): arm + puff sleeve each side --
  const arms = [];
  {
    const armSwing = 0;
    for (const side of [-1, 1]) {
      const sx = CX + side * 14, sy = 102 + dy;
      const hx = CX + side * 23 + armSwing * side, hy = 121 + dy;
      // CapsuleOutlined(shoulder->hand, r=4.2, ow=1.7, colSkin)
      add(arms, SKIN, 2, capsule(sx, sy, hx, hy, 4.2, 1.7));
      // puff sleeve: CircleOutlined(sx, sy+1.5, r=6.8, ow=1.7, colDress)
      add(arms, DRESS, 2, disc(sx, sy + 1.5, 6.8));
    }
  }

  // -- layer 6: head (drawHead) --
  const head = [];
  {
    const faceR = 31.5, backR = 36.5, capR = 35.5, capCy = 59.0, browY = 51.0;
    // back hair mass: FillCircle(cx,60, backR+2.2, outline) then (cx,60, backR, hair)
    add(head, OUT, 4, disc(CX, 60 + dy, backR + 2.2));
    add(head, HAIR, 4, disc(CX, 60 + dy, backR));
    // face: CircleOutlined(cx,63, faceR, ow=2.0, colSkin, colOutlineSkin)
    add(head, SKIN, 2, disc(CX, 63 + dy, faceR));
    // bangs: circular segment above the brow line, PolyOutlined(ow=2.2)
    {
      const cyc = capCy + dy;
      let sinT = (cyc - (browY + dy)) / capR;
      sinT = Math.min(Math.max(sinT, 0.05), 0.95);
      const t0 = Math.asin(sinT), n = 22, bang = [];
      for (let i = 0; i <= n; i++) {
        const th = t0 + (Math.PI - 2 * t0) * i / n;
        bang.push([r2(CX + Math.cos(th) * capR), r2(cyc - Math.sin(th) * capR)]);
      }
      const chordHalf = Math.sqrt(Math.max(capR * capR - (cyc - browY - dy) ** 2, 1));
      bang.push([CX - chordHalf, browY + dy]);
      add(head, HAIR, 4, bang);
    }
    // fringe spikes dipping onto the forehead
    for (let i = 0; i < 5; i++) {
      const bx = CX - 24 + i * 12;
      const jit = i % 2 === 0 ? -2.0 : 2.0;
      const tipY = i % 2 === 0 ? 66.0 + dy : 69.0 + dy;
      add(head, HAIR, 3, [[bx - 5, Y(49)], [bx + 5, Y(49)], [bx + jit, tipY]]);
    }
    // side locks framing the face
    add(head, HAIR, 2, capsule(CX - 32, 48 + dy, CX - 28, 88 + dy, 5, 2.0));
    add(head, HAIR, 2, capsule(CX + 32, 48 + dy, CX + 28, 88 + dy, 5, 2.0));
    // ahoge (the single rebellious strand): two filled tapers
    add(head, HAIR, 4, taper(CX - 2, 27 + dy, CX + 6, 15 + dy, 2.8, 1.1, 0));
    add(head, HAIR, 4, taper(CX + 6, 15 + dy, CX + 13, 19 + dy, 1.1, 0.8, 0));
    // hair ties
    add(head, TIE, 2, disc(CX - 27, 52 + dy, 5.4));
    add(head, TIE, 2, disc(CX + 27, 52 + dy, 5.4));
  }

  // -- layer 7: face (drawFace, neutral) --
  const face = [];
  {
    const eyeY = 72 + dy, my = 89 + dy;
    // eyes: open standard anime eye (idx=0, idy=0, ik=1) each side
    for (const side of [-1, 1]) {
      const ex = CX + side * 13, idx = 0, idy = 0, ik = 1;
      // oEllipse(ex,eyeY, 6.2,8.2, ow=1.2, colSclera, colLash)
      add(face, SCLERA, 2, ellipse(ex, eyeY, 6.2, 8.2, 0));
      add(face, IRIS, 4, ellipse(ex + idx, eyeY + 0.8 + idy * ik, 5.0 * ik, 7.0 * ik, 0));
      add(face, PUPIL, 4, ellipse(ex + idx, eyeY + 1.6 + idy * ik, 2.5 * ik, 3.7 * ik, 0));
      addOne(face, SCLERA, 4, r2(ex + idx - 2.2 * side * ik), r2(eyeY + 0.8 + idy * ik - 4.0 * ik));
      addOne(face, SCLERA, 4, r2(ex + idx + 2.0 * side * ik), r2(eyeY + 0.8 + idy * ik + 2.8 * ik));
      add(face, LASH, 3, capsule(ex - 6.4, eyeY - 6.6, ex + 6.4, eyeY - 7.6, 1.9, 0));
      add(face, LASH, 3, capsule(ex + side * 6.2, eyeY - 7.4, ex + side * 9.4, eyeY - 9.4, 1.4, 0));
    }
    // nose dot
    addOne(face, NOSE, 2, r2(CX), r2(82 + dy));
    // neutral blush pair (blushPair, amount 1.0)
    for (const side of [-1, 1]) {
      add(face, BLUSH, 4, ellipse(CX + side * 19, eyeY + 13, 4.6, 2.4, side * 0.4));
    }
    // neutral gentle smile: FillCapsule(cx-4,my -> cx+4,my-0.6, r=1.4, colMouth)
    add(face, MOUTH, 3, [[CX - 4, my], [CX + 4, Y(my - 0.6)]]);
  }

  const layers = [
    { id: "shadow", strokes: shadow },
    { id: "hair_back", strokes: hair_back },
    { id: "legs", strokes: legs },
    { id: "body", strokes: body },
    { id: "arms", strokes: arms },
    { id: "head", strokes: head },
    { id: "face", strokes: face },
  ];

  // Onidia's full 40-line chatter pool, each with the face she says it with
  // (onidia/phrases.go). Exported as "mood: text" for the phrases textarea.
  const phrases = [
    ["happy", "mew~"], ["happy", "hi there!"], ["happy", "nice day, huh?"],
    ["sleepy", "*stretches*"], ["wink", "boop!"], ["happy", "you got this!"],
    ["sleepy", "brb, napping"], ["sad", "feed me?"], ["happy", "keep going!"],
    ["wink", "<3"], ["sleepy", "so sleepy..."], ["thinking", "watcha doing?"],
    ["happy", "la la la~"], ["happy", "high four!"], ["happy", "i can walk!"],
    ["thinking", "hmm... where did my sock go?"], ["anxious", "did i leave the oven on??"],
    ["anxious", "that shadow moved on its own!!"], ["angry", "hey! who took my snack?!"],
    ["angry", "hmph."], ["sad", "ouch... that hurt..."], ["sad", "i'm fine... really..."],
    ["surprised", "?! you startled me!"], ["sleepy", "*yawn* five more minutes"],
    ["fear", "eep!!"], ["fear", "that shadow is back!!"], ["disgust", "ew, gross!"],
    ["disgust", "yuck, no thanks"], ["contempt", "hmph. as if."],
    ["contempt", "sure, keep telling yourself that"], ["confused", "wait... what??"],
    ["confused", "huh? i don't get it"], ["skeptical", "are you sure about that?"],
    ["skeptical", "yeah... right..."], ["embarrassed", "w-wait, don't stare!!"],
    ["embarrassed", "that was so embarrassing..."], ["adore", "awoo~!"],
    ["adore", "you're so warm..."], ["happy", "nailed it!!"],
  ];

  return { name: "onidia2", layers, phrases };
})();
