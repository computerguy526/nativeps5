// getbin: MSL source or .metallib -> MTLBinaryArchive (Mach-O with the final AGX machine code).
// Part of work/agxdis (SHADER X-RAY layer 6). Compiles pipelines only; never draws, dispatches
// or samples GPU counters. Approach follows dougallj/applegpu compiler_explorer_tools
// (BSD-3-Clause, (c) 2021 Dougall Johnson); this file is an independent rewrite.
// Exit codes: 0 ok, 2 usage, 3 cannot read input, 4 MSL/metallib compile error,
//             5 no usable function, 6 pipeline/archive error, 7 no Metal device.
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(void) {
    fprintf(stderr,
        "usage: getbin [-f function]... [--format FMT | --color N=FMT ...] [--depth]\n"
        "              [--no-fast-math] -o out.bin <shader.metal | shader.metallib | ->\n"
        "       getbin --list <shader.metal | shader.metallib>   (prints '<vertex|fragment|kernel> <name>' lines)\n"
        "       getbin --info                                   (prints the Metal device name)\n"
        "  FMT  rgba8unorm bgra8unorm rgba16float rgba32float r32float rg16float rg11b10float rgb10a2unorm\n"
        "       rgba8uint rgba8sint rgba16uint rgba32uint rgba32sint r32uint r32sint rg32uint rg32sint\n"
        "       r8unorm rg8unorm (default colour 0 = rgba8unorm)\n"
        "  -f   pick entry points (default: the single kernel, or the single vertex+fragment pair)\n"
        "  exit: 0 ok, 2 usage, 3 unreadable input, 4 compile error, 5 no usable function, 6 pipeline/archive, 7 no device\n");
    exit(2);
}

