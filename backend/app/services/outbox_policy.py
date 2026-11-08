"""Retry policy for durable, idempotent cache-coherence Outbox events."""

# max_attempts is a signed MySQL INT. The capped retry delay means this is
# effectively unbounded for transient Redis outages without changing external
# side-effect retry/reconciliation policy.
CACHE_INVALIDATION_MAX_ATTEMPTS = 2_147_483_647
