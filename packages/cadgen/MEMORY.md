# Daemon memory admission

The daemon checks available headroom before starting a worker and before reusing
a warm worker. This is a soft admission estimate, not an allocator limit or a
prediction of a model's geometry cost. The resident-worker cap remains separate.

Linux uses host `MemAvailable`, capped by each visible finite ancestor memory
cgroup's limit minus current usage. It resolves the current process's v1/v2
memory hierarchy and mount mapping; namespace-relative fallback must prove
membership at the mapped group. Swap and guessed cgroup cache reclaim do not
increase this allowance. Other consumers, including Docker in a shared VM,
reduce available headroom. Limits hidden above a namespace's accessible mount
cannot be inspected by this reader.

The Linux policy estimates are a 512 MiB startup floor, a 450 MiB next-job
increment, and a 1 GiB system reserve. Fresh, never-used idle worker measurements
may raise the startup floor. Failed baseline measurement retains the seed;
geometry retained by a used worker never calibrates that startup baseline.

Windows retains its 2 GiB start reservation and 2 GiB system reserve. Its start
reservation already includes work; the separate 450 MiB increment applies to
warm reuse. Availability is the minimum of physical and commit headroom.

Admission compares available bytes against the system reserve, pending starts,
and this request's incremental estimate. Linux demand starts reserve startup
plus job increment; spare imports reserve startup only. Windows pending starts
reserve 2 GiB each. Warm reuse adds no startup cost. Actual worker/descendant RSS
is already reflected in availability and is not charged a second time. Busy
parents retain their resident processes even after yielding a job slot.

Observations occur outside the pool lock. Changed pending reservations invalidate
a sample, and admission resamples observations older than a quarter second.
Insufficient headroom reclaims owned idle workers, retaining retiring survivors
as counted, quarantined residents until observed exit. Waiting demand precedes
speculative spare refill. Missing required Windows/Linux observations refuse
admission explicitly and skip spare warming; other supported daemon platforms
retain resident-count policy without memory observations.

Server requests report admission waiting and allow cancellation while waiting.
An import already started waits for readiness or its separate 120-second startup
timeout before cleanup; a disconnected request does not dispatch work afterward.
Active broker
slots, bounded imports, and owned teardown may justify waiting. A two-second
transition grace tolerates dispatch races without claiming exact dependency
analysis. Admission refuses after no productive progress or the existing
120-second admission allowance. Capacity refusal is terminal; cold fallback
would bypass this policy.

Memory can change between observation and work, and simultaneous admitted jobs
can grow beyond their estimates. Keep build scope proportional, reuse valid
results, and reduce contention before raising limits. This policy changes no
persistent derived artifact or publication rule.
