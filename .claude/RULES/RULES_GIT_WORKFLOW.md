# Regras de Commit, Push e PR

## Gate de qualidade (antes de cada commit)
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
Tudo verde antes de commitar. Se o `ruff` estiver bloqueado localmente pela
Smart App Control do Windows, o CI no GitHub cobre — mas resolva o bloqueio
quando puder.

## Commit
- Mensagens em inglês, no imperativo, estilo conventional
  (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
- Um commit = uma unidade lógica de trabalho.

## Push e PR
- `main` é protegida: nada de push direto. Toda mudança vai por branch + PR.
- No primeiro push da branch, abra o PR (draft se ainda em andamento):
  ```bash
  git push -u origin <branch>
  gh pr create --base main --head <branch> --title "..." --body "..."
  ```
- Faça push a cada commit — o CI roda por PR/push.
- Se o CI falhar: corrija na mesma branch.
- Merge: squash (`gh pr merge --squash --delete-branch`).

## Idioma
Código, comentários, commits e PRs em inglês. Português só na documentação e em
texto para usuário.
