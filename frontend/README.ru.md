# Frontend Orcestr Commerce Solana

TypeScript workspace с переиспользуемыми контрактами Solana-платежей, интеграцией React Query и Wallet Standard, а также доступными checkout-компонентами на Orcestr UI.

Пакеты принимают нативный SOL и явно разрешённые Token-2022 активы через transaction request, выпущенный backend-ом. В v0.1 нет пути для static transfer и legacy token. Пакеты не хранят private key, не создают отдельный auth-клиент, не открывают свой WebSocket и не опрашивают статус по таймеру.

## Пакеты

- `@orcestr/commerce-solana-core` — runtime-валидация API, точные суммы, transport-порты, Solana Pay URI и сверка snapshot транзакции.
- `@orcestr/commerce-solana-react` — интеграция с host React Query, общими payment events, Wallet Standard и встроенным inspector на Solana Kit.
- `@orcestr/commerce-solana-ui` — управляемый checkout, QR, выбор кошелька и актива, lifecycle-состояния, полные русские и английские тексты.

## Разработка

Из этой папки:

```bash
npm install
npm run typecheck
npm test
npm run build
npm run pack:dry-run
```

Для обычной разработки используйте `npm install`, а не глобальный link. Соберите этот workspace, временно укажите все три пути в consumer-е и выполните его `npm install` перед запуском dev-сервера:

```json
{
  "dependencies": {
    "@orcestr/commerce-solana-core": "file:../../orcestr-commerce-solana/frontend/packages/core",
    "@orcestr/commerce-solana-react": "file:../../orcestr-commerce-solana/frontend/packages/react",
    "@orcestr/commerce-solana-ui": "file:../../orcestr-commerce-solana/frontend/packages/ui"
  }
}
```

Все три пути нужны, чтобы exact internal dependencies `0.1.0` тоже оставались локальными. До коммита релизного consumer верните registry-версии.

Все пакеты публикуются только как ESM, содержат TypeScript declarations и собственные `LICENSE`, `NOTICE`, `TRADEMARKS.md`, а также README на русском и английском внутри npm tarball.

## Первый релиз npm

После browser-аутентификации через `npm login` выполните проверку и публикуйте в порядке зависимостей:

```bash
npm test
npm run pack:dry-run
npm publish --workspace @orcestr/commerce-solana-core --access public
npm publish --workspace @orcestr/commerce-solana-react --access public
npm publish --workspace @orcestr/commerce-solana-ui --access public
```

Каждый `prepack` заново собирает пакет и копирует legal-файлы в его package root. Последующие CI-релизы должны использовать npm Trusted Publishing и тот же порядок core → react → UI.
