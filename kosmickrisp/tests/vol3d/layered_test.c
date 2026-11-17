/* Layered draws as a later render pass of a command buffer (KosmicKrisp).
 * AnyPS5 draws Unreal's 32^3 tonemap LUT as ONE layered mesh draw into the slices of a 3D image (a 2D array view of
 * a VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT image, per-primitive gl_Layer). On PPSA30552 that draw left the LUT
 * unchanged whenever an earlier render pass had been recorded into the same command buffer (docs/playbook.md row 98).
 * This test records pass B (the layered draw) alone and after other work, into a 3D, a 2D array and a cube target,
 * with a mesh shader and with a vertex shader that writes gl_Layer, and reports per case how many layers hold the
 * colour of the draw.
 *
 *   layered_test <dir with *.spv> [filter substring]        exit 0 = every layer of every case is right
 */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define CK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { printf("FAIL %s = %d (line %d)\n", #x, r_, __LINE__); exit(2);} } while (0)
enum { N = 32, AW = 64, MAXL = 32 };
static VkDevice dev; static VkPhysicalDevice pd; static VkQueue q; static VkCommandPool cp; static const char *dir;
static PFN_vkCmdDrawMeshTasksEXT drawMesh;
static VkPipelineLayout layout;
static VkBuffer buf, buf2; static uint8_t *map;

static uint32_t memType(uint32_t bits, VkMemoryPropertyFlags want) {
  VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(pd, &mp);
  for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want) return i;
  printf("FAIL no memory type\n"); exit(2);
}
static VkShaderModule load(const char *name) {
  char path[1024]; snprintf(path, sizeof path, "%s/%s.spv", dir, name);
  FILE *f = fopen(path, "rb"); if (!f) { printf("FAIL cannot open %s\n", path); exit(2); }
  fseek(f, 0, SEEK_END); long size = ftell(f); fseek(f, 0, SEEK_SET);
  uint32_t *w = malloc(size); if (fread(w, 1, size, f) != (size_t)size) exit(2); fclose(f);
  VkShaderModuleCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = size, .pCode = w};
  VkShaderModule m; CK(vkCreateShaderModule(dev, &ci, NULL, &m)); free(w); return m;
}
static int vbStrides; /* != 0: the next vertex pipeline reads bindings 0..2 with strides 16, 20, 24 */
static VkPipeline pipeline2(const char *first, int mesh, const char *frag) {
  VkPipelineShaderStageCreateInfo st[2] = {
    {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = mesh ? VK_SHADER_STAGE_MESH_BIT_EXT : VK_SHADER_STAGE_VERTEX_BIT, .module = load(first), .pName = "main"},
    {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = load(frag), .pName = "main"}};
  VkPipelineVertexInputStateCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
  const VkVertexInputBindingDescription vb[3] = {{0, 16, VK_VERTEX_INPUT_RATE_VERTEX}, {1, 20, VK_VERTEX_INPUT_RATE_VERTEX}, {2, 24, VK_VERTEX_INPUT_RATE_VERTEX}};
  const VkVertexInputAttributeDescription va[3] = {{0, 0, VK_FORMAT_R32G32B32A32_SFLOAT, 0}, {1, 1, VK_FORMAT_R32G32B32A32_SFLOAT, 0}, {2, 2, VK_FORMAT_R32G32B32A32_SFLOAT, 0}};
  if (vbStrides) { vi.vertexBindingDescriptionCount = 3; vi.pVertexBindingDescriptions = vb; vi.vertexAttributeDescriptionCount = 3; vi.pVertexAttributeDescriptions = va; }
  VkPipelineInputAssemblyStateCreateInfo ia = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO, .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
  VkPipelineViewportStateCreateInfo vp = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO, .viewportCount = 1, .scissorCount = 1};
  VkPipelineRasterizationStateCreateInfo rs = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO, .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE, .lineWidth = 1.0f};
  VkPipelineMultisampleStateCreateInfo ms = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO, .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
  VkPipelineColorBlendAttachmentState ba = {.colorWriteMask = 0xf};
  VkPipelineColorBlendStateCreateInfo cb = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO, .attachmentCount = 1, .pAttachments = &ba};
  VkDynamicState dyns[2] = {VK_DYNAMIC_STATE_VIEWPORT, VK_DYNAMIC_STATE_SCISSOR};
  VkPipelineDynamicStateCreateInfo ds = {.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO, .dynamicStateCount = 2, .pDynamicStates = dyns};
  VkFormat fmt = VK_FORMAT_R8G8B8A8_UNORM;
  VkPipelineRenderingCreateInfo rc = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO, .colorAttachmentCount = 1, .pColorAttachmentFormats = &fmt};
  VkGraphicsPipelineCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &rc, .stageCount = 2, .pStages = st, .pVertexInputState = mesh ? NULL : &vi,
    .pInputAssemblyState = mesh ? NULL : &ia, .pViewportState = &vp, .pRasterizationState = &rs, .pMultisampleState = &ms, .pColorBlendState = &cb, .pDynamicState = &ds, .layout = layout};
  VkPipeline p; CK(vkCreateGraphicsPipelines(dev, VK_NULL_HANDLE, 1, &ci, NULL, &p)); return p;
}
static VkPipeline pipeline(const char *first, int mesh) { return pipeline2(first, mesh, "layer.frag"); }
typedef struct { const char *name; VkImage img; VkImageView view; unsigned layers, size; int is3d; } Target;
static Target makeTarget(const char *name, VkImageType type, VkImageCreateFlags flags, unsigned size, unsigned layers, VkImageViewType viewType) {
  Target t = {.name = name, .layers = layers, .size = size, .is3d = type == VK_IMAGE_TYPE_3D};
  VkImageCreateInfo ii = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .flags = flags, .imageType = type, .format = VK_FORMAT_R8G8B8A8_UNORM,
    .extent = {size, size, t.is3d ? layers : 1}, .mipLevels = 1, .arrayLayers = t.is3d ? 1 : layers, .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
    .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT};
  CK(vkCreateImage(dev, &ii, NULL, &t.img));
  VkMemoryRequirements mr; vkGetImageMemoryRequirements(dev, t.img, &mr);
  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = memType(mr.memoryTypeBits, 0)};
  VkDeviceMemory mem; CK(vkAllocateMemory(dev, &mai, NULL, &mem)); CK(vkBindImageMemory(dev, t.img, mem, 0));
  VkImageViewCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = t.img, .viewType = viewType, .format = ii.format, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, layers}};
  CK(vkCreateImageView(dev, &vi, NULL, &t.view));
  return t;
}
static VkCommandBuffer begin(void) {
  VkCommandBuffer cb; VkCommandBufferAllocateInfo ca = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = cp, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
  CK(vkAllocateCommandBuffers(dev, &ca, &cb));
  VkCommandBufferBeginInfo bgi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
  CK(vkBeginCommandBuffer(cb, &bgi)); return cb;
}
static void end(VkCommandBuffer cb) {
  CK(vkEndCommandBuffer(cb));
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
  CK(vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE)); CK(vkQueueWaitIdle(q)); vkFreeCommandBuffers(dev, cp, 1, &cb);
}
static void barrier(VkCommandBuffer cb, const Target *t, VkImageLayout from, VkImageLayout to) {
  VkImageMemoryBarrier b = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = 0x1ffff, .dstAccessMask = 0x1ffff, .oldLayout = from, .newLayout = to,
    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = t->img, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, t->is3d ? 1 : t->layers}};
  vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 0, NULL, 1, &b);
}
/* One render pass over all layers of `t`. pipe == NULL: the pass only clears (to v). Otherwise loadOp as given and one
 * layered (or, for a 1-layer target, plain) draw of colour (v, layer, 77). */