static MTLPixelFormat fmt_of(const char *s) {
    if (!strcasecmp(s, "rgba8unorm")) return MTLPixelFormatRGBA8Unorm;
    if (!strcasecmp(s, "bgra8unorm")) return MTLPixelFormatBGRA8Unorm;
    if (!strcasecmp(s, "rgba16float")) return MTLPixelFormatRGBA16Float;
    if (!strcasecmp(s, "rgba32float")) return MTLPixelFormatRGBA32Float;
    if (!strcasecmp(s, "r32float")) return MTLPixelFormatR32Float;
    if (!strcasecmp(s, "r8unorm")) return MTLPixelFormatR8Unorm;
    if (!strcasecmp(s, "rg8unorm")) return MTLPixelFormatRG8Unorm;
    if (!strcasecmp(s, "rg32uint")) return MTLPixelFormatRG32Uint;
    if (!strcasecmp(s, "rg32sint")) return MTLPixelFormatRG32Sint;
    if (!strcasecmp(s, "r32sint")) return MTLPixelFormatR32Sint;
    if (!strcasecmp(s, "rg16float")) return MTLPixelFormatRG16Float;
    if (!strcasecmp(s, "rg11b10float")) return MTLPixelFormatRG11B10Float;
    if (!strcasecmp(s, "rgb10a2unorm")) return MTLPixelFormatRGB10A2Unorm;
    if (!strcasecmp(s, "rgba8uint")) return MTLPixelFormatRGBA8Uint;
    if (!strcasecmp(s, "rgba8sint")) return MTLPixelFormatRGBA8Sint;
    if (!strcasecmp(s, "rgba16uint")) return MTLPixelFormatRGBA16Uint;
    if (!strcasecmp(s, "rgba32uint")) return MTLPixelFormatRGBA32Uint;
    if (!strcasecmp(s, "rgba32sint")) return MTLPixelFormatRGBA32Sint;
    if (!strcasecmp(s, "r32uint")) return MTLPixelFormatR32Uint;
    fprintf(stderr, "getbin: unknown pixel format '%s'\n", s);
    exit(2);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        const char *out = NULL, *in = NULL;
        NSMutableArray<NSString *> *want = [NSMutableArray array];
        MTLPixelFormat fmt = MTLPixelFormatRGBA8Unorm;
        BOOL fast = YES, info = NO, list = NO, depth = NO;
        MTLPixelFormat colors[8] = {0};
        BOOL anyColor = NO;
        for (int i = 1; i < argc; i++) {
            const char *a = argv[i];
            if (!strcmp(a, "-o") && i + 1 < argc) out = argv[++i];
            else if (!strcmp(a, "-f") && i + 1 < argc) [want addObject:@(argv[++i])];
            else if (!strcmp(a, "--format") && i + 1 < argc) fmt = fmt_of(argv[++i]);
            else if (!strcmp(a, "--no-fast-math")) fast = NO;
            else if (!strcmp(a, "--depth")) depth = YES;
            else if (!strcmp(a, "--color") && i + 1 < argc) {
                const char *v = argv[++i];
                const char *eq = strchr(v, '=');
                int idx = atoi(v);
                if (!eq || idx < 0 || idx > 7) usage();
                colors[idx] = fmt_of(eq + 1);
                anyColor = YES;
            }
            else if (!strcmp(a, "--info")) info = YES;
            else if (!strcmp(a, "--list")) list = YES;
            else if (a[0] == '-' && a[1]) usage();
            else if (!in) in = a;
            else usage();
        }
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { fprintf(stderr, "getbin: no Metal device\n"); return 7; }
        if (info) { printf("%s\n", dev.name.UTF8String); return 0; }
        if (!in || (!out && !list)) usage();

        NSData *data;
        if (!strcmp(in, "-")) data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
        else data = [NSData dataWithContentsOfFile:@(in)];
        if (!data || data.length == 0) { fprintf(stderr, "getbin: cannot read input '%s' (missing or empty)\n", in); return 3; }

        NSError *err = nil;
        id<MTLLibrary> lib = nil;
        if (data.length >= 4 && !memcmp(data.bytes, "MTLB", 4)) {
            dispatch_data_t dd = dispatch_data_create(data.bytes, data.length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
            lib = [dev newLibraryWithData:dd error:&err];
            if (!lib) { fprintf(stderr, "getbin: bad metallib: %s\n", err.localizedDescription.UTF8String); return 4; }
        } else {
            NSString *src = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
            if (!src) { fprintf(stderr, "getbin: input is neither a metallib nor UTF-8 MSL text\n"); return 4; }
            MTLCompileOptions *o = [MTLCompileOptions new];
            if (!fast) o.mathMode = MTLMathModeSafe;
            lib = [dev newLibraryWithSource:src options:o error:&err];
            if (!lib) { fprintf(stderr, "getbin: MSL compile failed: %s\n", err.localizedDescription.UTF8String); return 4; }
        }

        if (list) {
            for (NSString *n in [lib.functionNames sortedArrayUsingSelector:@selector(compare:)]) {
                id<MTLFunction> f = [lib newFunctionWithName:n];
                const char *k = !f ? NULL : f.functionType == MTLFunctionTypeVertex ? "vertex"
                              : f.functionType == MTLFunctionTypeFragment ? "fragment"
                              : f.functionType == MTLFunctionTypeKernel ? "kernel" : NULL;
                if (k) printf("%s %s\n", k, n.UTF8String);
            }
            return 0;
        }
        id<MTLFunction> vs = nil, fs = nil, cs = nil;
        for (NSString *n in lib.functionNames) {
            if (want.count && ![want containsObject:n]) continue;
            id<MTLFunction> f = [lib newFunctionWithName:n];
            if (!f) continue;
            id<MTLFunction> __strong *slot = f.functionType == MTLFunctionTypeVertex ? &vs
                                           : f.functionType == MTLFunctionTypeFragment ? &fs
                                           : f.functionType == MTLFunctionTypeKernel ? &cs : NULL;
            if (!slot) continue;
            if (*slot) {
                fprintf(stderr, "getbin: more than one entry point of the same stage (%s, %s); pick with -f\n",
                        (*slot).name.UTF8String, n.UTF8String);
                return 5;
            }
            *slot = f;
        }
        for (NSString *n in want)
            if (![lib.functionNames containsObject:n]) { fprintf(stderr, "getbin: function '%s' not in library\n", n.UTF8String); return 5; }
        if (!vs && !fs && !cs) { fprintf(stderr, "getbin: no vertex/fragment/kernel entry point found\n"); return 5; }
        if (fs && !vs) { fprintf(stderr, "getbin: a fragment function needs a vertex function in the same input\n"); return 5; }

        id<MTLBinaryArchive> arc = [dev newBinaryArchiveWithDescriptor:[MTLBinaryArchiveDescriptor new] error:&err];
        if (!arc) { fprintf(stderr, "getbin: cannot create binary archive: %s\n", err.localizedDescription.UTF8String); return 6; }
        if (vs) {
            MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
            d.vertexFunction = vs;
            d.fragmentFunction = fs;
            if (fs) {
                if (!anyColor) colors[0] = fmt;
                for (int c = 0; c < 8; c++)
                    if (colors[c]) d.colorAttachments[c].pixelFormat = colors[c];
                if (depth) d.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
            } else {
                // vertex-only: a depth-only pass keeps rasterisation on (a non-void vertex return needs it)
                d.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
            }
            if (![arc addRenderPipelineFunctionsWithDescriptor:d error:&err]) {
                fprintf(stderr, "getbin: render pipeline failed: %s\n", err.localizedDescription.UTF8String); return 6;
            }
        }
        if (cs) {
            MTLComputePipelineDescriptor *d = [MTLComputePipelineDescriptor new];
            d.computeFunction = cs;
            if (![arc addComputePipelineFunctionsWithDescriptor:d error:&err]) {
                fprintf(stderr, "getbin: compute pipeline failed: %s\n", err.localizedDescription.UTF8String); return 6;
            }
        }
        if (![arc serializeToURL:[NSURL fileURLWithPath:@(out)] error:&err]) {
            fprintf(stderr, "getbin: cannot write '%s': %s\n", out, err.localizedDescription.UTF8String); return 6;
        }
        return 0;
    }
}
