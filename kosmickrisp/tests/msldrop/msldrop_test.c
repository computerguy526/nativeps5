/* Regression test for KK_MSL_COMPRESS: builds a compute and a graphics pipeline
 * (through the Vulkan pipeline cache), renders, and prints a checksum of the
 * results plus the pipeline cache size.
 *   msldrop_test <dir with tri.vert.spv tri.frag.spv fill.comp.spv> [cache in] [cache out]
 * run.sh runs it with and without KK_MSL_COMPRESS, from an empty cache and from
 * the other mode's cache, and requires every checksum to match. */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#define CK(x) do { VkResult r_ = (x); if (r_) { fprintf(stderr, "%s failed %d line %d\n", #x, r_, __LINE__); exit(2); } } while (0)
#define W 96
#define H 64
static void *rd(const char *p, size_t *n) {
   FILE *f = fopen(p, "rb"); if (!f) return NULL;
   fseek(f, 0, SEEK_END); *n = ftell(f); fseek(f, 0, SEEK_SET);
   void *b = malloc(*n ? *n : 1); if (fread(b, 1, *n, f) != *n) exit(2); fclose(f); return b;
}
static VkShaderModule mod(VkDevice d, const char *dir, const char *name) {
   char p[512]; snprintf(p, sizeof p, "%s/%s.spv", dir, name);
   size_t n; void *b = rd(p, &n); if (!b) { fprintf(stderr, "missing %s\n", p); exit(2); }
   VkShaderModuleCreateInfo ci = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, 0, 0, n, b};
   VkShaderModule m; CK(vkCreateShaderModule(d, &ci, 0, &m)); return m;
}
static uint32_t memtype(VkPhysicalDevice pd, uint32_t bits, VkMemoryPropertyFlags want) {
   VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(pd, &mp);
   for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
      if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want) return i;
   fprintf(stderr, "no memory type\n"); exit(2);
}
int main(int argc, char **argv) {
   const char *dir = argv[1], *cin = argc > 2 ? argv[2] : NULL, *cout = argc > 3 ? argv[3] : NULL;
   const char *iext[] = {"VK_KHR_portability_enumeration"};
   VkApplicationInfo ai = {VK_STRUCTURE_TYPE_APPLICATION_INFO, 0, "msldrop", 1, "x", 1, VK_API_VERSION_1_3};
   VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, 0, VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR, &ai, 0, 0, 1, iext};
   VkInstance inst; CK(vkCreateInstance(&ici, 0, &inst));
   uint32_t n = 1; VkPhysicalDevice pd; vkEnumeratePhysicalDevices(inst, &n, &pd);
   VkPhysicalDeviceVulkan12Features f12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
   VkPhysicalDeviceVulkan13Features f13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, &f12};
   f12.bufferDeviceAddress = 1; f13.dynamicRendering = 1;
   VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &f13}; f2.features.shaderInt64 = 1;
   float pr = 1; VkDeviceQueueCreateInfo qi = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, 0, 0, 0, 1, &pr};
   VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &f2, 0, 1, &qi};
   VkDevice dev; CK(vkCreateDevice(pd, &dci, 0, &dev));
   VkQueue q; vkGetDeviceQueue(dev, 0, 0, &q);

   size_t cn = 0; void *cb = cin ? rd(cin, &cn) : NULL;
   VkPipelineCacheCreateInfo pci = {VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO, 0, 0, cn, cb};
   VkPipelineCache pc; CK(vkCreatePipelineCache(dev, &pci, 0, &pc));

   VkShaderModule vs = mod(dev, dir, "tri.vert"), fs = mod(dev, dir, "tri.frag"), cs = mod(dev, dir, "fill.comp");
   VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 8};
   VkPipelineLayoutCreateInfo lci = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, 0, 0, 0, 0, 1, &pcr};
   VkPipelineLayout cl; CK(vkCreatePipelineLayout(dev, &lci, 0, &cl));
   VkPipelineLayoutCreateInfo lci2 = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
   VkPipelineLayout gl; CK(vkCreatePipelineLayout(dev, &lci2, 0, &gl));
   VkComputePipelineCreateInfo cpi = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, 0, 0, {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, 0, 0, VK_SHADER_STAGE_COMPUTE_BIT, cs, "main"}, cl};
   VkPipeline cp; CK(vkCreateComputePipelines(dev, pc, 1, &cpi, 0, &cp));
   VkPipelineShaderStageCreateInfo st[2] = {{VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, 0, 0, VK_SHADER_STAGE_VERTEX_BIT, vs, "main"}, {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, 0, 0, VK_SHADER_STAGE_FRAGMENT_BIT, fs, "main"}};
   VkPipelineVertexInputStateCreateInfo vi = {VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
   VkPipelineInputAssemblyStateCreateInfo ia = {VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO, 0, 0, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
   VkPipelineViewportStateCreateInfo vp = {VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO, 0, 0, 1, 0, 1, 0};
   VkPipelineRasterizationStateCreateInfo rs = {VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO}; rs.lineWidth = 1;
   VkPipelineMultisampleStateCreateInfo ms = {VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO}; ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
   VkPipelineColorBlendAttachmentState ba = {.colorWriteMask = 15};
   VkPipelineColorBlendStateCreateInfo cbs = {VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO, 0, 0, 0, 0, 1, &ba};
   VkDynamicState dy[2] = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
   VkPipelineDynamicStateCreateInfo dsi = {VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO, 0, 0, 2, dy};
   VkFormat fmt = VK_FORMAT_R8G8B8A8_UNORM;
   VkPipelineRenderingCreateInfo ri = {VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO, 0, 0, 1, &fmt};
   VkGraphicsPipelineCreateInfo gpi = {VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, &ri, 0, 2, st, &vi, &ia, 0, &vp, &rs, &ms, 0, &cbs, &dsi, gl};
   VkPipeline gp; CK(vkCreateGraphicsPipelines(dev, pc, 1, &gpi, 0, &gp));

   VkImageCreateInfo ii = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, 0, 0, VK_IMAGE_TYPE_2D, fmt, {W, H, 1}, 1, 1, VK_SAMPLE_COUNT_1_BIT, VK_IMAGE_TILING_OPTIMAL, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT};
   VkImage img; CK(vkCreateImage(dev, &ii, 0, &img));
   VkMemoryRequirements mr; vkGetImageMemoryRequirements(dev, img, &mr);
   VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, 0, mr.size, memtype(pd, mr.memoryTypeBits, 0)};
   VkDeviceMemory im; CK(vkAllocateMemory(dev, &mai, 0, &im)); CK(vkBindImageMemory(dev, img, im, 0));
   VkImageViewCreateInfo vci = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, 0, 0, img, VK_IMAGE_VIEW_TYPE_2D, fmt, {0}, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
   VkImageView iv; CK(vkCreateImageView(dev, &vci, 0, &iv));

   const uint32_t NB = 4096;
   VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, 0, 0, W * H * 4 + NB * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT};
   VkBuffer bo; CK(vkCreateBuffer(dev, &bi, 0, &bo));
   vkGetBufferMemoryRequirements(dev, bo, &mr);
   VkMemoryAllocateFlagsInfo fi = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO, 0, VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT};
   VkMemoryAllocateInfo mbi = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &fi, mr.size, memtype(pd, mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
   VkDeviceMemory bm; CK(vkAllocateMemory(dev, &mbi, 0, &bm)); CK(vkBindBufferMemory(dev, bo, bm, 0));
   uint8_t *map; CK(vkMapMemory(dev, bm, 0, VK_WHOLE_SIZE, 0, (void **)&map)); memset(map, 0, bi.size);
   VkBufferDeviceAddressInfo dai = {VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO, 0, bo};
   uint64_t addr = vkGetBufferDeviceAddress(dev, &dai) + W * H * 4;

   VkCommandPoolCreateInfo cpci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, 0, 0, 0};
   VkCommandPool pool; CK(vkCreateCommandPool(dev, &cpci, 0, &pool));
   VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, 0, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
   VkCommandBuffer cmd; CK(vkAllocateCommandBuffers(dev, &cai, &cmd));
   VkCommandBufferBeginInfo bgi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO}; CK(vkBeginCommandBuffer(cmd, &bgi));
   vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, cp);
   vkCmdPushConstants(cmd, cl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 8, &addr);
   vkCmdDispatch(cmd, NB / 64, 1, 1);
   VkImageMemoryBarrier b1 = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, 0, 0, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, 0, 0, img, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
   vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, 0, 0, 0, 1, &b1);
   VkRenderingAttachmentInfo ca = {VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, 0, iv, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, 0, 0, 0, VK_ATTACHMENT_LOAD_OP_CLEAR, VK_ATTACHMENT_STORE_OP_STORE};
   VkRenderingInfo rinfo = {VK_STRUCTURE_TYPE_RENDERING_INFO, 0, 0, {{0, 0}, {W, H}}, 1, 0, 1, &ca};
   vkCmdBeginRendering(cmd, &rinfo);
   vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, gp);
   VkViewport vpt = {0, 0, W, H, 0, 1}; VkRect2D sc = {{0, 0}, {W, H}};
   vkCmdSetViewport(cmd, 0, 1, &vpt); vkCmdSetScissor(cmd, 0, 1, &sc);
   vkCmdDraw(cmd, 3, 1, 0, 0);
   vkCmdEndRendering(cmd);
   VkImageMemoryBarrier b2 = b1; b2.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT; b2.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
   b2.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL; b2.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
   vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, 0, 0, 0, 1, &b2);
   VkBufferImageCopy reg = {0, 0, 0, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {0, 0, 0}, {W, H, 1}};
   vkCmdCopyImageToBuffer(cmd, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, bo, 1, &reg);
   CK(vkEndCommandBuffer(cmd));
   VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, 0, 0, 0, 0, 1, &cmd};
   CK(vkQueueSubmit(q, 1, &si, 0)); CK(vkQueueWaitIdle(q));

   uint64_t h = 1469598103934665603ull;
   for (size_t i = 0; i < bi.size; i++) { h ^= map[i]; h *= 1099511628211ull; }
   size_t sz = 0; CK(vkGetPipelineCacheData(dev, pc, &sz, 0));
   void *data = malloc(sz); CK(vkGetPipelineCacheData(dev, pc, &sz, data));
   if (cout) { FILE *f = fopen(cout, "wb"); fwrite(data, 1, sz, f); fclose(f); }
   printf("checksum %016llx cache %zu\n", (unsigned long long)h, sz);
   return 0;
}
