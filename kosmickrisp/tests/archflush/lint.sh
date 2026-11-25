#!/bin/bash
# T3 (GPU-free, static): after patch 0043 nothing compiles on the serializer's compiler outside kk_compile_lock.
#   lint.sh <mesa source dir> [libvulkan_kosmickrisp.dylib]
set -eu
SRC="$1"; LIB="${2:-}"; B="$SRC/src/kosmickrisp"; fail=0
# 1. compile sites (pipeline states, MetalFX scalers) exist only in mtl_compiler.m (locked) and mtl_fx.m (plain compiler)
bad=$(grep -rnE 'newComputePipelineState|newRenderPipelineState|ScalerWithDevice:[^]]*compiler:|newLibraryWith(Source|Descriptor)' "$B" --include='*.m' --include='*.c' --include='*.h' \
      | grep -v '/bridge/mtl_compiler.m:' | grep -v '/bridge/mtl_fx.m:' | grep -v '^[^:]*:[0-9]*: *\(/\?\*\|//\)' || true)
[ -z "$bad" ] || { echo "FAIL: compile sites outside mtl_compiler.m / mtl_fx.m:"; echo "$bad"; fail=1; }
# 2. every pipeline compile in mtl_compiler.m is inside the shared lock
for fn in newComputePipelineStateWithDescriptor newRenderPipelineStateWithDescriptor; do
  n=$(grep -c "$fn" "$B/bridge/mtl_compiler.m" || true); echo "info: $fn sites in mtl_compiler.m: $n"
done
rd=$(grep -c 'pthread_rwlock_rdlock(&kk_compile_lock)' "$B/bridge/mtl_compiler.m"); [ "$rd" -ge 2 ] || { echo "FAIL: rdlock sites $rd < 2"; fail=1; }
grep -q 'pthread_rwlock_trywrlock(&kk_compile_lock)' "$B/bridge/mtl_compiler.m" || { echo "FAIL: try-flush missing"; fail=1; }
# 3. mtl_fx.m: every compile site uses `comp`, which is rebound to the plain compiler once, at the top of mtl_fx_scale
grep -q 'comp = (id<MTL4Compiler>)mtl_compiler_plain(' "$B/bridge/mtl_fx.m" || { echo "FAIL: mtl_fx.m comp is not mtl_compiler_plain"; fail=1; }
badfx=$(grep -nE 'ScalerWithDevice:|newComputePipelineState' "$B/bridge/mtl_fx.m" | grep -vE 'compiler:comp\]|\[comp newComputePipelineState' || true)
[ -z "$badfx" ] || { echo "FAIL: mtl_fx.m compile site not on comp:"; echo "$badfx"; fail=1; }
grep -qE 'KK_FX_PLAIN_COMPILER' "$B/bridge/mtl_compiler.m" || { echo "FAIL: gate KK_FX_PLAIN_COMPILER missing"; fail=1; }
# 4. the plain compiler must not get the serializer
awk '/^mtl_compiler_plain\(/{f=1} f&&/pipelineDataSetSerializer/{print "FAIL: serializer attached in mtl_compiler_plain"; exit 1} f&&/^}/{exit}' "$B/bridge/mtl_compiler.m" || fail=1
# 5. symbol present (when a built library is given)
if [ -n "$LIB" ]; then
  nm "$LIB" | grep -q '_mtl_compiler_plain' && echo "ok: mtl_compiler_plain in $LIB" || { echo "FAIL: mtl_compiler_plain not in $LIB"; fail=1; }
fi
[ $fail = 0 ] && echo "ok: archflush lint" || exit 1
