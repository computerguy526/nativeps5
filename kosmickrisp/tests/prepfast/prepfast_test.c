/* Host-only tests of kk_libkey.h (stamp / key text) and kk_prefault.h (parallel pre-fault); no Metal, no GPU. */
#include "kk_libkey.h"
#include "kk_prefault.h"

#include <assert.h>

static int failures;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static void
test_archive_stamp(void)
{
   char a[256], b[256], c[256], classic[256];
   /* gate off (epoch 0): the text is exactly what the driver wrote before prepfast, so existing stores keep their directory */
   kk_archive_stamp_text(classic, sizeof(classic), 0, "25.3.0", "-devel", "25F71", "Apple M4 Pro");
   CHECK(!strcmp(classic, "fmt=1;kk=25.3.0-devel;os=25F71;gpu=Apple M4 Pro;"));
   /* gate off: another KK build = another stamp (the problem) */
   kk_archive_stamp_text(a, sizeof(a), 0, "25.3.0", "-devel-sha-A", "25F71", "Apple M4 Pro");
   kk_archive_stamp_text(b, sizeof(b), 0, "25.3.0", "-devel-sha-B", "25F71", "Apple M4 Pro");
   CHECK(strcmp(a, b) != 0);
   /* gate on: two KK builds, same epoch = same stamp; another epoch / OS build / GPU = another stamp; never equal to the classic one */
   kk_archive_stamp_text(a, sizeof(a), 1, "25.3.0", "-devel-sha-A", "25F71", "Apple M4 Pro");
   kk_archive_stamp_text(b, sizeof(b), 1, "25.3.0", "-devel-sha-B", "25F71", "Apple M4 Pro");
   CHECK(!strcmp(a, b));
   kk_archive_stamp_text(c, sizeof(c), 2, "25.3.0", "-devel-sha-A", "25F71", "Apple M4 Pro");
   CHECK(strcmp(a, c) != 0);
   kk_archive_stamp_text(c, sizeof(c), 1, "25.3.0", "-devel-sha-A", "25F72", "Apple M4 Pro");
   CHECK(strcmp(a, c) != 0);
   kk_archive_stamp_text(c, sizeof(c), 1, "25.3.0", "-devel-sha-A", "25F71", "Apple M5");
   CHECK(strcmp(a, c) != 0);
   CHECK(strcmp(a, classic) != 0);
}

static void
test_libkey(void)
{
   char s1[128], s2[128], k1[33], k2[33], k3[33];
   const char *msl = "kernel void f() {}\n";
   /* gate off: the stamp carries the KK build, so the key changes with it */
   kk_libkey_stamp_text(s1, sizeof(s1), false, "25.3.0", "-A", "25F71");
   kk_libkey_stamp_text(s2, sizeof(s2), false, "25.3.0", "-B", "25F71");
   CHECK(!strcmp(s1, "kk=25.3.0-A;os=25F71;"));
   kk_libkey_compute(s1, "Apple M4 Pro", msl, 0x30000, 1, 1, k1);
   kk_libkey_compute(s2, "Apple M4 Pro", msl, 0x30000, 1, 1, k2);
   CHECK(strcmp(k1, k2) != 0);
   /* gate on: equal MSL + options under two fake build strings (and two minor OS builds of one major) = equal key */
   kk_libkey_stamp_text(s1, sizeof(s1), true, "25.3.0", "-A", "25F71");
   kk_libkey_stamp_text(s2, sizeof(s2), true, "25.3.0", "-B", "25F80");
   CHECK(!strcmp(s1, s2));
   kk_libkey_compute(s1, "Apple M4 Pro", msl, 0x30000, 1, 1, k1);
   kk_libkey_compute(s2, "Apple M4 Pro", msl, 0x30000, 1, 1, k2);
   CHECK(!strcmp(k1, k2));
   /* one changed byte of the text, an option, the GPU, or the OS major = another key: a stale file under an old key can never be
    * loaded for different MSL, because the key covers the text */
   const char *msl2 = "kernel void g() {}\n";
   kk_libkey_compute(s1, "Apple M4 Pro", msl2, 0x30000, 1, 1, k3);
   CHECK(strcmp(k1, k3) != 0);
   kk_libkey_compute(s1, "Apple M4 Pro", msl, 0x30000, 2, 1, k3);
   CHECK(strcmp(k1, k3) != 0);
   kk_libkey_compute(s1, "Apple M4 Pro", msl, 0x30000, 1, 0, k3);
   CHECK(strcmp(k1, k3) != 0);
   kk_libkey_compute(s1, "Apple M5", msl, 0x30000, 1, 1, k3);
   CHECK(strcmp(k1, k3) != 0);
   kk_libkey_stamp_text(s2, sizeof(s2), true, "25.3.0", "-A", "26A5");
   kk_libkey_compute(s2, "Apple M4 Pro", msl, 0x30000, 1, 1, k3);
   CHECK(strcmp(k1, k3) != 0);
   /* content-mode and classic keys never collide */
   kk_libkey_stamp_text(s2, sizeof(s2), false, "25.3.0", "-A", "25F71");
   kk_libkey_compute(s2, "Apple M4 Pro", msl, 0x30000, 1, 1, k3);
   CHECK(strcmp(k1, k3) != 0);
   char major[16];
   kk_os_major("25F71", major);
   CHECK(!strcmp(major, "25"));
   kk_os_major("?", major);
   CHECK(!strcmp(major, "?"));
}

