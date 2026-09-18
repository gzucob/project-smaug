# Content strategy playbook

How to write blog posts and project pages for the gzucob blog that go **deeper**
without getting harder to read. The goal of the blog is not to be the most
technical page on the internet — it is to be the most honest and the most
interesting to skim. This playbook is the agreed strategy; every new post and
project page must follow it.

## Core principle: deepen in breadth, not in density

Depth here means **more narrative, more evidence, more decision** — not more
jargon, acronyms or concepts. If a sentence needs the reader to already know
something to make sense, it is failing. The rule of thumb:

> Every paragraph should do ONE of these: tell a moment, show a consequence,
> justify a decision, or admit a mistake. Never two facts squeezed into one
> sentence.

The current material (READMEs, docs, code) is *dense*: each sentence packs a
fact. The job of the writer is to *unpack* — turn each compressed fact into a
real story with context, cause and consequence.

## The three acts (applies to posts AND projects)

Every piece is a story, not a summary:

- **Act I — the problem.** The concrete moment that interrupted the workflow.
  Not "forms are hard" but a scene: "the form went live on a Sunday; by
  Tuesday there were forty spam messages".
- **Act II — the attempts.** Frustrated tries, wrong turns, dead ends. This is
  where empathy lives and where the reader learns what NOT to do. If nothing
  went wrong, you are hiding something.
- **Act III — the final solution and why it works.** The decision made, the
  trade-offs accepted, the result — with evidence.

## First-seconds rule

The title plus the first three sentences must answer, for the reader:

1. "Is this written for someone like me?" (audience in the first beat)
2. "What do I gain by reading it?" (the outcome, stated early)

A cold open (a scene, a moment, a number) beats a conceptual summary. The
reader decides in seconds; spend them on a hook, not on definitions.

## Talking headings

Write headings that tell the story by themselves. Instead of the topic
("Architecture"), write the claim ("Two worlds, one rule: business logic
never travels to the frontend"). The skim reader should be able to read only
the headings and still take a lesson home.

## Depth techniques (the toolbox)

- **One idea per paragraph.** Unpack dense sentences into vignettes with a
  concrete example.
- **Every decision is a mini-story.** Format: *I thought X → I tried Y → I
  switched to Z because of W*, with the cost of each. When two options are
  comparable, use a trade-off table (decision | alternatives | why X | price).
- **Show the artifact.** Paste the real JSON response, the real embed, the
  actual error message, terminal output, commit. Evidence beats adjectives.
- **Name the failure.** Edge cases, the case that almost broke it, the
  migration that went backward. This is the Act II material. It is what makes
  a person feel real to a reader.
- **One idea at a time in lists.** Bullets are for *results*, not for
  compressed paragraphs. A decision in a list is a red flag — it deserves a
  paragraph.
- **One degree beyond.** The piece must be understandable a level *below* the
  target audience: define jargon at first use ("a webhook is just a POST to a
  URL"), keep the reader who is skimming.
- **Metrics that can be cited.** Precise numbers, real measurements, real
  outcomes (`>1000 concurrent conversions`, `+40% revenue`, `10–15 min for a
  30 min video`). They feed both skimmers and LLM citations (GEO).
- **Diagrams for relationships.** For flows, a small ASCII diagram or a
  table beats a paragraph with arrows in prose.

## The honest conclusion

The ending is a reflection, not a marketing sentence. Give the reader the
takeaway: *what you would do differently, what you still don't know, or what
you would change next*. Success stories are fine; perfect stories are not.

---

# Posts

Target: **~100–160 lines**, 2–4 code blocks, tables allowed. PT-BR from start
to finish.

If the post is not a walkthrough, pick its frame and stay in it:

- **How-to** (passo a passo) — steps with outcomes, the order matters.
- **Under the hood** — a mechanism explained, from the outside in.
- **Opinion / reflection** — a thesis with evidence, not a summary of
  choices.

Skeleton:

1. Cold open (Act I scene, first-seconds rule).
2. Context and the problem that started it.
3. The failed approaches (Act II).
4. The solution and the decisions (Act III) — with code/artifacts.
5. The trade-offs and costs (table where it applies).
6. **"O que aprendi"** — the conclusion section, short, with real takeaways.

## Posts: what NOT to do

- Do not open with a conceptual definition ("a webhook is…") — open with the
  moment that needed a webhook.
- Do not wrap up decisions in bullets.
- Do not describe the solution without the alternatives.
- Do not write more than one idea per sentence.
- Do not exaggerate technical vocabulary — define it once.

---

## Projects

Target: **~60–110 lines**, same template for every project. The frontmatter
block (name, description, url, stars, language, topics, updatedAt, featured)
is the "card" — do not change it in the same pass as the body. The body is
the story.

### The six acts for a project page (template)

1. **Cold open** (# + intro, first-seconds rule) — the moment/event that gave
   birth to the project, with a concrete scene.
2. **## Por que existe** — the concrete pain it kills. "Because it was hard"
   is not enough; say **whose pain, what exactly, how it ended without the
   project**.
3. **## O que resolve hoje** — current behavior, user view, bullets as
   *facts with results* (not philosophy decisions).
4. **## A jornada** — how it got here: versions, phases, timeline table when
   there is one. Each phase says *what was validated* and *what that
   changed*.
5. **## Decisões com trade-off abertos** — the map of choices: table or
   mini-narratives ("I though X, tried Y, switched to Z because W"). If the
   repo has ADRs / decision records (issue 85, ADR 00xx), the *why* is the
   real content — tell it.
6. **## O que deu errado / me surpreendeu + estado** — Act II material
   (failures, what broke, embarrassing moments), then *estado atual* and
   *próximos passos* with the reason.

The skeleton is a **template, all six acts** — it applies to every project so
the site feels coherent. But reorder and adapt to the story; what must stay
is: a cold open, a named failure/decision, and an honest current state.

### Projects: what NOT to do

- Do not write a README transcription (stack list, features list only).
- Do not hide decisions behind "Stack: Python" — say why Python here.
- Do not inflate. If there is no traction, say it. The honest section is the
  part that makes a portfolio feel professional.
- Keep the frontmatter intact and consistent with `src/lib/projects.ts`.

---

## How this playbook is used

When creating or rewriting content, the author of the piece must:

1. Read the skill body rules in `SKILL.md` (they still apply).
2. Read this playbook and apply the three acts and the six-act template.
3. When a fact has a real home (a GitHub repo, an issue, a README), consult
   it before writing — never guess numbers, dates or URLs (the AGENTS.md and
   the user provided materials are the source of truth).
4. When a number or a story is not known, ASK the user instead of inventing.