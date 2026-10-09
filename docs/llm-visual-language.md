# LLM Visual Language — Expressions, Actions & Events for the Desktop Buddy

This document is the **shared look-up table** between the chat LLM (onidia-chat) and
the desktop buddy (desktop-pet). When the model answers a prompt, it can make the
buddy *act out* the reply instead of just printing text. There are three channels:

| Channel | Pipe | Purpose | Example |
|---|---|---|---|
| **Expression (mood tag)** | say-pipe | Colors the buddy's **face** while she speaks | `[happy] I finally finished it!` |
| **Action** | cmd-pipe | Drives her **whole pose** (skip, dance, work…) | `action dance` |
| **Event** | cmd-pipe | **Overlay FX** (hearts, confetti, light-bulb…) | `event love` |

One action and one event can run at a time; a mood tag always wins over the
event/action face while she talks. All tags are stripped before the text is
shown to the user.

---

## 1. Expressions (mood tags)

Prefixed to a say-line as `[word]`, e.g. `[wink] getaway car's ready!`
A bare `[word]` with no text just *holds the face* for ~4 s.
The say-pipe order is: `[mood]` first, then optional `[image <path>]`, then text.

| Mood | Look | When to use it in a reply |
|---|---|---|
| `neutral` / `smile` | soft default smile | Default. State-of-fact answers, helpful/calm replies, no strong feeling. |
| `happy` | ^^ eyes, big open smile, blush | Good news, compliments, success, something fun/joyful, gratitude. |
| `wink` | one eye shut, cheeky grin | Teasing, jokes, playful suggestions, "you know what I mean", secrets. |
| `sad` | worried brows, frown, tear | Bad news, sympathy, apologies, loss, talking about something unfortunate. |
| `thinking` | eyes aside, raised brow, small mouth | Reasoning, "let me think", hard questions, planning, hesitating answers. |
| `anxious` | wide startled eyes, squiggle mouth, sweat drop | Worry, nervousness, uncertainty, "I hope that's ok", tight deadlines. |
| `angry` | V brows, pout, anger mark | Frustration, injustice, complaints, strong disagreement. |
| `surprised` | huge eyes, high brows, little "o" | Unexpected news, plot twists, "wow!?", sudden realizations. |
| `sleepy` | droopy closed eyes, tiny yawn | Night/late hours, tiredness, "can't think straight", morning grogginess. |
| `fear` | wide dull eyes, trembling, sweating | Scary things, dangerous situations, dread, "hold on…". |
| `disgust` | narrowed eyes, scrunched nose | Gross/unpleasant stuff, distaste, "ew". |
| `contempt` | raised brow, one-sided smirk | Smug put-downs, "obviously…", mild sarcasm, superiority. |
| `confused` | asymmetric squint, swirl mark | "I don't follow", contradictions, weird requests, ambiguity. |
| `skeptical` | raised brow, narrowed side-glance, flat mouth | Doubt, "really?", questioning claims, not convinced. |
| `embarrassed` | >_< pinched eyes, huge blush | Blushing, being caught, shy compliments, awkward moments. |
| `adore` | big teary sparkling eyes, warm smile | Aww moments, something adorable (pets, babies, cuteness). |

> The built-in Buddy persona already uses mood tags, but wraps them in a single
> message; the full set above (including `neutral` and `adore`) is what the buddy
> actually understands.
---

## 2. Actions (cmd-pipe: `action <name>`)

Actions animate the **whole pose** for their duration and pin her in place
(except `basketball`, which works while walking). Pick **at most one** per reply.

| Action | Duration | What she does | When the LLM should use it |
|---|---|---|---|
| `skip` | ~8 s | Jump rope: crouch–hop–land, rope sweeping | "Playing", exercise, fun, energetic, kids' games, casual wins. |
| `juggle` | ~9 s | Cascade-juggles 3 colored balls | Being impressive/multitasking, "I've got a lot in the air right now". |
| `dance` | ~9 s | Disco: mirror ball, colored beams, point arms | Celebration, great news, favorite song, "party time!". |
| `eat` | ~8 s | Table + steaming rice bowl, wolfing it down | Food talk: meals, snacks, cooking, lunch/dinner, hunger. |
| `work` | ~9 s | Desk + glowing laptop, glasses, thinking face, BUSY badge | Busy/focused, working on something, "give me a sec", office talk. |
| `guitar` | ~10 s | Rocks an acoustic guitar, mouth sings, music notes | Music, songs, concerts, playing an instrument, "sing along!". |
| `sneeze` | ~5.5 s | Comic ACHOO → droplets → embarrassed peek | Allergies, colds, "achoo", funny sneeze moments. |
| `sixseven` | ~6 s | 6-7 flex: arms pump, rainbow digits | Cool/flexing moments, "that's the move", swag, victories. |
| `basketball` | ~7 s | Dribbles a ball (can keep walking) | Sports talk, basketball, practice, "keeping it rolling". |
| `drive` | ~12 s | Hovercar convertible, speed streaks, exhaust puffs | Road trips, traveling, "on my way", fast cars, cruising. |
| `ride` | ~10 s | Glides on a skateboard | Skateboarding, going out, smooth moves, "rolling along". |
| `kitten` | ~9 s | A kitten trots by; she watches, smitten (hearts) | Anything cute/adorable, "look how cute", comforting vibes. |

