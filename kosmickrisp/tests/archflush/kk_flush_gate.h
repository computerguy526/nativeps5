/* GPU-free model of the compile / flush gate in mtl_compiler.m (kk_compile_lock + kk_st_flush_deferred).
 * Compiles that record into the archive serializer hold the lock SHARED (begin/end); a flush holds it EXCLUSIVE
 * (periodic: try, release/exit: block). tests/archflush/lint.sh checks that mtl_compiler.m still uses exactly these
 * primitives, so this model and the driver cannot drift apart silently. */
#ifndef KK_FLUSH_GATE_H
#define KK_FLUSH_GATE_H
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>

struct kk_gate {
   pthread_rwlock_t lock;
   _Atomic int flushing;           /* 1 while the (fake) serializer is writing */
   _Atomic int in_flight;          /* compiles holding the lock */
   _Atomic uint64_t deferred;      /* try-flush that found compiles in flight */
   _Atomic uint64_t violations;    /* begin granted while flushing (must stay 0) */
   _Atomic uint64_t flushes;
};

static inline void kk_gate_init(struct kk_gate *g)
{
   pthread_rwlock_init(&g->lock, NULL);
   atomic_init(&g->flushing, 0);
   atomic_init(&g->in_flight, 0);
   atomic_init(&g->deferred, 0);
   atomic_init(&g->violations, 0);
   atomic_init(&g->flushes, 0);
}

static inline void kk_gate_begin(struct kk_gate *g)
{
   pthread_rwlock_rdlock(&g->lock);
   if (atomic_load(&g->flushing))
      atomic_fetch_add(&g->violations, 1);
   atomic_fetch_add(&g->in_flight, 1);
}

static inline void kk_gate_end(struct kk_gate *g)
{
   atomic_fetch_sub(&g->in_flight, 1);
   pthread_rwlock_unlock(&g->lock);
}

/* wait=true: blocking flush; false: periodic try-flush, returns false when deferred */
static inline bool kk_gate_flush(struct kk_gate *g, bool wait, void (*fake_serialize)(void *), void *ctx)
{
   if (wait)
      pthread_rwlock_wrlock(&g->lock);
   else if (pthread_rwlock_trywrlock(&g->lock) != 0) {
      atomic_fetch_add(&g->deferred, 1);
      return false;
   }
   atomic_store(&g->flushing, 1);
   if (atomic_load(&g->in_flight) != 0)
      atomic_fetch_add(&g->violations, 1); /* a flush must never overlap a compile */
   if (fake_serialize)
      fake_serialize(ctx);
   atomic_store(&g->flushing, 0);
   atomic_fetch_add(&g->flushes, 1);
   pthread_rwlock_unlock(&g->lock);
   return true;
}
#endif
