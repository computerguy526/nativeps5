/* T1: standalone reproduction of the Apple Metal archive crash (-[_MTLBinaryArchive newArchiverIdWithBinaryKey:...],
 * NULL airScript) and proof that patch 0043's compiler split avoids it. Needs a GPU (a few seconds), no game.
 *
 *   archflush_probe [rounds]      forks one child per mode; the parent never touches Metal.
 *
 * Per round (all in the child):
 *   step 1: thread A starts a long pipeline compile C1 on compiler S (serializer attached; unique source text)
 *   step 2: +20 ms thread B calls serializeAsArchiveAndFlushToURL (Metal makes it wait for C1; _serializing is 1 the
 *           whole time)
 *   step 3: +20 ms thread C starts a small compile C2:
 *             canary  C2 on S, no lock            -> the child must die by a signal (documents the Apple bug; if it
 *                                                    survives, a later OS fixed it: reported as NOTE, not a failure)
 *             plain   C2 on P (no serializer)     -> must exit 0   (the 0043 behaviour of MetalFX compiles)
 *             locked  C2 on S through the rwlock  -> must exit 0   (the 0010 behaviour of pipeline compiles)
 * Exit: 0 when plain and locked pass (canary is informational), 1 otherwise. */
#import <Metal/Metal.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static id<MTLDevice> dev;
static id<MTL4Compiler> S, P;
static id<MTL4PipelineDataSetSerializer> ser;
static pthread_rwlock_t gate = PTHREAD_RWLOCK_INITIALIZER;
static int mode; /* 0 canary 1 plain 2 locked */

static NSString *kernel_src(int round, int lines)
{
   NSMutableString *s = [NSMutableString stringWithFormat:
      @"#include <metal_stdlib>\nusing namespace metal;\nkernel void k(device float *o [[buffer(0)]], uint t [[thread_position_in_grid]]) {\n float a = %d.0 + t;\n", round + 1];
   for (int i = 0; i < lines; i++)
      [s appendFormat:@" a = fma(a, %d.%d, sin(a + %d.0)) * cos(a - %d.5);\n", (i % 7) + 1, i % 10, i, i % 13];
   [s appendString:@" o[t] = a;\n}\n"];
   return s;
}

static id<MTLLibrary> lib_for(NSString *src)
{
   NSError *e = nil;
   id<MTLLibrary> l = [dev newLibraryWithSource:src options:nil error:&e];
   if (!l) { fprintf(stderr, "library failed: %s\n", e.localizedDescription.UTF8String); exit(2); }
   return l;
}

static id<MTLComputePipelineState> compile_on(id<MTL4Compiler> c, id<MTLLibrary> l)
{
   MTL4LibraryFunctionDescriptor *fn = [[MTL4LibraryFunctionDescriptor new] autorelease];
   fn.name = @"k";
   fn.library = l;
   MTL4ComputePipelineDescriptor *d = [[MTL4ComputePipelineDescriptor new] autorelease];
   d.computeFunctionDescriptor = fn;
   NSError *e = nil;
   id<MTLComputePipelineState> p = [c newComputePipelineStateWithDescriptor:d compilerTaskOptions:nil error:&e];
   if (!p) { fprintf(stderr, "compile failed: %s\n", e.localizedDescription.UTF8String); exit(2); }
   return p;
}

struct round { id<MTLLibrary> big, small; NSURL *url; };

static void *thA(void *a)
{
   struct round *r = a;
   @autoreleasepool {
      if (mode == 2) pthread_rwlock_rdlock(&gate);
      compile_on(S, r->big);
      if (mode == 2) pthread_rwlock_unlock(&gate);
   }
   return NULL;
}
static void *thB(void *a)
{
   struct round *r = a;
   @autoreleasepool {
      if (mode == 2) pthread_rwlock_wrlock(&gate);
      NSError *e = nil;
      [ser serializeAsArchiveAndFlushToURL:r->url error:&e];
      if (mode == 2) pthread_rwlock_unlock(&gate);
   }
   return NULL;
}
static void *thC(void *a)
{
   struct round *r = a;
   @autoreleasepool {
      if (mode == 2) pthread_rwlock_rdlock(&gate);
      compile_on(mode == 1 ? P : S, r->small);
      if (mode == 2) pthread_rwlock_unlock(&gate);
   }
   return NULL;
}

static int child(int m, int rounds)
{
   mode = m;
   alarm(300);
   @autoreleasepool {
      dev = MTLCreateSystemDefaultDevice();
      if (!dev) { fprintf(stderr, "no Metal device\n"); return 2; }
      MTL4PipelineDataSetSerializerDescriptor *sd = [[MTL4PipelineDataSetSerializerDescriptor new] autorelease];
      sd.configuration = MTL4PipelineDataSetSerializerConfigurationCaptureBinaries;
      ser = [dev newPipelineDataSetSerializerWithDescriptor:sd];
      MTL4CompilerDescriptor *cd = [[MTL4CompilerDescriptor new] autorelease];
      cd.pipelineDataSetSerializer = ser;
      NSError *e = nil;
      S = [dev newCompilerWithDescriptor:cd error:&e];
      P = [dev newCompilerWithDescriptor:[[MTL4CompilerDescriptor new] autorelease] error:&e];
      if (!S || !P || !ser) { fprintf(stderr, "setup failed\n"); return 2; }
      int lines = getenv("PROBE_LINES") ? atoi(getenv("PROBE_LINES")) : 1500;
      for (int i = 0; i < rounds; i++) {
         @autoreleasepool {
            struct round r;
            r.big = lib_for(kernel_src(i * 2, lines));
            r.small = lib_for(kernel_src(i * 2 + 1, 4));
            r.url = [NSURL fileURLWithPath:[NSString stringWithFormat:@"%@/archflush_probe_%d_%d.bin", NSTemporaryDirectory(), getpid(), i]];
            pthread_t a, b, c;
            pthread_create(&a, NULL, thA, &r);
            usleep(20000);
            pthread_create(&b, NULL, thB, &r);
            usleep(20000);
            pthread_create(&c, NULL, thC, &r);
            pthread_join(a, NULL); pthread_join(b, NULL); pthread_join(c, NULL);
            [[NSFileManager defaultManager] removeItemAtURL:r.url error:nil];
         }
      }
   }
   return 0;
}

int main(int argc, char **argv)
{
   int rounds = argc > 1 ? atoi(argv[1]) : 20;
   const char *names[3] = {"canary", "plain", "locked"};
   int bad = 0;
   setvbuf(stdout, NULL, _IOLBF, 0);
   for (int m = 0; m < 3; m++) {
      int canary_rounds = m == 0 ? 3 : rounds;
      pid_t p = fork();
      if (p == 0)
         _exit(child(m, canary_rounds));
      int st = 0;
      waitpid(p, &st, 0);
      if (WIFSIGNALED(st)) {
         if (m == 0) printf("canary: child died by signal %d after <=%d rounds (the Apple bug reproduces, as documented)\n", WTERMSIG(st), canary_rounds);
         else { printf("FAIL %s: child died by signal %d\n", names[m], WTERMSIG(st)); bad = 1; }
      } else if (WEXITSTATUS(st) != 0) {
         printf("FAIL %s: exit %d\n", names[m], WEXITSTATUS(st)); bad = 1;
      } else if (m == 0) {
         printf("NOTE canary: child survived; the bug does not reproduce on this OS (or the compile was too short: raise PROBE_LINES)\n");
      } else {
         printf("ok: %s, %d rounds\n", names[m], rounds);
      }
   }
   return bad;
}
