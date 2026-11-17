/* VK timestamp query test for KosmicKrisp (patch 0004).
 * Creates timestamp pools of several sizes, writes timestamps around a GPU transfer workload of
 * increasing size, reads them back and checks monotonic, plausible (timestampPeriod) values. */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }
#define CK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { printf("FAIL %s = %d (line %d)\n", #x, r_, __LINE__); exit(1);} } while (0)

int main(void) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .apiVersion = VK_API_VERSION_1_3};
  const char *iext[] = {"VK_KHR_portability_enumeration", "VK_KHR_get_physical_device_properties2"};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai,
                              .flags = VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR,
                              .enabledExtensionCount = 1, .ppEnabledExtensionNames = iext};
  VkInstance inst; CK(vkCreateInstance(&ici, NULL, &inst));
  uint32_t n = 1; VkPhysicalDevice pd; CK(vkEnumeratePhysicalDevices(inst, &n, &pd));
  VkPhysicalDeviceProperties pr; vkGetPhysicalDeviceProperties(pd, &pr);
  printf("device %s timestampPeriod %.4f ns, computeAndGraphics %d\n", pr.deviceName, pr.limits.timestampPeriod,
         pr.limits.timestampComputeAndGraphics);
  uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, NULL);
  VkQueueFamilyProperties qf[8]; if (nq > 8) nq = 8; vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, qf);
  printf("queue 0 timestampValidBits %u\n", qf[0].timestampValidBits);
  float prio = 1; VkDeviceQueueCreateInfo qi = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &prio};
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi};
  VkDevice dev; CK(vkCreateDevice(pd, &dci, NULL, &dev));
  VkQueue q; vkGetDeviceQueue(dev, 0, 0, &q);
  VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(pd, &mp);
  VkDeviceSize bsz = 256u << 20;
  VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = bsz,
                           .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT};
  VkBuffer buf; CK(vkCreateBuffer(dev, &bi, NULL, &buf));
  VkMemoryRequirements mr; vkGetBufferMemoryRequirements(dev, buf, &mr);
  uint32_t mt = 0; for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if (mr.memoryTypeBits & (1u << i)) { mt = i; break; }
  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = mt};
  VkDeviceMemory mem; CK(vkAllocateMemory(dev, &mai, NULL, &mem)); CK(vkBindBufferMemory(dev, buf, mem, 0));
  VkCommandPoolCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
  VkCommandPool cp; CK(vkCreateCommandPool(dev, &cpi, NULL, &cp));
  int fails = 0;
  uint32_t sizes[] = {1, 2, 3, 16, 64, 100, 1000, 4096, 5000, 16384, 65536};
  for (unsigned si = 0; si < sizeof sizes / sizeof *sizes; si++) {
    uint32_t cnt = sizes[si];
    VkQueryPoolCreateInfo qpi = {.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO, .queryType = VK_QUERY_TYPE_TIMESTAMP, .queryCount = cnt};
    VkQueryPool qp; VkResult r = vkCreateQueryPool(dev, &qpi, NULL, &qp);
    if (r != VK_SUCCESS) { printf("pool %u: create FAILED %d\n", cnt, r); fails++; continue; }
    VkCommandBuffer cb; VkCommandBufferAllocateInfo ca = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = cp, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    CK(vkAllocateCommandBuffers(dev, &ca, &cb));
    VkCommandBufferBeginInfo bgi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CK(vkBeginCommandBuffer(cb, &bgi));
    vkCmdResetQueryPool(cb, qp, 0, cnt);
    uint32_t used = cnt < 6 ? cnt : 6; if (used < 2) used = 2; if (cnt < 2) used = cnt;
    /* ts[0], fill 1x, ts[1], fill 8x, ts[2] ... */
    uint32_t k = 0;
    if (used >= 1) vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, qp, k++);
    for (uint32_t i = 1; i < used; i++) {
      for (uint32_t j = 0; j < (1u << (i - 1)); j++) vkCmdFillBuffer(cb, buf, 0, bsz, i + j);
      vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, qp, k++);
    }
    if (cnt > 100) vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, qp, cnt - 1);
    CK(vkEndCommandBuffer(cb));
    VkSubmitInfo si2 = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    double w0 = now_ms();
    CK(vkQueueSubmit(q, 1, &si2, VK_NULL_HANDLE)); CK(vkQueueWaitIdle(q));
    double wall = now_ms() - w0;
    uint64_t t[8] = {0}; VkResult rr = vkGetQueryPoolResults(dev, qp, 0, used, sizeof t[0] * used, t, 8, VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT);
    printf("pool %5u: create ok, results rc=%d:", cnt, rr);
    int ok = rr == VK_SUCCESS;
    for (uint32_t i = 0; i < used; i++) {
      printf(" %llu", (unsigned long long)t[i]);
      if (i && t[i] < t[i - 1]) ok = 0;
    }
    printf("\n   wall submit->idle %.1f ms, ts span %.1f ms\n   deltas (ms):", wall, used >= 2 ? (double)(t[used - 1] - t[0]) * pr.limits.timestampPeriod / 1e6 : 0.0);
    for (uint32_t i = 1; i < used; i++) printf(" %.3f", (double)(t[i] - t[i - 1]) * pr.limits.timestampPeriod / 1e6);
    if (used >= 3 && !(t[used - 1] > t[0])) ok = 0;
    if (used >= 2 && t[0] == 0) ok = 0;
    printf("  => %s\n", ok ? "OK" : "FAIL");
    if (!ok) fails++;
    vkFreeCommandBuffers(dev, cp, 1, &cb); vkDestroyQueryPool(dev, qp, NULL);
  }
  printf(fails ? "RESULT: %d FAILED\n" : "RESULT: PASS\n", fails);
  return fails != 0;
}