static VkPipeline pFlatVb;
static void pass(VkCommandBuffer cb, const Target *t, VkPipeline pipe, int mesh, VkAttachmentLoadOp loadOp, unsigned v) {
  VkRenderingAttachmentInfo att = {.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = t->view, .imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
    .loadOp = pipe ? loadOp : VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE, .clearValue = {.color = {.float32 = {v / 255.0f, 200 / 255.0f, 0, 1.0f}}}};
  if (pipe && loadOp == VK_ATTACHMENT_LOAD_OP_CLEAR) att.clearValue.color.float32[0] = 5 / 255.0f;
  VkRenderingInfo ri = {.sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = {{0, 0}, {t->size, t->size}}, .layerCount = t->layers, .colorAttachmentCount = 1, .pColorAttachments = &att};
  vkCmdBeginRendering(cb, &ri);
  if (pipe) {
    VkViewport vp = {0, 0, t->size, t->size, 0, 1}; VkRect2D sc = {{0, 0}, {t->size, t->size}};
    vkCmdSetViewport(cb, 0, 1, &vp); vkCmdSetScissor(cb, 0, 1, &sc);
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, pipe);
    const float pc[4] = {v / 255.0f, 0, 77 / 255.0f, 1};
    vkCmdPushConstants(cb, layout, VK_SHADER_STAGE_MESH_BIT_EXT | VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 16, pc);
    if (pipe == pFlatVb) { const VkBuffer vbs[3] = {buf2, buf2, buf2}; const VkDeviceSize offs[3] = {0, 0, 0}; vkCmdBindVertexBuffers(cb, 0, 3, vbs, offs); }
    if (mesh == 2) drawMesh(cb, 1, t->layers, 1); else if (mesh) drawMesh(cb, t->layers, 1, 1); else vkCmdDraw(cb, 3, t->layers, 0, 0);
  }
  vkCmdEndRendering(cb);
}
/* Reads the target back; returns the number of layers whose every texel is (v, layer, 77). *sample = red of the first wrong texel (or -1). */
static unsigned check(const Target *t, unsigned v, int *sample) {
  VkCommandBuffer cb = begin(); barrier(cb, t, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
  VkBufferImageCopy r = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, t->is3d ? 1 : t->layers}, .imageExtent = {t->size, t->size, t->is3d ? t->layers : 1}};
  memset(map, 0xEE, (size_t)N * N * MAXL * 4);
  vkCmdCopyImageToBuffer(cb, t->img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &r);
  barrier(cb, t, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL); end(cb);
  unsigned good = 0; *sample = -1;
  for (unsigned l = 0; l < t->layers; l++) {
    int ok = 1;
    for (size_t i = 0; i < (size_t)t->size * t->size; i++) {
      const uint8_t *p = map + ((size_t)l * t->size * t->size + i) * 4;
      if (p[0] != v || p[1] != l || p[2] != 77) { ok = 0; if (*sample < 0) *sample = p[0]; break; }
    }
    good += ok;
  }
  return good;
}
int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  if (argc < 2) { printf("usage: layered_test <spv dir> [filter]\n"); return 2; }
  dir = argv[1]; const char *filter = argc > 2 ? argv[2] : NULL;
  VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .apiVersion = VK_API_VERSION_1_3};
  const char *iext[] = {"VK_KHR_portability_enumeration"};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai, .flags = VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR, .enabledExtensionCount = 1, .ppEnabledExtensionNames = iext};
  VkInstance inst; CK(vkCreateInstance(&ici, NULL, &inst));
  uint32_t n = 1; CK(vkEnumeratePhysicalDevices(inst, &n, &pd));
  VkPhysicalDeviceProperties pr; vkGetPhysicalDeviceProperties(pd, &pr);
  VkPhysicalDeviceMeshShaderFeaturesEXT fm = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT};
  VkPhysicalDeviceVulkan12Features f12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &fm};
  VkPhysicalDeviceFeatures2 f2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f12};
  vkGetPhysicalDeviceFeatures2(pd, &f2);
  const int haveMesh = fm.meshShader, haveVsLayer = f12.shaderOutputLayer;
  printf("device %s: meshShader=%d shaderOutputLayer=%d\n", pr.deviceName, haveMesh, haveVsLayer);
  float prio = 1; VkDeviceQueueCreateInfo qi = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &prio};
  VkPhysicalDeviceMeshShaderFeaturesEXT em = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, .meshShader = haveMesh};
  VkPhysicalDeviceVulkan13Features e13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &em, .dynamicRendering = VK_TRUE};
  VkPhysicalDeviceVulkan12Features e12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &e13, .shaderOutputLayer = haveVsLayer};
  const char *dext[] = {VK_EXT_MESH_SHADER_EXTENSION_NAME};
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &e12, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi, .enabledExtensionCount = haveMesh ? 1 : 0, .ppEnabledExtensionNames = dext};
  CK(vkCreateDevice(pd, &dci, NULL, &dev)); vkGetDeviceQueue(dev, 0, 0, &q);
  drawMesh = (PFN_vkCmdDrawMeshTasksEXT)vkGetDeviceProcAddr(dev, "vkCmdDrawMeshTasksEXT");
  VkCommandPoolCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT}; CK(vkCreateCommandPool(dev, &cpi, NULL, &cp));
  VkPushConstantRange pcr = {VK_SHADER_STAGE_MESH_BIT_EXT | VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 16};
  if (!haveMesh) pcr.stageFlags &= ~VK_SHADER_STAGE_MESH_BIT_EXT;
  VkPipelineLayoutCreateInfo pli = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .pushConstantRangeCount = 1, .pPushConstantRanges = &pcr};
  CK(vkCreatePipelineLayout(dev, &pli, NULL, &layout));
  VkBuffer *bufs[2] = {&buf, &buf2};
  for (int i = 0; i < 2; i++) {
    VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = (VkDeviceSize)N * N * MAXL * 4, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT};
    CK(vkCreateBuffer(dev, &bi, NULL, bufs[i])); VkMemoryRequirements mr; vkGetBufferMemoryRequirements(dev, *bufs[i], &mr);
    VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = memType(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    VkDeviceMemory bm; CK(vkAllocateMemory(dev, &mai, NULL, &bm)); CK(vkBindBufferMemory(dev, *bufs[i], bm, 0));
    if (i == 0) { void *p; CK(vkMapMemory(dev, bm, 0, VK_WHOLE_SIZE, 0, &p)); map = p; }
  }
  VkPipeline pLayerMesh = haveMesh ? pipeline("layer.mesh", 1) : VK_NULL_HANDLE, pFlatMesh = haveMesh ? pipeline("flat.mesh", 1) : VK_NULL_HANDLE;
  VkPipeline pLayerPc = haveMesh ? pipeline2("layerpc.mesh", 1, "varying.frag") : VK_NULL_HANDLE;
  VkPipeline pLayerBig = haveMesh ? pipeline2("layerbig.mesh", 1, "varying.frag") : VK_NULL_HANDLE;
  VkPipeline pLayerVert = haveVsLayer ? pipeline("layer.vert", 0) : VK_NULL_HANDLE, pFlatVert = pipeline("flat.vert", 0);
  vbStrides = 1; pFlatVb = pipeline("flatvb.vert", 0); vbStrides = 0;
  Target targets[3] = {
    makeTarget("3d", VK_IMAGE_TYPE_3D, VK_IMAGE_CREATE_2D_ARRAY_COMPATIBLE_BIT, N, N, VK_IMAGE_VIEW_TYPE_2D_ARRAY),
    makeTarget("array", VK_IMAGE_TYPE_2D, 0, N, N, VK_IMAGE_VIEW_TYPE_2D_ARRAY),
    makeTarget("cube", VK_IMAGE_TYPE_2D, VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT, N, 6, VK_IMAGE_VIEW_TYPE_CUBE)};
  Target a = makeTarget("a", VK_IMAGE_TYPE_2D, 0, AW, 1, VK_IMAGE_VIEW_TYPE_2D);
  { VkCommandBuffer cb = begin(); for (int i = 0; i < 3; i++) barrier(cb, &targets[i], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
    barrier(cb, &a, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL); end(cb); }
  /* What is recorded into the command buffer before pass B */
  static const char *const before[] = {"alone", "after-vertex-draw-pass", "after-clear-only-pass", "after-mesh-draw-pass", "after-buffer-copy", "after-layered-pass-same-target", "after-2-passes", "after-vertex-buffer-draw-pass"};
  static const VkAttachmentLoadOp loads[] = {VK_ATTACHMENT_LOAD_OP_LOAD, VK_ATTACHMENT_LOAD_OP_DONT_CARE, VK_ATTACHMENT_LOAD_OP_CLEAR};
  static const char *const loadNames[] = {"load", "dontcare", "clear"};
  int fails = 0, cases = 0; unsigned v = 40;
  printf("%-6s %-6s %-9s %-32s %s\n", "target", "stage", "loadOp", "command buffer before pass B", "layers right");
  for (int ti = 0; ti < 3; ti++) for (int si = 0; si < 4; si++) for (int li = 0; li < 3; li++) for (int bi = 0; bi < 8; bi++) {
    const Target *t = &targets[ti]; const int mesh = si == 0 ? 1 : si >= 2 ? 2 : 0;
    static const char *const stageNames[] = {"mesh", "vert", "meshpc", "meshbig"}; const char *stage = stageNames[si];
    VkPipeline pB = si == 0 ? pLayerMesh : si == 2 ? pLayerPc : si == 3 ? pLayerBig : pLayerVert;
    char name[128]; snprintf(name, sizeof name, "%s/%s/%s/%s", t->name, stage, loadNames[li], before[bi]);
    if (filter && !strstr(name, filter)) continue;
    if (!pB || (bi == 3 && !pFlatMesh)) { printf("%-6s %-6s %-9s %-32s SKIP (feature missing)\n", t->name, stage, loadNames[li], before[bi]); continue; }
    v = v >= 240 ? 40 : v + 3;
    /* known content first, in a command buffer of its own: every layer (10, 200, 0) */
    VkCommandBuffer cb = begin(); pass(cb, t, VK_NULL_HANDLE, 0, 0, 10); end(cb);
    cb = begin();
    switch (bi) {
    case 1: pass(cb, &a, pFlatVert, 0, VK_ATTACHMENT_LOAD_OP_CLEAR, 99); break;
    case 2: pass(cb, &a, VK_NULL_HANDLE, 0, 0, 99); break;
    case 3: pass(cb, &a, pFlatMesh, 1, VK_ATTACHMENT_LOAD_OP_CLEAR, 99); break;
    case 4: { VkBufferCopy bc = {0, 0, 4096}; vkCmdCopyBuffer(cb, buf, buf2, 1, &bc); break; }
    case 5: pass(cb, t, pB, mesh, VK_ATTACHMENT_LOAD_OP_LOAD, 20); break;
    case 6: pass(cb, &a, pFlatVert, 0, VK_ATTACHMENT_LOAD_OP_CLEAR, 99); pass(cb, &a, pFlatVert, 0, VK_ATTACHMENT_LOAD_OP_LOAD, 98); break;
    case 7: pass(cb, &a, pFlatVb, 0, VK_ATTACHMENT_LOAD_OP_CLEAR, 99); break; /* leaves binding strides 16, 20, 24 in the command buffer's state */
    }
    pass(cb, t, pB, mesh, loads[li], v);
    end(cb);
    int sample; const unsigned good = check(t, v, &sample);
    cases++; fails += good != t->layers;
    if (good == t->layers) printf("%-6s %-6s %-9s %-32s %2u/%-2u ok\n", t->name, stage, loadNames[li], before[bi], good, t->layers);
    else printf("%-6s %-6s %-9s %-32s %2u/%-2u LOST (want red %u, first wrong texel red %d%s)\n", t->name, stage, loadNames[li], before[bi], good, t->layers, v, sample,
                sample == 10 ? " = content before the command buffer" : sample == 20 ? " = the earlier pass" : sample == 5 ? " = pass B's clear" : "");
  }
  if (fails) printf("RESULT: %d of %d cases LOST layers\n", fails, cases); else printf("RESULT: PASS (%d cases)\n", cases);
  return fails != 0;
}
