/* 3D image rendered through 2D array views, read through the 3D image (KosmicKrisp).
 * AnyPS5 renders Unreal's 32^3 tonemap LUT into the slices of a VK_IMAGE_TYPE_3D image created with
 * VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT (a layered color attachment) and samples it through a 3D view.
 * KosmicKrisp backs the 2D array views of such an image with a second Metal texture placed over the same
 * heap bytes. This test writes the slices through the layered attachment (a clear, no pipeline needed) and
 * reads the 3D image back, with the driver operations AnyPS5 mixes in between (image -> buffer stores,
 * buffer -> image uploads), and reports every step whose read-back is not the colour last rendered. */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define CK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { printf("FAIL %s = %d (line %d)\n", #x, r_, __LINE__); exit(2);} } while (0)
enum { N = 32 };
static VkDevice dev; static VkQueue q; static VkCommandPool cp;
static VkImage img; static VkImageView arrayView, sliceViews[N]; static VkBuffer buf; static uint8_t *map;
static VkCommandBuffer begin(void) {
  VkCommandBuffer cb; VkCommandBufferAllocateInfo ca = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = cp, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  CK(vkAllocateCommandBuffers(dev, &ca, &cb));
  VkCommandBufferBeginInfo bgi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  CK(vkBeginCommandBuffer(cb, &bgi)); return cb;
}
static void end(VkCommandBuffer cb) {
  CK(vkEndCommandBuffer(cb));
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
  CK(vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE)); CK(vkQueueWaitIdle(q)); vkFreeCommandBuffers(dev, cp, 1, &cb);
}
static void barrier(VkCommandBuffer cb, VkImageLayout from, VkImageLayout to) {
  VkImageMemoryBarrier b = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = 0x1ffff, .dstAccessMask = 0x1ffff, .oldLayout = from, .newLayout = to,
    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = img, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 0, NULL, 1, &b);
}
/* render: clear every slice to (v/255, ...) through the layered 2D array attachment; perSlice: 32 single-layer passes */
static void render(VkCommandBuffer cb, unsigned v, int perSlice) {
  for (int pass = 0; pass < (perSlice ? N : 1); pass++) {
    VkRenderingAttachmentInfo att = {.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = perSlice ? sliceViews[pass] : arrayView, .imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
      .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE, .clearValue = {.color = {.float32 = {v / 255.0f, v / 255.0f, v / 255.0f, 1.0f}}}};
    VkRenderingInfo ri = {.sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = {{0, 0}, {N, N}}, .layerCount = perSlice ? 1 : N, .colorAttachmentCount = 1, .pColorAttachments = &att};
    vkCmdBeginRendering(cb, &ri); vkCmdEndRendering(cb);
  }
}
static void store(VkCommandBuffer cb) { /* 3D image -> buffer */
  VkBufferImageCopy r = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {N, N, N}};
  vkCmdCopyImageToBuffer(cb, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &r);
}
static void upload(VkCommandBuffer cb) { /* buffer -> 3D image */
  VkBufferImageCopy r = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {N, N, N}};
  vkCmdCopyBufferToImage(cb, buf, img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);
}
static unsigned readback(unsigned *lo, unsigned *hi) { /* returns the red byte of the centre texel; lo/hi over all texels */
  VkCommandBuffer cb = begin(); barrier(cb, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL); store(cb);
  barrier(cb, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL); end(cb);
  *lo = 255; *hi = 0;
  for (size_t i = 0; i < (size_t)N * N * N; i++) { unsigned r = map[i * 4]; if (r < *lo) *lo = r; if (r > *hi) *hi = r; }
  return map[((size_t)(N / 2) * N * N + (N / 2) * N + N / 2) * 4];
}
int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  const int perSlice = argc > 1 && !strcmp(argv[1], "slices");
  VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .apiVersion = VK_API_VERSION_1_3};
  const char *iext[] = {"VK_KHR_portability_enumeration"};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai, .flags = VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR, .enabledExtensionCount = 1, .ppEnabledExtensionNames = iext};
  VkInstance inst; CK(vkCreateInstance(&ici, NULL, &inst));
  uint32_t n = 1; VkPhysicalDevice pd; CK(vkEnumeratePhysicalDevices(inst, &n, &pd));
  VkPhysicalDeviceProperties pr; vkGetPhysicalDeviceProperties(pd, &pr); printf("device %s (%s layered pass)\n", pr.deviceName, perSlice ? "one pass per slice, no" : "one");
  float prio = 1; VkDeviceQueueCreateInfo qi = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &prio};
  VkPhysicalDeviceVulkan13Features f13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .dynamicRendering = VK_TRUE};
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &f13, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi};
  CK(vkCreateDevice(pd, &dci, NULL, &dev)); vkGetDeviceQueue(dev, 0, 0, &q);
  VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(pd, &mp);
  VkCommandPoolCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT}; CK(vkCreateCommandPool(dev, &cpi, NULL, &cp));
  VkImageCreateInfo ii = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .flags = VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT, .imageType = VK_IMAGE_TYPE_3D, .format = VK_FORMAT_R8G8B8A8_UNORM,
    .extent = {N, N, N}, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
    .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_STORAGE_BIT};
  CK(vkCreateImage(dev, &ii, NULL, &img));
  VkMemoryRequirements mr; vkGetImageMemoryRequirements(dev, img, &mr);
  uint32_t mt = 0; for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if (mr.memoryTypeBits & (1u << i)) { mt = i; break; }
  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = mt};
  VkDeviceMemory imem; CK(vkAllocateMemory(dev, &mai, NULL, &imem)); CK(vkBindImageMemory(dev, img, imem, 0));
  VkImageViewCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = img, .viewType = VK_IMAGE_VIEW_TYPE_2D_ARRAY, .format = ii.format, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, N}};
  CK(vkCreateImageView(dev, &vi, NULL, &arrayView));
  for (int s = 0; s < N; s++) { vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.subresourceRange.baseArrayLayer = s; vi.subresourceRange.layerCount = 1; CK(vkCreateImageView(dev, &vi, NULL, &sliceViews[s])); }
  VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = (VkDeviceSize)N * N * N * 4, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT};
  CK(vkCreateBuffer(dev, &bi, NULL, &buf)); vkGetBufferMemoryRequirements(dev, buf, &mr);
  mt = 0; for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if ((mr.memoryTypeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) { mt = i; break; }
  mai.allocationSize = mr.size; mai.memoryTypeIndex = mt;
  VkDeviceMemory bmem; CK(vkAllocateMemory(dev, &mai, NULL, &bmem)); CK(vkBindBufferMemory(dev, buf, bmem, 0)); void *p; CK(vkMapMemory(dev, bmem, 0, VK_WHOLE_SIZE, 0, &p)); map = p;
  int fails = 0; unsigned lo, hi, got;
  /* step 0: first upload (as StorageTexture::Create does), value 10 */
  memset(map, 10, (size_t)N * N * N * 4);
  VkCommandBuffer cb = begin(); barrier(cb, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL); upload(cb); barrier(cb, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL); end(cb);
  memset(map, 0xEE, (size_t)N * N * N * 4); got = readback(&lo, &hi); printf("step  0 upload 10            -> read %3u (min %3u max %3u) %s\n", got, lo, hi, lo == 10 && hi == 10 ? "ok" : "MISMATCH"); fails += !(lo == 10 && hi == 10);
  unsigned v = 20; int step = 1;
  for (int round = 0; round < 6; round++) {
    /* a) a run of renders, each followed by a read-back */
    for (int k = 0; k < 3; k++, v += 7, step++) {
      cb = begin(); render(cb, v, perSlice); end(cb);
      memset(map, 0xEE, (size_t)N * N * N * 4); got = readback(&lo, &hi);
      printf("step %2d render %3u           -> read %3u (min %3u max %3u) %s\n", step, v, got, lo, hi, lo == v && hi == v ? "ok" : "MISMATCH"); fails += !(lo == v && hi == v);
    }
    /* b) renders without a read-back in between (the title samples only), then one read */
    cb = begin(); for (int k = 0; k < 5; k++) { v += 3; render(cb, v, perSlice); } end(cb); step++;
    memset(map, 0xEE, (size_t)N * N * N * 4); got = readback(&lo, &hi);
    printf("step %2d 5 renders, last %3u   -> read %3u (min %3u max %3u) %s\n", step, v, got, lo, hi, lo == v && hi == v ? "ok" : "MISMATCH"); fails += !(lo == v && hi == v);
    /* c) an upload into the 3D image (a refresh from guest memory), then a render over it */
    memset(map, 200, (size_t)N * N * N * 4);
    cb = begin(); barrier(cb, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL); upload(cb); barrier(cb, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL); end(cb); step++;
    memset(map, 0xEE, (size_t)N * N * N * 4); got = readback(&lo, &hi);
    printf("step %2d upload 200           -> read %3u (min %3u max %3u) %s\n", step, got, lo, hi, lo == 200 && hi == 200 ? "ok" : "MISMATCH"); fails += !(lo == 200 && hi == 200);
    v += 5; cb = begin(); render(cb, v, perSlice); end(cb); step++;
    memset(map, 0xEE, (size_t)N * N * N * 4); got = readback(&lo, &hi);
    printf("step %2d render %3u after upload -> read %3u (min %3u max %3u) %s\n", step, v, got, lo, hi, lo == v && hi == v ? "ok" : "MISMATCH"); fails += !(lo == v && hi == v);
    if (v > 180) v = 20;
  }
  printf(fails ? "RESULT: %d MISMATCHES\n" : "RESULT: PASS\n", fails);
  return fails != 0;
}
