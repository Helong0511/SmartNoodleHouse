# Customer lifecycle write-gate protocol

Status: implemented on PR #217. The latest fully validated code head is `85d524b58598ddf5ce4a39ac40707bdba87b7f54`; CI run [#2897](https://github.com/Helong0511/SmartNoodleHouse/actions/runs/45144165215) passed all 12 jobs. Its MySQL 8 job reported 35 passed and 47 deselected; the backend suite reported 2,217 passed and 50 skipped (60.87% coverage). This confirms the tested CI environment only; it does not mean every item in the broader matrix has a dedicated two-session test or that production schema/lock behavior has been verified.

## Why a separate gate is required

`app.models.customer.CustomerLifecycle` is an analytical RFM/lifecycle snapshot. It is not guaranteed to exist for every customer and is updated by analytics jobs, so it must not be used as a serialization primitive.

Merge acquires sorted CustomerWriteGate rows first, then locks Order, RechargeOrder, outstanding FlashSaleClaim, and Customer rows. Flash-sale claim creation and verification acquire the customer gate before claim/customer locks. Merge cancels outstanding `CLAIMED` claims with `status_reason=CUSTOMER_MERGED`; `USED` claims and historical order references are preserved.

Adding a customer lock after an operation-specific row lock is unsafe: merge can hold an order/recharge parent while waiting for a claim/customer, while a verifier holds the claim/customer and proceeds to create an order.

## Proposed invariant

The implementation uses a dedicated `CustomerWriteGate` table with exactly one durable row per customer:

- `customer_id`: unsigned customer identifier and primary key; the table intentionally has no FK to `customers.id`;
- no timestamp columns or business state; the row exists solely as a transaction mutex.

The lack of an FK is deliberate so test-schema teardown can drop `customers` independently. Production lifecycle integrity is maintained by migration backfill plus same-transaction MySQL `AFTER INSERT` / `AFTER DELETE` triggers; the ORM `after_insert` hook additionally provisions rows for SQLite `create_all()` tests. Because the gate has no FK, operational integrity checks must periodically detect both customers missing gates and orphan gate rows. Orphan rows should be removed only after confirming no customer transaction can still reference the deleted customer.

The gate row must be created in the same transaction as every new customer. A migration must backfill one row for every existing customer before application code depends on it. Do not lazily create missing gate rows in request handlers: concurrent insert-or-lock behavior would make the mutex itself racy.

A helper should lock one or more gate rows using a current read (`SELECT ... FOR UPDATE`), sorted by ascending `customer_id`, and fail closed if any expected row is missing. After acquiring the gate, customer state must be read with a current locked read / refresh before accepting a customer-owned write.

## Global lock order

For operations participating in customer ownership changes, use this order:

1. `CustomerWriteGate` rows, ascending customer ID;
2. operation-specific parent rows, each model in a documented stable primary-key order;
3. `FlashSaleClaim` rows, ascending claim ID where applicable;
4. `Customer` rows, ascending customer ID;
5. dependent detail/ledger rows.

No participating operation may acquire a gate after locking an order, recharge, claim, or customer row.

### Operation requirements

- **Customer merge:** lock source and target gates in sorted order first; then lock orders, recharge orders, outstanding flash-sale claims, and customer rows in stable order. Apply an explicit flash-sale policy in the same transaction. Implemented: invalidate unverified source claims (`status=CANCELLED`, `status_reason=CUSTOMER_MERGED`) rather than silently transferring a time-limited entitlement to another account. Preserve already-used claims and historical order references.
- **POS order create:** acquire the selected customer's gate before customer status validation and before `OrderCreateService.create_order`. No gate is needed for anonymous POS orders.
- **POS bind/unbind:** perform a non-locking read of the order's current customer ID only to determine the gate set; lock old and new customer gates in sorted order; then lock/re-read the order and verify its owner has not changed. If it changed, abort with a retryable conflict rather than proceeding with a stale gate set.
- **Recharge create:** acquire the customer's gate before creating or reusing a recharge order. Payment creation may continue to lock the recharge order; it must derive ownership from the locked recharge row, not the request-scoped customer.
- **Flash-sale claim create:** acquire the customer's gate before checking active status and creating a claim.
- **Flash-sale verify:** read the claim identifier without locking only to discover its customer ID; acquire that customer's gate; then lock and refresh the claim and verify its customer ID is unchanged. Only then create the order/payment. Already-used idempotent responses must also verify the claim under the same gate.
- **Other customer-owned writes:** audit recommendation consent/preferences, points, coupons, queue tickets, dining sessions, wallet debits, and every endpoint that writes a customer ID onto a new order. Each path must participate in this protocol or be proven not to race with merge.

