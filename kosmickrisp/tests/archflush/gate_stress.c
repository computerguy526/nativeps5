/* T2: 8 threads x 10k begin/end against a flusher. Asserts: no compile starts while flushing, no flush overlaps a
 * compile, try-flush defers and counts, a blocking flush gets in. Counters only, no timing. */
#include "kk_flush_gate.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static struct kk_gate G;
static _Atomic int stop;
static _Atomic uint64_t begun;

static void fake_serialize(void *ctx) { (void)ctx; usleep(200); }

static void *worker(void *arg)
{
   (void)arg;
   for (int i = 0; i < 10000; i++) {
      kk_gate_begin(&G);
      atomic_fetch_add(&begun, 1);
      if ((i & 63) == 0)
         usleep(50);
      kk_gate_end(&G);
   }
   return NULL;
}

static void *flusher(void *arg)
{
   (void)arg;
   while (!atomic_load(&stop)) {
      kk_gate_flush(&G, false, fake_serialize, NULL);
      usleep(100);
   }
   return NULL;
}

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); exit(1); } } while (0)

int main(void)
{
   kk_gate_init(&G);
   /* deterministic: a held compile makes the try-flush defer, and a blocking flush waits for it */
   kk_gate_begin(&G);
   CHECK(!kk_gate_flush(&G, false, fake_serialize, NULL));
   CHECK(atomic_load(&G.deferred) == 1);
   kk_gate_end(&G);
   CHECK(kk_gate_flush(&G, true, fake_serialize, NULL));
   CHECK(atomic_load(&G.flushes) == 1);

   pthread_t t[8], f;
   pthread_create(&f, NULL, flusher, NULL);
   for (int i = 0; i < 8; i++)
      pthread_create(&t[i], NULL, worker, NULL);
   for (int i = 0; i < 8; i++)
      pthread_join(t[i], NULL);
   atomic_store(&stop, 1);
   pthread_join(f, NULL);
   CHECK(atomic_load(&begun) == 80000);
   CHECK(atomic_load(&G.violations) == 0);
   CHECK(atomic_load(&G.in_flight) == 0);
   CHECK(kk_gate_flush(&G, true, fake_serialize, NULL));
   printf("ok: gate stress (%llu compiles, %llu flushes, %llu deferred, 0 violations)\n",
          (unsigned long long)atomic_load(&begun), (unsigned long long)atomic_load(&G.flushes),
          (unsigned long long)atomic_load(&G.deferred));
   return 0;
}
