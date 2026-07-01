# Regras de Branch

## Padrão
- Branch default: `main` — protegida: merge só via PR + squash, sem push direto.
- Branches de trabalho usam um prefixo:
  - `feat/<escopo>`, `fix/<escopo>`, `refactor/<escopo>`, `test/<escopo>`,
    `chore/<escopo>`, `docs/<escopo>`
- Escopo em kebab-case, curto e técnico. Preferir < 40 caracteres.

## Uso
- Uma branch por unidade lógica de trabalho.
- Sempre criar a branch a partir de uma `main` atualizada:
  ```bash
  git checkout main
  git pull
  git checkout -b <prefixo>/<escopo>
  ```
- Se a tarefa mistura assuntos não relacionados, quebre antes de implementar.

## Merge
- Todo merge para `main` é squash-merge (política do repositório no GitHub).
- Commits iterativos da branch são achatados — só o commit final do PR entra na
  `main`.
- Após o merge: a branch remota é apagada automaticamente; apague a local:
  `git checkout main && git pull && git branch -d <branch>`.
- Se o CI falhar na branch: corrija na mesma branch — não abra outra branch/PR.

## Idioma
Mensagens de commit e títulos/corpos de PR em inglês. PT-BR só na documentação
e em texto para usuário.
