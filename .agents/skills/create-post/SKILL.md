---
name: create-post
description: Creates blog posts for the gzucob blog (gzucob.com). Use when the user asks to write, create or publish a post, article or text for the blog, or mentions "post", "blog", "write content" or files under src/content/posts. Also handles creating projects under src/content/projects when requested.
---

# Create Blog Post

Writing skill for Gabriño Zuco Brezolin's personal blog — Next.js with Markdown content rendered by shiki. Content is published in **PT-BR**.

## Location and format

Create the post at `src/content/posts/<slug>.md`:

```markdown
---
title: "Título do post"
description: "Resumo que aparece nas listas."
date: "2026-08-01"
tags: ["backend", "reflexão"]
---

Conteúdo em Markdown... com ```ts code blocks com syntax highlight ```.
```

### Frontmatter rules

| Field | Required | Rule |
| --- | --- | --- |
| `title` | yes | In PT-BR, sentence case. Quoted. |
| `description` | yes | One sentence that summarizes the post — what shows in the lists. Quoted. |
| `date` | yes | Format `"YYYY-MM-DD"`. If the user doesn't give a date, use today's date. Quoted. |
| `tags` | no | Array of strings, 2 to 5 tags, lowercase, mostly accent-free, atomic (e.g.: `["webhooks", "backend", "nextjs"]`). Reuse tags already used in other posts when it makes sense. |

### Slug

- Kebab-case, lowercase, accents-free, no unnecessary articles.
- Concise and descriptive of the theme (e.g., `webhooks-do-zero-discord-como-caixa-de-entrada`).
- If the file already exists in `src/content/posts/`, warn the user and suggest an alternative slug.

### Body rules

1. **PT-BR** from start to finish — including UI code and examples.
2. First paragraph is the introduction: context + what the post solves. No heading repeating the `title`.
3. Hierarchy with `##` for sections; `###` optional for subsections. Do not use `#` in the body.
4. Code blocks with declared language (```ts, ```bash...) — the editor applies syntax highlighting.
5. Links and references in standard Markdown format; do not invent URLs.
6. Personal tone (it's a personal blog), direct, no fluff. A short reflection/conclusion section at the end when it makes sense (`## O que aprendi`).
7. Stay consistent with existing posts in `src/content/posts/` — read at least one existing post before writing to catch the tone.

### Content strategy

**This is the main playbook — read it before writing anything.** The depth strategy (three acts, talking headings, trade-off tables, honest conclusions) lives in [`content-strategy.md`](./content-strategy.md) next to this skill. It applies to posts AND projects. Body rules above are the mechanics (PT-BR, frontmatter, headings); `content-strategy.md` is how to make the content deep without making it harder to read.

## Checklist before finishing

1. Correct path: `src/content/posts/<slug>.md` (`.md` extension, not `.mdx`).
2. `date` in `YYYY-MM-DD`.
3. `description` short and representative.
4. Syntax highlighting: every code block has a declared language.
5. No accentuation errors or unnecessary anglicisms.
6. If the user asks where to find the post (any listing/home), remind them the post shows up automatically — no extra step to publish on the production site (deploy on Vercel).

## New project (when the user asks)

At `src/content/projects/<slug>.mdx`:

```markdown
---
name: "Nome do projeto"
description: "Uma frase de o que ele faz."
url: "https://..."
stars: 0
language: "SaaS · Python"
topics: ["b2b"]
updatedAt: "2026-04-27T20:54:09Z"
featured: true
---

# Nome

História e detalhes do projeto…
```

`featured: true` highlights the project on the home page. File name in kebab-case. Ask the user for any fields they don't provide (`url`, `stars`, `language`, `topics`, `updatedAt`, `featured`) instead of inventing them.