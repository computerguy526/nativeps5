// KosmicKrisp NPS5_KK_SWITCH_TREE (patch 0035): a compute kernel with a 24-leaf if (x < K) tree gives the same results
// whether or not the tree is printed as one MSL switch. Run by run.sh without and with the switch; exit 0 = all inputs equal the CPU reference.
//   switchtree_test <switchtree.comp.spv>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s:%d: %s = %d\n", __FILE__, __LINE__, #x, r_); exit(2); } } while (0)
enum { N = 256 };

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
    if (argc < 2) { fprintf(stderr, "usage: switchtree_test shader.spv\n"); return 2; }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "switchtree", 1, "x", 1, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ii = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
    VkInstance inst; CHECK(vkCreateInstance(&ii, NULL, &inst));
    uint32_t n = 1; vkEnumeratePhysicalDevices(inst, &n, &phys);
    VkPhysicalDeviceProperties pp; vkGetPhysicalDeviceProperties(phys, &pp);
    printf("device: %s\n", pp.deviceName);
    float prio = 1.f;
    VkDeviceQueueCreateInfo qi = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio};
    VkDeviceCreateInfo di = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qi, 0, NULL, 0, NULL, NULL};
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
    VkBuffer bin = mkbuf(N * 4, (void**)&in), bout = mkbuf(N * 4, (void**)&out);
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, 0};
    VkCommandPool cp; CHECK(vkCreateCommandPool(dev, &cpi, NULL, &cp));
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, cp, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb; CHECK(vkAllocateCommandBuffers(dev, &cai, &cb));
    // inputs: every leaf boundary (K-1, K, K+1 for K = 10, 20, ..., 240) and the extremes
    for (int i = 0; i < N; i++) in[i] = (uint32_t)i;
    in[N - 1] = 0xffffffffu; in[N - 2] = 100000u;
    memset(out, 0x55, N * 4);
    VkDescriptorBufferInfo b0 = {bin, 0, VK_WHOLE_SIZE}, b1 = {bout, 0, VK_WHOLE_SIZE};
    VkWriteDescriptorSet w[2] = {
        {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, ds, 0, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &b0, NULL},
        {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, ds, 1, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &b1, NULL}};
    vkUpdateDescriptorSets(dev, 2, w, 0, NULL);
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CHECK(vkBeginCommandBuffer(cb, &bi));
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &ds, 0, NULL);
    vkCmdDispatch(cb, N / 64, 1, 1);
    CHECK(vkEndCommandBuffer(cb));
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb};
    CHECK(vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE)); CHECK(vkQueueWaitIdle(q));
    int fails = 0;
    for (int i = 0; i < N; i++) {
        // CPU reference of the tree in switchtree.comp: leaf k (1..24) for x < 10*k, leaf 25 otherwise; result = leaf * 1000 + x % 1000.
        uint32_t x = in[i], leaf = 25;
        for (uint32_t k = 1; k <= 24; k++) if (x < 10u * k) { leaf = k; break; }
        uint32_t want = leaf * 1000u + x % 1000u;
        if (out[i] != want) { if (fails++ < 20) fprintf(stderr, "FAIL i=%d x=%u got=%u want=%u\n", i, x, out[i], want); }
    }
    printf("%s: %d inputs, %d failures\n", fails ? "FAIL" : "PASS", N, fails);
    return fails ? 1 : 0;
}
