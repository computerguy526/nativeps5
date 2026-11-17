// VK_EXT_mesh_shader smoke test for KosmicKrisp (nativeps5): renders mesh-shader draws offscreen and
// checks pixels. Shaders are GLSL compiled by glslang (run.sh). Exit 0 = all checks pass.
//
//   mesh_test <dir with *.spv> [out.ppm prefix]
//
// Cases (each its own 256x256 RGBA8 target):
//   tri    one invocation, 3 vertices, per-vertex + per-primitive colour, push constant, direct draw
//   grid   64 invocations x 2 workgroups, workgroup memory, SSBO + UBO, subgroup ballot,
//          gl_CullPrimitiveEXT, vkCmdDrawMeshTasksIndirectEXT, a vertex-pipeline draw before and after
//   big    256 invocations, 255 vertices / 85 primitives, clip + cull distances, 8 KiB workgroup memory,
//          vkCmdDrawMeshTasksIndirectCountEXT
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define W 256
#define H 256
#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s:%d: %s = %d\n", __FILE__, __LINE__, #x, r_); exit(2); } } while (0)

static VkDevice dev;
static VkPhysicalDevice phys;
static const char* dir;

static VkShaderModule load(const char* name) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s.spv", dir, name);
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(2); }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint32_t* words = malloc(size);
    if (fread(words, 1, size, f) != (size_t)size) exit(2);
    fclose(f);
    VkShaderModuleCreateInfo info = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, size, words};
    VkShaderModule module;
    CHECK(vkCreateShaderModule(dev, &info, NULL, &module));
    free(words);
    return module;
}

static uint32_t memoryType(uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(phys, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & want) == want) return i;
    fprintf(stderr, "no memory type\n");
    exit(2);
}

typedef struct { VkBuffer buffer; VkDeviceMemory memory; void* map; } Buffer;

static Buffer makeBuffer(VkDeviceSize size, VkBufferUsageFlags usage) {
    Buffer b;
    VkBufferCreateInfo info = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, size, usage, VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(dev, &info, NULL, &b.buffer));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, b.buffer, &req);
    VkMemoryAllocateInfo alloc = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, memoryType(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    CHECK(vkAllocateMemory(dev, &alloc, NULL, &b.memory));
    CHECK(vkBindBufferMemory(dev, b.buffer, b.memory, 0));
    CHECK(vkMapMemory(dev, b.memory, 0, size, 0, &b.map));
    return b;
}