## MySQL 8 regression matrix

Use two independent `AsyncSession` connections against MySQL 8 / InnoDB. Use explicit barriers to exercise both lock acquisition orders, and bounded timeouts so deadlocks/hangs fail the test.

1. Merge vs POS create: both complete; no new order remains attached to the disabled source.
2. Merge vs POS bind: cover old-owner and new-owner gate sets; a stale pre-read yields a retryable conflict.
3. Merge vs recharge create: no recharge is committed to the disabled source.
4. Merge vs recharge payment creation: payment ownership equals the locked recharge owner; the source request never receives the target wallet snapshot.
5. Merge vs flash claim create: no new claim is committed to the disabled source.
6. Merge wins vs flash verify: source claim becomes terminal and no source-owned order/payment is created.
7. Flash verify wins: verification completes before merge; merge preserves the used claim's historical order link and moves ownership-bearing rows consistently.
8. Two simultaneous merges sharing one customer: sorted gate acquisition prevents opposite-order deadlocks.
9. Missing gate row: operation fails closed with an actionable integrity error.

For every case, assert both sessions finish within timeout, inspect committed ownership from a third fresh session, and verify no order/payment/recharge/claim is orphaned or attached to a disabled source account.

## Implementation and CI coverage status

The model, migration/backfill, gate helper, merge integration, POS create/bind, recharge create/payment ownership, flash claim/verify, and recommendation-consent paths are implemented on the PR branch. The MySQL 8 CI job currently runs the following two-session lifecycle regressions:

- Customer order creation vs merge, including both merge-wins and create-wins orderings.
- Opposite-direction customer merges sharing a customer.
- Recharge creation vs merge, including merge-wins and create-wins orderings.
- Customer-associated POS order creation vs merge.
- POS bind/reassignment vs merge for both the new owner and stale old owner.
- Flash-sale claim creation vs merge, including stale authenticated customer and claim-wins ordering.
- Flash-sale verification vs merge, covering merge-cancels-claim and verify-wins/history-preservation orderings.
- Recharge payment creation and settlement vs merge.
- Refund parent-lock wait vs merge gate, plus the recommendation-consent serialization suite and missing-gate fail-closed behavior.

On validated code head `85d524b58598ddf5ce4a39ac40707bdba87b7f54`, CI run `45144165215` passed all 12 jobs; the MySQL-marked job reported 35 passed and 47 deselected. The backend suite reported 2,217 passed and 50 skipped. The CI command is the source of truth for which tests actually execute. Some items in the broader matrix (for example every possible simultaneous-merge topology, all wallet debit paths, and every operational recovery scenario) are not proven merely by this aggregate count.

## Recommendation consent reads and cache coherence

Recommendation reads participate in the same customer lifecycle gate as consent writes:

- `/miniprogram/recommendations` acquires the customer's `CustomerWriteGate` and current `Customer` row before reading a customer-scoped cache. This prevents a cache hit from bypassing an in-flight consent revocation.
- If the current consent is disabled, the endpoint skips customer-scoped cache lookup and returns cold-start recommendations. A stale optional-auth object is not trusted after the current locked read.
- Consent revocation clears derived data and writes cache-invalidation Outbox events in the same transaction. Re-consent also writes a fresh invalidation event so a delayed opt-out invalidation cannot leave old cache payloads usable.
- Redis generation keys remain the fast cross-worker fence. The cache identity additionally includes the latest durable recommendation-cache Outbox event ID, read with a current locking read after the customer gate. Thus a Redis outage/recovery window cannot make an old customer cache key current again while its invalidation event is pending.
- Migration `0053_outbox_aggregate_lookup` adds `idx_oe_aggregate_latest(aggregate_type, aggregate_id, id)` for this current Outbox lookup. Production rollout must apply this migration before deploying the code that depends on it.

The recommendation-read tests cover (1) opted-out requests bypassing customer cache and A/B assignment, (2) authorized cache hits occurring only after the consent gate, (3) durable Outbox epoch inclusion in the cache key, and (4) re-consent scheduling a new invalidation. These are unit/contract regressions; they do not replace a real Redis outage/recovery test in staging.

## Remaining rollout checks

1. Verify the deployed production migration head, trigger definitions, gate-row parity, table DDL/indexes, and relevant `EXPLAIN` plans.
2. Run realistic staging concurrency tests across merge, order/recharge creation, payment, refund, POS bind, and flash-sale flows; inspect InnoDB lock diagnostics and retry behavior.
3. Verify Redis outage/recovery and Outbox retry behavior in the deployed environment.
4. Treat the gate as a code-level serialization invariant, but do not infer production readiness from CI alone.