# Участие в разработке

[English version](./CONTRIBUTING.md)

Orcestr Commerce Solana — публичная платёжная основа, выделенная из продуктовой разработки
Orcestr. Изменения обязаны сохранять явную границу доверия между кошельком, host-приложением,
CommerceXL runtime, RPC-узлом и on-chain доказательством.

## Разработка

Проверки backend:

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv build
```

Проверки frontend:

```bash
cd frontend
npm ci
npm test
npm run pack:dry-run
```

## Что описывать в Pull Request

- изменённый публичный Python- или npm-контракт;
- влияние на payment state, идемпотентность, ownership и finalization;
- влияние cluster, token program, mint extensions, instruction и transaction version;
- изменение схемы и миграции, которой владеет consumer;
- обновления английской и русской документации или UI-текстов;
- целевые тесты и проверенный consumer flow.

Нельзя ослаблять Token-2022 validation ради Legacy Token Program. Wallet callback, строка
signature, состояние `confirmed` или symbol токена не доказывают оплату. Нельзя коммитить
credentials, seed phrase, private key, signed transaction body, capability, production wallet
data и локальные package paths.
