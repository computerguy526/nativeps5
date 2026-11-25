#include <stdio.h>
#include <string.h>
#include "kk_msl_memflags.h"

static int valid(const char *s)
{
   return s && (!strcmp(s, "mem_flags::mem_texture") || !strcmp(s, "mem_flags::mem_none") ||
                !strcmp(s, "mem_flags::mem_threadgroup") || !strcmp(s, "mem_flags::mem_device"));
}

int main(void)
{
   int bad = 0, n = 0;
   /* every single mode bit of a 32-bit nir_variable_mode */
   for (unsigned i = 0; i < 32; i++) {
      const char *s = kk_msl_mem_flag_for_mode((nir_variable_mode)(1u << i));
      n++;
      if (!valid(s)) { printf("FAIL bit %u -> %s\n", i, s ? s : "(null)"); bad++; }
   }
   if (strcmp(kk_msl_mem_flag_for_mode(nir_var_image), "mem_flags::mem_texture")) { puts("FAIL image"); bad++; }
   if (strcmp(kk_msl_mem_flag_for_mode(nir_var_mem_shared), "mem_flags::mem_threadgroup")) { puts("FAIL shared"); bad++; }
   if (strcmp(kk_msl_mem_flag_for_mode(nir_var_function_temp), "mem_flags::mem_none")) { puts("FAIL temp"); bad++; }
   if (strcmp(kk_msl_mem_flag_for_mode(nir_var_mem_ssbo), "mem_flags::mem_device")) { puts("FAIL ssbo"); bad++; }
   if (strcmp(kk_msl_mem_flag_for_mode(nir_var_mem_global), "mem_flags::mem_device")) { puts("FAIL global"); bad++; }
   printf("memflags: %d modes checked, %d bad\n", n, bad);
   return bad ? 1 : 0;
}
