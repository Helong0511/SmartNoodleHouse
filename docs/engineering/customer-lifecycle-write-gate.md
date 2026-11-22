# Customer lifecycle write-gate protocol

Status: implementation notes for PR #217; the gate and flash-sale merge policy are implemented on the PR branch. MySQL concurrency validation remains in progress.

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

## Rollout sequence

1. Add model + migration/backfill and transactional gate-row creation to every customer creation path.
2. Add the shared gate helper and unit tests for sorted multi-row locking, missing-row behavior, and active-customer refresh.
3. Convert merge first so it acquires gates before all existing parent locks.
4. Convert POS create/bind, recharge create, and flash claim/verify; audit all other customer-owned writes.
5. Add and run the MySQL 8 two-session matrix above.
6. Only after all participating paths are converted and MySQL tests pass, treat the gate as an enforced invariant.

This document records the gate implementation and its intended lock protocol on the PR branch. It does not claim that the full MySQL 8 concurrency matrix has passed: the matrix remains a release gate until each participating path is exercised against the real `CustomerMergeService.merge()` implementation and committed ownership is checked from a third session.