static void
test_prefault(void)
{
   char path[] = "/tmp/kk-prefault-XXXXXX";
   int fd = mkstemp(path);
   CHECK(fd >= 0);
   const size_t size = (size_t)64 << 20;
   CHECK(ftruncate(fd, (off_t)size) == 0);
   char *buf = malloc(1 << 20);
   memset(buf, 0x5a, 1 << 20);
   for (size_t off = 0; off < size; off += (size_t)1 << 20)
      CHECK(pwrite(fd, buf, 1 << 20, (off_t)off) == (ssize_t)(1 << 20));
   free(buf);
   close(fd);

   /* 64 MiB with 8 MiB pieces: 8 pieces, every one visited, every page accounted for (touched or already cached) */
   struct kk_prefault_stats st = {0};
   const char *paths[1] = {path};
   uint64_t n = kk_prefault_files(paths, 1, (size_t)8 << 20, &st);
   CHECK(n == 8);
   CHECK(atomic_load(&st.pieces) == 8);
   const uint64_t pages = size / (uint64_t)getpagesize();
   CHECK(atomic_load(&st.pages_touched) + atomic_load(&st.pages_resident) == pages);

   /* a second call finds it all cached */
   struct kk_prefault_stats st2 = {0};
   CHECK(kk_prefault_files(paths, 1, (size_t)8 << 20, &st2) == 8);
   CHECK(atomic_load(&st2.pages_touched) == 0);

   /* a file shorter than a piece = one short piece; an empty file and a missing file = nothing, no crash */
   char path2[] = "/tmp/kk-prefault-XXXXXX";
   int fd2 = mkstemp(path2);
   CHECK(write(fd2, "abc", 3) == 3);
   close(fd2);
   char path3[] = "/tmp/kk-prefault-XXXXXX";
   int fd3 = mkstemp(path3);
   close(fd3);
   const char *more[3] = {path2, path3, "/nonexistent/kk-segment.mtl4a"};
   struct kk_prefault_stats st3 = {0};
   CHECK(kk_prefault_files(more, 3, (size_t)8 << 20, &st3) == 1);
   CHECK(atomic_load(&st3.pieces) == 1);
   CHECK(kk_prefault_files(more, 0, 0, NULL) == 0);
   unlink(path);
   unlink(path2);
   unlink(path3);
}

int
main(void)
{
   test_archive_stamp();
   test_libkey();
   test_prefault();
   if (failures) {
      fprintf(stderr, "prepfast_test: %d failure(s)\n", failures);
      return 1;
   }
   printf("prepfast_test: all passed\n");
   return 0;
}
