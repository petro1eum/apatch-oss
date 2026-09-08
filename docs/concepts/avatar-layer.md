# Концепты: слой аватара

Канонические узлы экономики аватаров (RFP-034 §A.2). Источник правды по сущностям —
[Avatar Architecture Canon](../AVATAR-ARCHITECTURE-CANON.md); здесь они выражены как
адресуемые узлы концепт-графа, на которые ссылаются доки, спеки и код.

<!-- @cid:cpt_signed_fact -->
```concept
name: Подписанный факт работы
aliases: [signed fact]
c4: context
realized_by: trust_chain
```
Атом. Всё доказуемое сводится к цепочке подписанных фактов. Владелец — крипто-субстрат
(`trust_chain` / TrustChain Platform); аватар и экономика только ссылаются на него.

<!-- @cid:cpt_contribution_event -->
```concept
name: ContributionEvent
aliases: [contribution, вклад]
c4: component
realized_by:
  - avatar-contract/avatar_contract/contribution_event.py:ContributionEvent
  - apatch/contribution.py:build_event
relations:
  - {rel: depends_on, to: cpt_signed_fact}
  - {rel: constrains, to: cpt_identity_key_id}
invariants:
  - {kind: edge, ref: SCHEMA-LOCKSTEP}
  - {kind: test, ref: economic_barrier}
```
Единственный мост «работа → аватар → экономика» (Канон §7). Одна версионированная схема,
импортируемая и apatch, и HC, — не «две схемы, совпадающие по соглашению». Денег не
содержит (экономический барьер); `avatar_id` == `key_id`; идемпотентна.

<!-- @cid:cpt_avatar -->
```concept
name: Аватар
aliases: [avatar]
c4: component
realized_by: apatch/avatar_compiler.py
relations:
  - {rel: consumes, to: cpt_contribution_event}
invariants:
  - {kind: test, ref: economic_barrier}
```
Портативная, адресуемая по identity money-free проекция подписанной работы одного
subject: `history + WorkEpisodes + CapabilityEstimates + WorkAsset refs + asset_summary`.
Ownership объединяет отдельные human/agent avatars только в портфель и не смешивает
авторство. Проекция вычисляется apatch Avatar Compiler; `trust-chain.ai` и HC Tracker
показывают один HC-owned read-model.

<!-- @cid:cpt_identity_key_id -->
```concept
name: Identity (key_id)
aliases: [key_id, identity]
c4: container
realized_by:
  - apatch/contribution.py:resolve_identity
  - avatar-contract/avatar_contract/identity.py:AliasResolver
```
Первичный ключ личности — `key_id` (sha256 публичного ключа сертификата). `github_login`,
`user_id`, `professional_uuid`, `contributor_id` — алиасы, резолвящиеся в `key_id`.

<!-- @cid:cpt_avatar_economy -->
```concept
name: Экономика аватара
aliases: [economy, PI/GPI]
c4: context
realized_by: HC_Platform
relations:
  - {rel: consumes, to: cpt_avatar}
```
Цена, клиринг, бонус, трансфер. Владелец — HC_Platform. Потребляет проекцию аватара
(не сырой реестр) и обязана учитывать `trust_level`: `claimed`-события в клиринг не входят.

<!-- @sid:claim_one_contract -->
```claim
type: decision
about: [cpt_contribution_event]
verify: {edge: SCHEMA-LOCKSTEP}
```
Один контракт `ContributionEvent`, импортируемый и apatch, и HC (Канон Правило 1). Стоит
именно так, потому что «две схемы по соглашению» молча разъезжаются; держится ребром
`SCHEMA-LOCKSTEP` (SPEC-EDGE-LOCKSTEP-1).
