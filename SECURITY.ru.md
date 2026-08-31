# Политика безопасности

[English version](./SECURITY.md)

## Поддерживаемые версии

Пока пакеты находятся в pre-1.0, security-исправления выпускаются из default branch.

## Как сообщить об уязвимости

Не создавайте публичный issue. Сообщите maintainer приватно через GitHub. Укажите пакет и
версию, сценарий атаки, необходимые права, cluster и token program, ожидаемые и фактические
переходы состояния и минимальный пример. Не прикладывайте реальные credentials, capabilities,
private keys, seed phrases или подписанные production-транзакции.

## Критичные области

Отдельное review требуется для ownership заказа, CSRF, утечки capability, идемпотентности,
точного преобразования amount, проверки mint и recipient, декодирования транзакции,
Token-2022 extensions, commitment/finality, повторов signature, поздних платежей, RPC failover,
row locking, outbox delivery, исполнения заказа, reconciliation и release artifacts.

Полная модель угроз описана в [docs/threat-model.md](./docs/threat-model.md).
