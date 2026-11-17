// Robust vector SSBO load test for KosmicKrisp patch 0005 (bounds-checked 2-4 component loads stay vector loads).
// The SSBO is bound with a range that ends inside, or exactly at, a vector: every component must be the buffer
// value when its own 4 bytes are in range and zero otherwise (robustBufferAccess2, 4-byte granularity),
// never a value from beyond the range, and never zero for an in-range component of a partially OOB vector.
//   vecload_test <vecload.comp.spv>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s:%d: %s = %d\n", __FILE__, __LINE__, #x, r_); exit(2); } } while (0)
enum { BUFSZ = 512, N = 32 };

static VkDevice dev; static VkPhysicalDevice phys;

static uint32_t memtype(uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties p; vkGetPhysicalDeviceMemoryProperties(phys, &p);
    for (uint32_t i = 0; i < p.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (p.memoryTypes[i].propertyFlags & want) == want) return i;
    fprintf(stderr, "no memory type\n"); exit(2);
}
static VkBuffer mkbuf(VkDeviceSize size, void** map) {
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, size, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer b; CHECK(vkCreateBuffer(dev, &bi, NULL, &b));
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(dev, b, &mr);
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, mr.size,
        memtype(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    VkDeviceMemory m; CHECK(vkAllocateMemory(dev, &ai, NULL, &m));
    CHECK(vkBindBufferMemory(dev, b, m, 0));
    CHECK(vkMapMemory(dev, m, 0, VK_WHOLE_SIZE, 0, map));
    return b;
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: vecload_test shader.spv\n"); return 2; }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vecload", 1, "x", 1, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ii = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
    VkInstance inst; CHECK(vkCreateInstance(&ii, NULL, &inst));
    uint32_t n = 1; CHECK(vkEnumeratePhysicalDevices(inst, &n, &phys) == VK_INCOMPLETE ? VK_SUCCESS : VK_SUCCESS);
    VkPhysicalDeviceProperties pp; vkGetPhysicalDeviceProperties(phys, &pp);
    printf("device: %s\n", pp.deviceName);

    VkPhysicalDeviceRobustness2FeaturesEXT r2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT};
    VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &r2};
    vkGetPhysicalDeviceFeatures2(phys, &f2);
    int have_r2 = r2.robustBufferAccess2 == VK_TRUE;
    printf("robustBufferAccess=%d robustBufferAccess2=%d\n", f2.features.robustBufferAccess, have_r2);
    if (!f2.features.robustBufferAccess) { fprintf(stderr, "no robustBufferAccess\n"); return 2; }
    VkPhysicalDeviceRobustness2FeaturesEXT r2on = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT, NULL, have_r2, VK_FALSE, VK_FALSE};
    VkPhysicalDeviceFeatures2 en = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, have_r2 ? &r2on : NULL};
    en.features.robustBufferAccess = VK_TRUE;
    float prio = 1.f;
    VkDeviceQueueCreateInfo qi = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio};
    const char* dext[] = {"VK_EXT_robustness2"};
    VkDeviceCreateInfo di = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &en, 0, 1, &qi, 0, NULL, have_r2 ? 1u : 0u, dext, NULL};
    CHECK(vkCreateDevice(phys, &di, NULL, &dev));
    VkQueue q; vkGetDeviceQueue(dev, 0, 0, &q);

    FILE* f = fopen(argv[1], "rb"); if (!f) { perror("spv"); return 2; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint32_t* code = malloc(sz); if (fread(code, 1, sz, f) != (size_t)sz) return 2; fclose(f);
    VkShaderModuleCreateInfo smi = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, (size_t)sz, code};
    VkShaderModule sm; CHECK(vkCreateShaderModule(dev, &smi, NULL, &sm));

    VkDescriptorSetLayoutBinding lb[2] = {
        {0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL},
        {1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL}};
    VkDescriptorSetLayoutCreateInfo dli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, 2, lb};
    VkDescriptorSetLayout dsl; CHECK(vkCreateDescriptorSetLayout(dev, &dli, NULL, &dsl));
    VkPipelineLayoutCreateInfo pli = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, 1, &dsl, 0, NULL};
    VkPipelineLayout pl; CHECK(vkCreatePipelineLayout(dev, &pli, NULL, &pl));
    VkComputePipelineCreateInfo ci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, NULL, 0,
        {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_COMPUTE_BIT, sm, "main", NULL}, pl, VK_NULL_HANDLE, 0};
    VkPipeline pipe; CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &ci, NULL, &pipe));
    VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2};
    VkDescriptorPoolCreateInfo dpi = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, 0, 1, 1, &ps};
    VkDescriptorPool dp; CHECK(vkCreateDescriptorPool(dev, &dpi, NULL, &dp));
    VkDescriptorSetAllocateInfo dai = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, dp, 1, &dsl};
    VkDescriptorSet ds; CHECK(vkAllocateDescriptorSets(dev, &dai, &ds));

    uint32_t *in, *out;
    VkBuffer bin = mkbuf(BUFSZ, (void**)&in), bout = mkbuf(N * 16, (void**)&out);
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, 0};
    VkCommandPool cp; CHECK(vkCreateCommandPool(dev, &cpi, NULL, &cp));
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, cp, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb; CHECK(vkAllocateCommandBuffers(dev, &cai, &cb));

    // ranges: whole elements, partial vec4 (2 and 3 dwords of the last vec4 in range), tiny, odd uvec3 boundary.
    const uint32_t ranges[] = {128, 64, 56, 60, 52, 16, 20, 24, 36, 12, 4};
    int fails = 0, checked = 0;
    for (unsigned ri = 0; ri < sizeof ranges / sizeof *ranges; ri++) {
        uint32_t range = ranges[ri];
        for (int i = 0; i < BUFSZ / 4; i++) in[i] = 0x1000u + i;   // never zero: OOB reads would show
        memset(out, 0x55, N * 16);
        VkDescriptorBufferInfo b0 = {bin, 0, range}, b1 = {bout, 0, VK_WHOLE_SIZE};
        VkWriteDescriptorSet w[2] = {
            {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, ds, 0, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &b0, NULL},
            {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, ds, 1, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &b1, NULL}};
        vkUpdateDescriptorSets(dev, 2, w, 0, NULL);
        VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        CHECK(vkResetCommandBuffer(cb, 0)); CHECK(vkBeginCommandBuffer(cb, &bi));
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &ds, 0, NULL);
        vkCmdDispatch(cb, 1, 1, 1);
        CHECK(vkEndCommandBuffer(cb));
        VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb};
        CHECK(vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE)); CHECK(vkQueueWaitIdle(q));
        for (int inv = 0; inv < 24; inv++) {
            int ncomp, stride_dw, elt;           // view: vec4 / vec2 / 3-dword struct (stride 3 dwords... std430 T3 is 12 bytes)
            if (inv < 8)  { ncomp = 4; stride_dw = 4; elt = inv; }
            else if (inv < 16) { ncomp = 2; stride_dw = 2; elt = inv - 8; }
            else { ncomp = 3; stride_dw = 3; elt = inv - 16; }
            for (int c = 0; c < 4; c++) {
                uint32_t byteoff = (elt * stride_dw + c) * 4;
                uint32_t want = 0;
                if (c < ncomp && byteoff + 4 <= range) want = 0x1000u + byteoff / 4;
                uint32_t got = out[inv * 4 + c];
                checked++;
                if (got != want) {
                    if (fails++ < 20) fprintf(stderr, "FAIL range=%u inv=%d comp=%d got=0x%x want=0x%x\n", range, inv, c, got, want);
                }
            }
        }
    }
    printf("%s: %d components checked, %d failures\n", fails ? "FAIL" : "PASS", checked, fails);
    return fails ? 1 : 0;
}
