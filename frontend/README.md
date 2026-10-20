# Frontend do Smaug

Interface web da aplicação: Next.js 15, React 19, Tailwind CSS v4 e TypeScript.
Consulta as análises persistidas pela API; não calcula indicadores financeiros.

Para instalar o Smaug, use o [guia de instalação](../docs/installation.md).
Para preparar os bancos e iniciar a API pelo código, consulte o
[guia de desenvolvimento](../docs/development.md#trabalhar-no-código).

## Executar pelo código

Dentro de `frontend/`, com a API em execução:

```bash
cp .env.example .env.local
npm ci
npm run dev
```

Acesse <http://localhost:3000>. A variável `NEXT_PUBLIC_API_BASE` em `.env.local`
aponta para a API; o padrão é `http://localhost:8000`.

## Verificar alterações

```bash
npm run typecheck
npm run build
```

Pare o servidor de desenvolvimento antes do build, pois ambos usam `.next/`.
As regras de componentes, dados e identidade visual estão em [AGENTS.md](AGENTS.md).
O site de downloads fica em `website/`, separado desta aplicação.