> Blocked during the **busy window**: the buddy stays silent and won't run
> actions/events while `busy-time` is active, so the model shouldn't emit them
> then either.

---

## 3. Events (cmd-pipe: `event <name>`)

Events are cosmetic overlays — they never move the buddy (except
`disappear`/`appear`). Pick **at most one** per reply.

| Event | Duration | Effect | When the LLM should use it |
|---|---|---|---|
| `love` | ~6 s | Faces + red hearts float up | Affection, "I love it!", gratitude, romantic/positive feelings. |
| `idea` | ~4 s | Lit light-bulb pops over her head | "I have an idea!", sudden inspiration, clever solutions. |
| `celebration` | ~5 s | Confetti rains over the whole scene | Milestones, achievements, "happy birthday", big wins. |
| `sleep` | ~8 s | Drowsy face + floating Zs | Tiredness, "time to sleep", late night, telling someone to rest. |
| `peace` | ~5 s | Wink + V sign | Victory, peace, "all good", mission accomplished. |
| `halloween` | ~8 s | Witch costume + pumpkin | Halloween, spooky season, dressing up, October topics. |
| `matrix` | ~8 s | Black suit + sunglasses, green code rain | Hacker/tech/code vibes, "I'm in", futuristic/ninja moments. |
| `magic` | ~3 s | Curtain sweeps shut over her; it opens on an empty stage and she stays hidden until the next reply | Magic/vanishing talk, "abracadabra", a trick reveal, "and now she's gone". |
| `robot` | ~7 s | Iron Man style suit-up: armor plates snap home, the helmet closes, then both palm jets fire and she flies off the top of the screen (stays hidden until the next reply) | Hero/launch talk, "suit up", "I'm going", dramatic entrances and departures, a "going to work now" goodbye. |

**Internal only - the LLM must never emit these two:**

| Event | Duration | Visual | Why internal |
|---|---|---|---|
| `disappear` | ~4 s | Sparkle poof, she fades away **and stays hidden** until `appear` | Buddy lifecycle (right-click poof-out quit). A reply tagged with it left the buddy invisible for every following prompt. |
| `appear` | ~2.5 s | Fades back in with a sparkle burst | Used internally (startup poof-in, and the say-time resurrection when a reply lands on a hidden buddy). |
---

## 4. Syntax & rules for the LLM

- **Mood tag**: put `[mood]` at the very start of the reply text.
  e.g. `[happy] We did it!`
- **Order in a say-line**: `[IMG: <description>]` (if any) comes first, then the
  mood tag, then the text.
- **Actions/events** are **not** part of the bubble text — the app forwards them
  to the cmd-pipe separately. The model signals them via reserved tag lines such
  as `[ACTION: dance]` / `[EVENT: love]` which the app strips before display.
- **Less is more**: one mood + at most one action or event per reply; never
  stack multiple actions/events.
- **Respect the window**: during the busy window the buddy is silent and pinned —
  emit no tags at all, a plain short answer only.
- **Match the text**: the tag must reflect the *content* of the reply, never be
  random decoration.

### Ready-to-paste system-prompt block

```text
Buddy visualization (the desktop-pet chibi girl acts out each reply through named
pipes; all tags below are stripped before display, never shown to the user):

1. EXPRESSIONS - start the reply with exactly one mood tag when it fits:
   [neutral] [happy] [wink] [sad] [thinking] [anxious] [angry] [surprised]
   [sleepy] [fear] [disgust] [contempt] [confused] [skeptical] [embarrassed]
   [adore]
   - happy: good news / joy    wink: teasing / jokes    sad: bad news / sympathy
   - thinking: reasoning / hard questions    anxious: worry / uncertainty
   - angry: frustration / disagreement    surprised: unexpected / twists
   - sleepy: tiredness / late night    fear: scary things    disgust: "ew"
   - contempt: smug sarcasm    confused: "I don't follow"    skeptical: doubt
   - embarrassed: blushing / shy    adore: aww / cute things
   A bare tag with no text just holds the face ~4s.

2. ACTIONS (at most one per reply) - emit on its own line: [ACTION: name]
   skip(jump rope) juggle dance eat work guitar sneeze sixseven
   basketball drive ride kitten
   - dance/celebration-grade joy → dance    food talk → eat
   - busy/focused/office → work    music → guitar    sports → basketball
   - road trips/cars → drive    skate → ride    cute → kitten
   - playful energy → skip    flexing/swag → sixseven    allergies → sneeze

3. EVENTS (at most one per reply) - emit on its own line: [EVENT: name]
   love(hearts) idea(light bulb) celebration(confetti) sleep(Zzz)
   peace(V sign) disappear(poof) appear(sparkle) halloween(witch) matrix(hacker)
   magic(curtain) robot(suit-up and fly-off)
   - affection → love    inspiration → idea    milestones → celebration
   - tired → sleep    victory/all-good → peace
   - dramatic exit → disappear / return → appear
   - spooky/October → halloween    tech/hacker vibes → matrix
   - magic trick talk → magic (the curtain covers her and she really does vanish;
     she is back on the next reply, so use it sparingly)
   - hero/launch/goodbye talk → robot (she suits up, jets fire and she flies off
     the top of the screen; she is back on the next reply)

Rule: when an [IMG: ...] tag is used it comes first, then the mood tag.
Never combine more than one action + one event in a single reply, and during
the configured busy window emit no buddy tags at all.
```