static VkResult tryPipeline(VkPipelineLayout layout, const char* first, int mesh, const char* frag, VkPipeline* out) {
    VkPipelineShaderStageCreateInfo stages[2] = {
        {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, mesh ? VK_SHADER_STAGE_MESH_BIT_EXT : VK_SHADER_STAGE_VERTEX_BIT, load(first), "main"},
        {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_FRAGMENT_BIT, load(frag), "main"}};
    VkPipelineVertexInputStateCreateInfo vi = {VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    VkPipelineInputAssemblyStateCreateInfo ia = {VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO, NULL, 0, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
    VkViewport viewport = {0, 0, W, H, 0, 1};
    VkRect2D scissor = {{0, 0}, {W, H}};
    VkPipelineViewportStateCreateInfo vp = {VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO, NULL, 0, 1, &viewport, 1, &scissor};
    VkPipelineRasterizationStateCreateInfo rs = {VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
    rs.polygonMode = VK_POLYGON_MODE_FILL;
    rs.cullMode = VK_CULL_MODE_NONE;
    rs.lineWidth = 1.0f;
    VkPipelineMultisampleStateCreateInfo ms = {VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO, NULL, 0, VK_SAMPLE_COUNT_1_BIT};
    VkPipelineColorBlendAttachmentState blend = {.colorWriteMask = 0xf};
    VkPipelineColorBlendStateCreateInfo cb = {VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO, NULL, 0, VK_FALSE, 0, 1, &blend};
    VkFormat format = VK_FORMAT_R8G8B8A8_UNORM;
    VkPipelineRenderingCreateInfo rendering = {VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO, NULL, 0, 1, &format};
    VkGraphicsPipelineCreateInfo info = {VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, &rendering};
    info.stageCount = 2;
    info.pStages = stages;
    info.pVertexInputState = mesh ? NULL : &vi;
    info.pInputAssemblyState = mesh ? NULL : &ia;
    info.pViewportState = &vp;
    info.pRasterizationState = &rs;
    info.pMultisampleState = &ms;
    info.pColorBlendState = &cb;
    info.layout = layout;
    return vkCreateGraphicsPipelines(dev, VK_NULL_HANDLE, 1, &info, NULL, out);
}

static VkPipeline makePipeline(VkPipelineLayout layout, const char* first, int mesh, const char* frag) {
    VkPipeline pipeline;
    CHECK(tryPipeline(layout, first, mesh, frag, &pipeline));
    return pipeline;
}

static int failures;

// Extension entry points are not exported by the loader.
static PFN_vkCmdDrawMeshTasksEXT drawMeshTasks;
static PFN_vkCmdDrawMeshTasksIndirectEXT drawMeshTasksIndirect;
static PFN_vkCmdDrawMeshTasksIndirectCountEXT drawMeshTasksIndirectCount;

static void expect(const char* test, const uint8_t* pixels, int x, int y, int r, int g, int b, const char* what) {
    const uint8_t* p = pixels + (y * W + x) * 4;
    const int ok = abs(p[0] - r) <= 2 && abs(p[1] - g) <= 2 && abs(p[2] - b) <= 2;
    if (!ok) failures++;
    printf("  %s %-5s (%3d,%3d) %-34s got %3u %3u %3u, want %3d %3d %3d\n", ok ? "ok  " : "FAIL", test, x, y, what, p[0], p[1], p[2], r, g, b);
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: mesh_test <spv dir> [ppm prefix]\n"); return 2; }
    dir = argv[1];
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "mesh_test", 1, "none", 1, VK_API_VERSION_1_3};
    const char* instanceExtensions[] = {"VK_KHR_portability_enumeration"};
    VkInstanceCreateInfo instanceInfo = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR, &app, 0, NULL, 1, instanceExtensions};
    VkInstance instance;
    if (vkCreateInstance(&instanceInfo, NULL, &instance) != VK_SUCCESS) {
        instanceInfo.flags = 0;
        instanceInfo.enabledExtensionCount = 0;
        CHECK(vkCreateInstance(&instanceInfo, NULL, &instance));
    }
    uint32_t count = 1;
    vkEnumeratePhysicalDevices(instance, &count, &phys);
    if (count == 0) { fprintf(stderr, "no Vulkan device\n"); return 2; }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    VkPhysicalDeviceMeshShaderFeaturesEXT meshFeatures = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT};
    VkPhysicalDeviceVulkan13Features features13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, &meshFeatures};
    VkPhysicalDeviceVulkan12Features features12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &features13};
    VkPhysicalDeviceFeatures2 features = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &features12};
    vkGetPhysicalDeviceFeatures2(phys, &features);
    VkPhysicalDeviceMeshShaderPropertiesEXT meshProps = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_PROPERTIES_EXT};
    VkPhysicalDeviceProperties2 props2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &meshProps};
    vkGetPhysicalDeviceProperties2(phys, &props2);
    printf("device: %s, meshShader=%u taskShader=%u, mesh limits: %u invocations, %u vertices, %u primitives, %u B shared, %u B output\n", props.deviceName, meshFeatures.meshShader, meshFeatures.taskShader,
           meshProps.maxMeshWorkGroupInvocations, meshProps.maxMeshOutputVertices, meshProps.maxMeshOutputPrimitives, meshProps.maxMeshSharedMemorySize, meshProps.maxMeshOutputMemorySize);
    if (!meshFeatures.meshShader) { printf("FAIL: device has no meshShader feature\n"); return 1; }

    VkPhysicalDeviceMeshShaderFeaturesEXT enableMesh = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, NULL, VK_FALSE, VK_TRUE};
    VkPhysicalDeviceVulkan13Features enable13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, &enableMesh};
    enable13.dynamicRendering = VK_TRUE;
    enable13.synchronization2 = VK_TRUE;
    VkPhysicalDeviceVulkan12Features enable12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &enable13};
    enable12.drawIndirectCount = features12.drawIndirectCount;
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queueInfo = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &priority};
    const char* deviceExtensions[] = {VK_EXT_MESH_SHADER_EXTENSION_NAME};
    VkDeviceCreateInfo deviceInfo = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &enable12, 0, 1, &queueInfo, 0, NULL, 1, deviceExtensions};
    CHECK(vkCreateDevice(phys, &deviceInfo, NULL, &dev));
    VkQueue queue;
    vkGetDeviceQueue(dev, 0, 0, &queue);
    drawMeshTasks = (PFN_vkCmdDrawMeshTasksEXT)vkGetDeviceProcAddr(dev, "vkCmdDrawMeshTasksEXT");
    drawMeshTasksIndirect = (PFN_vkCmdDrawMeshTasksIndirectEXT)vkGetDeviceProcAddr(dev, "vkCmdDrawMeshTasksIndirectEXT");
    drawMeshTasksIndirectCount = (PFN_vkCmdDrawMeshTasksIndirectCountEXT)vkGetDeviceProcAddr(dev, "vkCmdDrawMeshTasksIndirectCountEXT");
    if (!drawMeshTasks || !drawMeshTasksIndirect || !drawMeshTasksIndirectCount) { printf("FAIL: mesh draw entry points are missing\n"); return 1; }

    // Descriptors: storage buffer (palette) + uniform buffer (params), both read by the mesh stage.
    VkDescriptorSetLayoutBinding bindings[2] = {
        {0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_MESH_BIT_EXT},
        {1, VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 1, VK_SHADER_STAGE_MESH_BIT_EXT}};
    VkDescriptorSetLayoutCreateInfo setInfo = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, 2, bindings};
    VkDescriptorSetLayout setLayout;
    CHECK(vkCreateDescriptorSetLayout(dev, &setInfo, NULL, &setLayout));
    VkPushConstantRange push = {VK_SHADER_STAGE_MESH_BIT_EXT, 0, 16};
    VkPipelineLayoutCreateInfo layoutInfo = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, 1, &setLayout, 1, &push};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(dev, &layoutInfo, NULL, &layout));
    VkDescriptorPoolSize poolSizes[2] = {{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1}, {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 1}};
    VkDescriptorPoolCreateInfo poolInfo = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, 0, 1, 2, poolSizes};
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(dev, &poolInfo, NULL, &pool));
    VkDescriptorSetAllocateInfo setAlloc = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, pool, 1, &setLayout};
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(dev, &setAlloc, &set));
    Buffer palette = makeBuffer(64, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    const float colors[16] = {1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 1};
    memcpy(palette.map, colors, sizeof(colors));
    Buffer params = makeBuffer(32, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
    const float primitiveColor[4] = {0, 0, 0.5f, 1};
    const uint32_t cullCell = 21;
    memcpy(params.map, primitiveColor, 16);
    memcpy((char*)params.map + 16, &cullCell, 4);
    VkDescriptorBufferInfo paletteInfo = {palette.buffer, 0, 64}, paramsInfo = {params.buffer, 0, 32};
    VkWriteDescriptorSet writes[2] = {
        {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, set, 0, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &paletteInfo},
        {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, set, 1, 0, 1, VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, NULL, &paramsInfo}};
    vkUpdateDescriptorSets(dev, 2, writes, 0, NULL);

    // Indirect arguments: [0] grid draw {2,1,1}; [1..2] big draws {1,1,1} x2 with a count of 1.
    Buffer indirect = makeBuffer(64, VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT);
    const uint32_t args[] = {2, 1, 1, 1, 1, 1, 1, 1, 1, /* count */ 1};
    memcpy(indirect.map, args, sizeof(args));

    // MESH_TEST_CASES=tri,grid limits the run to some cases.
    const char* only = getenv("MESH_TEST_CASES");
    VkPipeline plain = makePipeline(layout, "plain.vert", 0, "plain.frag");

    VkCommandPoolCreateInfo cmdPoolInfo = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, 0};
    VkCommandPool cmdPool;
    CHECK(vkCreateCommandPool(dev, &cmdPoolInfo, NULL, &cmdPool));
    VkCommandBufferAllocateInfo cmdAlloc = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, cmdPool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cmd;
    CHECK(vkAllocateCommandBuffers(dev, &cmdAlloc, &cmd));
    Buffer readback = makeBuffer(W * H * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);

    const char* names[3] = {"tri", "grid", "big"};
    for (int test = 0; test < 3; test++) {
        if (only != NULL && strstr(only, names[test]) == NULL) continue;
        VkPipeline pipeline = test == 0 ? makePipeline(layout, "tri.mesh", 1, "common.frag") : test == 1 ? makePipeline(layout, "grid.mesh", 1, "common.frag") : makePipeline(layout, "big.mesh", 1, "big.frag");
        VkImageCreateInfo imageInfo = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, NULL, 0, VK_IMAGE_TYPE_2D, VK_FORMAT_R8G8B8A8_UNORM, {W, H, 1}, 1, 1, VK_SAMPLE_COUNT_1_BIT, VK_IMAGE_TILING_OPTIMAL,
                                       VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT};
        VkImage image;
        CHECK(vkCreateImage(dev, &imageInfo, NULL, &image));
        VkMemoryRequirements req;
        vkGetImageMemoryRequirements(dev, image, &req);
        VkMemoryAllocateInfo alloc = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, memoryType(req.memoryTypeBits, 0)};
        VkDeviceMemory memory;
        CHECK(vkAllocateMemory(dev, &alloc, NULL, &memory));
        CHECK(vkBindImageMemory(dev, image, memory, 0));
        VkImageViewCreateInfo viewInfo = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, NULL, 0, image, VK_IMAGE_VIEW_TYPE_2D, VK_FORMAT_R8G8B8A8_UNORM, {0}, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
        VkImageView view;
        CHECK(vkCreateImageView(dev, &viewInfo, NULL, &view));

        VkCommandBufferBeginInfo begin = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        CHECK(vkBeginCommandBuffer(cmd, &begin));
        VkImageMemoryBarrier toColor = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, NULL, 0, VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
                                        VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, image, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, NULL, 0, NULL, 1, &toColor);
        VkRenderingAttachmentInfo color = {VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, NULL, view, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
        color.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
        color.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
        color.clearValue.color.float32[0] = 0.1f;
        color.clearValue.color.float32[1] = 0.1f;
        color.clearValue.color.float32[2] = 0.1f;
        color.clearValue.color.float32[3] = 1.0f;
        VkRenderingInfo rendering = {VK_STRUCTURE_TYPE_RENDERING_INFO, NULL, 0, {{0, 0}, {W, H}}, 1, 0, 1, &color};
        vkCmdBeginRendering(cmd, &rendering);
        vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1, &set, 0, NULL);
        const float tint[4] = {0.2f, 0.4f, 0.6f, 1.0f};
        vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_MESH_BIT_EXT, 0, 16, tint);
        if (test == 0) {
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
            drawMeshTasks(cmd, 1, 1, 1);
        } else if (test == 1) {
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, plain);
            vkCmdDraw(cmd, 3, 1, 0, 0);
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
            drawMeshTasksIndirect(cmd, indirect.buffer, 0, 1, 12);
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, plain);
            vkCmdDraw(cmd, 3, 1, 0, 0);
        } else {
            vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
            if (features12.drawIndirectCount) drawMeshTasksIndirectCount(cmd, indirect.buffer, 12, indirect.buffer, 36, 2, 12);
            else drawMeshTasks(cmd, 1, 1, 1);
        }
        vkCmdEndRendering(cmd);
        VkImageMemoryBarrier toSource = toColor;
        toSource.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
        toSource.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        toSource.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        toSource.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &toSource);
        VkBufferImageCopy copy = {0, 0, 0, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {0, 0, 0}, {W, H, 1}};
        vkCmdCopyImageToBuffer(cmd, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, readback.buffer, 1, &copy);
        CHECK(vkEndCommandBuffer(cmd));
        VkSubmitInfo submit = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cmd};
        CHECK(vkQueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
        CHECK(vkQueueWaitIdle(queue));

        const uint8_t* pixels = readback.map;
        if (argc > 2) {
            char path[1024];
            snprintf(path, sizeof(path), "%s%s.ppm", argv[2], names[test]);
            FILE* f = fopen(path, "wb");
            if (f) {
                fprintf(f, "P6\n%d %d\n255\n", W, H);
                for (int i = 0; i < W * H; i++) fwrite(pixels + i * 4, 1, 3, f);
                fclose(f);
            }
        }
        printf("%s:\n", names[test]);
        const int clear = 26;   // 0.1
        if (test == 0) {
            // centre: vertex colours weighted 1/4, 1/4, 1/2 -> (.25,.25,.5); out = v/2 + tint/2
            expect("tri", pixels, 128, 128, 57, 83, 140, "centre: per-vertex + per-primitive");
            expect("tri", pixels, 4, 4, clear, clear, clear, "outside the triangle");
            // row 40: vertex weights (.464,.464,.073); row 215: (.036,.036,.927)
            expect("tri", pixels, 128, 40, 85, 110, 86, "near the red-green edge");
            expect("tri", pixels, 128, 215, 30, 56, 195, "near the blue vertex");
        } else if (test == 1) {
            // cell (cx, cy) of workgroup g: self = cy * 8 + cx, colour palette[(self + g) & 3] / 2
            // cell size 16 x 32 pixels; workgroup 0 is x in [0,128), workgroup 1 x in [128,256)
            expect("grid", pixels, 8 + 16 * 1, 16 + 32 * 0, 0, 128, 64, "group 0 cell 1: green");
            expect("grid", pixels, 8 + 16 * 2, 16 + 32 * 1, 0, 0, 192, "group 0 cell 10: blue");
            expect("grid", pixels, 8 + 16 * 7, 16 + 32 * 7, 128, 128, 64, "group 0 cell 63: yellow");
            expect("grid", pixels, 128 + 8 + 16 * 0, 16 + 32 * 0, 0, 128, 64, "group 1 cell 0: green");
            expect("grid", pixels, 128 + 8 + 16 * 3, 16 + 32 * 2, 128, 0, 64, "group 1 cell 19: red");
            // cell 21 (cx 5, cy 2): first triangle (lower-left half) culled, second kept
            expect("grid", pixels, 16 * 5 + 2, 32 * 2 + 3, clear, clear, clear, "group 0 cell 21: culled primitive");
            expect("grid", pixels, 16 * 5 + 13, 32 * 2 + 28, 0, 128, 64, "group 0 cell 21: kept primitive");
            // vertex-pipeline triangle: drawn before the grid (covered) and after (white on top), at z 0.1
            expect("grid", pixels, 4, 4, 255, 255, 255, "vertex pipeline after the mesh draw");
        } else {
            // triangle t in cell (t & 15, t >> 4), 16 x 42.67 pixels, lower-left half; colour (0,1,t&1)
            expect("big", pixels, 16 * 0 + 3, 4, 0, 255, 0, "triangle 0: green");
            expect("big", pixels, 16 * 1 + 3, 4, 0, 255, 255, "triangle 1: green + blue");
            expect("big", pixels, 16 * 4 + 3, 4, clear, clear, clear, "triangle 4: clipped (clip distance)");
            expect("big", pixels, 16 * 6 + 3, 4, clear, clear, clear, "triangle 6: culled (cull distance)");
            expect("big", pixels, 16 * 2 + 3, 5 * 43 + 4, 0, 255, 0, "triangle 82 (last row): green");
            expect("big", pixels, 16 * 5 + 3, 5 * 43 + 4, clear, clear, clear, "no triangle 85");
            expect("big", pixels, 16 * 0 + 14, 40, clear, clear, clear, "upper-right half of cell 0");
        }
        vkDestroyPipeline(dev, pipeline, NULL);
        vkDestroyImageView(dev, view, NULL);
        vkDestroyImage(dev, image, NULL);
        vkFreeMemory(dev, memory, NULL);
    }
    vkDeviceWaitIdle(dev);
    {
        // Pipelines at the edges of what the lowering must handle (no draw: creation only).
        VkPipeline pipeline = VK_NULL_HANDLE;
        VkResult r = tryPipeline(layout, "many.mesh", 1, "many.frag", &pipeline);
        printf("  %s many  100 scalar outputs: pipeline creation returned %d\n", r == VK_SUCCESS ? "ok  " : "FAIL", (int)r);
        if (r != VK_SUCCESS) failures++; else vkDestroyPipeline(dev, pipeline, NULL);
        pipeline = VK_NULL_HANDLE;
        r = tryPipeline(layout, "huge.mesh", 1, "common.frag", &pipeline);
        printf("  %s huge  28 KiB shared + staged outputs over 32 KiB: pipeline creation returned %d (want an error)\n", r != VK_SUCCESS ? "ok  " : "FAIL", (int)r);
        if (r == VK_SUCCESS) { failures++; vkDestroyPipeline(dev, pipeline, NULL); }
        r = tryPipeline(layout, "tri.mesh", 1, "common.frag", &pipeline);
        printf("  %s tri   a valid pipeline still builds after the failure: %d\n", r == VK_SUCCESS ? "ok  " : "FAIL", (int)r);
        if (r != VK_SUCCESS) failures++; else vkDestroyPipeline(dev, pipeline, NULL);
    }
    printf(failures ? "FAIL: %d pixel checks failed\n" : "PASS: all pixel checks passed\n", failures);
    return failures ? 1 : 0;
}
