// 64-bit buffer atomics test for KosmicKrisp (nativeps5): shaderBufferInt64Atomics under contention,
// checked against a CPU reference. Shaders are atomics.comp compiled per operation by run.sh.
//
//   atomics_test <dir with op<n>.spv and op<n>.bda.spv> [operation name [word count]]
//
// Every case dispatches N invocations that each apply one 64-bit atomic to slot[hash(i) % slots] and
// record the value the atomic returned. slots = 1 puts every invocation (and every lane of a SIMD
// group) on one word; 64 and 4096 spread them. Checks per case:
//   final   the slots equal the CPU fold of all operands (operations whose result is order-free)
//   chain   the returned values are consistent with SOME serial order: per slot, the multiset
//           {returned} + {final} equals {op(returned, operand)} + {initial}. A torn or lost update
//           breaks it. (Necessary for atomicity, not a full linearizability proof.)
//   casloop atomicLoad + atomicCompSwap retry loops add up exactly
//   torn    concurrent atomicStore / atomicLoad never show a word made of two stores
//   control dword-wise stores against 64-bit loads MUST show torn words (the plain case can see a tear)
//   plain   the same with plain aligned 64-bit loads and stores (no 64-bit atomic at all); this case
//           always runs, the others need shaderBufferInt64Atomics (KosmicKrisp: kk_int64_atomics=true)
// Each operation runs through a storage buffer binding and through a buffer_reference (physical
// storage buffer) pointer.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <vulkan/vulkan.h>

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s:%d: %s = %d\n", __FILE__, __LINE__, #x, r_); exit(2); } } while (0)

enum { OP_ADD, OP_UMIN, OP_UMAX, OP_SMIN, OP_SMAX, OP_AND, OP_OR, OP_XOR, OP_XCHG, OP_CAS, OP_CASLOOP, OP_LOADSTORE, OP_PLAIN, OP_CONTROL, OP_COUNT };
static const char* const opNames[OP_COUNT] = {"add", "umin", "umax", "smin", "smax", "and", "or", "xor", "xchg", "cas", "casloop", "loadstore", "plain", "control"};

static VkDevice dev;
static VkPhysicalDevice phys;
static const char* dir;
static int failures;

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

typedef struct { VkBuffer buffer; VkDeviceMemory memory; void* map; VkDeviceAddress address; } Buffer;

static Buffer makeBuffer(VkDeviceSize size) {
    Buffer b;
    VkBufferCreateInfo info = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, size, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(dev, &info, NULL, &b.buffer));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, b.buffer, &req);
    VkMemoryAllocateFlagsInfo flags = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO, NULL, VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT};
    VkMemoryAllocateInfo alloc = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &flags, req.size, memoryType(req.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    CHECK(vkAllocateMemory(dev, &alloc, NULL, &b.memory));
    CHECK(vkBindBufferMemory(dev, b.buffer, b.memory, 0));
    CHECK(vkMapMemory(dev, b.memory, 0, size, 0, &b.map));
    VkBufferDeviceAddressInfo addressInfo = {VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO, NULL, b.buffer};
    b.address = vkGetBufferDeviceAddress(dev, &addressInfo);
    return b;
}

// ---- the operand and slot functions of atomics.comp ----
static uint32_t hash(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16;
    return x;
}
static uint64_t pack(uint32_t hi, uint32_t lo) { return ((uint64_t)hi << 32) | lo; }
static uint64_t operand(int op, uint32_t i) {
    const uint32_t h1 = hash(2u * i + 1u), h2 = hash(2u * i + 2u);
    switch (op) {
        case OP_ADD: case OP_CASLOOP: return pack(h1 & 0xffu, 0xffffff00u | (h2 & 0xffu));
        case OP_UMIN: case OP_UMAX: return pack(h1 & 0x3ffu, h2);
        case OP_SMIN: case OP_SMAX: return pack((uint32_t)((int32_t)(h1 & 0x3ffu) - 512), h2);
        case OP_AND: return (h2 & 3u) == 0u ? ~((uint64_t)1 << (h1 & 63u)) : ~(uint64_t)0;
        case OP_OR: return (h2 & 3u) == 0u ? ((uint64_t)1 << (h1 & 63u)) : 0;
        case OP_XOR: return pack(h1, h2);
        default: return pack(~i, i);
    }
}
static uint32_t slotOf(uint32_t i, uint32_t slots) { return hash(i ^ 0x9e3779b9u) % slots; }

static uint64_t initial(int op) {
    switch (op) {
        case OP_ADD: case OP_CASLOOP: return 0x00000000fffffff0ull;
        case OP_UMIN: return ~0ull;
        case OP_SMIN: return 0x7fffffffffffffffull;
        case OP_SMAX: return 0x8000000000000000ull;
        case OP_AND: return ~0ull;
        case OP_XOR: return 0x0123456789abcdefull;
        case OP_XCHG: return 0xdeadbeef00000001ull;
        case OP_LOADSTORE: case OP_PLAIN: case OP_CONTROL: return pack(~0u, 0u);
        default: return 0;
    }
}
static uint64_t apply(int op, uint64_t old, uint64_t v) {
    switch (op) {
        case OP_ADD: case OP_CASLOOP: return old + v;
        case OP_UMIN: return old < v ? old : v;
        case OP_UMAX: return old > v ? old : v;
        case OP_SMIN: return (int64_t)old < (int64_t)v ? old : v;
        case OP_SMAX: return (int64_t)old > (int64_t)v ? old : v;
        case OP_AND: return old & v;
        case OP_OR: return old | v;
        case OP_XOR: return old ^ v;
        case OP_XCHG: return v;
        case OP_CAS: return old == 0 ? v : old;
        default: return old;
    }
}
static int orderFree(int op) { return op <= OP_XOR || op == OP_CASLOOP; }

typedef struct { uint32_t slot; uint64_t value; } Entry;
static int compareEntries(const void* a, const void* b) {
    const Entry* x = a; const Entry* y = b;
    if (x->slot != y->slot) return x->slot < y->slot ? -1 : 1;
    return x->value < y->value ? -1 : x->value > y->value ? 1 : 0;
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: atomics_test <spv dir> [operation name [word count]]\n"); return 2; }
    dir = argv[1];
    setvbuf(stdout, NULL, _IOLBF, 0);
    const char* onlyOp = argc > 2 ? argv[2] : NULL;
    const uint32_t onlySlots = argc > 3 ? (uint32_t)strtoul(argv[3], NULL, 0) : 0;
    int cases = 0;
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "atomics_test", 1, "none", 1, VK_API_VERSION_1_3};
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
    VkPhysicalDeviceVulkan12Features features12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
    VkPhysicalDeviceFeatures2 features = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &features12};
    vkGetPhysicalDeviceFeatures2(phys, &features);
    printf("device: %s, shaderBufferInt64Atomics=%u shaderSharedInt64Atomics=%u shaderInt64=%u bufferDeviceAddress=%u\n", props.deviceName, features12.shaderBufferInt64Atomics,
           features12.shaderSharedInt64Atomics, features.features.shaderInt64, features12.bufferDeviceAddress);
    if (!features.features.shaderInt64 || !features12.bufferDeviceAddress) {
        printf("FAIL: device has no shaderInt64 / bufferDeviceAddress\n");
        return 1;
    }
    const int atomics = features12.shaderBufferInt64Atomics == VK_TRUE;
    if (!atomics) printf("  note: no shaderBufferInt64Atomics (KosmicKrisp: set kk_int64_atomics=true to expose the lock emulation); only the plain 64-bit access case runs\n");
    VkPhysicalDeviceVulkan12Features enable12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
    enable12.shaderBufferInt64Atomics = features12.shaderBufferInt64Atomics;
    enable12.bufferDeviceAddress = VK_TRUE;
    enable12.vulkanMemoryModel = features12.vulkanMemoryModel;
    VkPhysicalDeviceFeatures2 enable = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &enable12};
    enable.features.shaderInt64 = VK_TRUE;

    uint32_t familyCount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &familyCount, NULL);
    VkQueueFamilyProperties* families = calloc(familyCount, sizeof(*families));
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &familyCount, families);
    uint32_t family = 0;
    while (family < familyCount && !(families[family].queueFlags & VK_QUEUE_COMPUTE_BIT)) family++;
    if (family == familyCount) { fprintf(stderr, "no compute queue\n"); return 2; }
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queueInfo = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, family, 1, &priority};
    VkDeviceCreateInfo deviceInfo = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &enable, 0, 1, &queueInfo};
    CHECK(vkCreateDevice(phys, &deviceInfo, NULL, &dev));
    VkQueue queue;
    vkGetDeviceQueue(dev, family, 0, &queue);
    VkCommandPoolCreateInfo poolInfo = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, family};
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(dev, &poolInfo, NULL, &pool));
    VkCommandBufferAllocateInfo cmdInfo = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cmd;
    CHECK(vkAllocateCommandBuffers(dev, &cmdInfo, &cmd));

    enum { MAX_N = 1 << 18, MAX_SLOTS = 4096 };
    Buffer slots = makeBuffer(MAX_SLOTS * 8);
    Buffer out = makeBuffer((VkDeviceSize)MAX_N * 8);
    Buffer stat = makeBuffer(16);

    VkDescriptorSetLayoutBinding bindings[3];
    for (uint32_t i = 0; i < 3; i++) bindings[i] = (VkDescriptorSetLayoutBinding){i, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL};
    VkDescriptorSetLayoutCreateInfo setLayoutInfo = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, 3, bindings};
    VkDescriptorSetLayout setLayout;
    CHECK(vkCreateDescriptorSetLayout(dev, &setLayoutInfo, NULL, &setLayout));
    VkPushConstantRange range = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 16};
    VkPipelineLayoutCreateInfo layoutInfo = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, 1, &setLayout, 1, &range};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(dev, &layoutInfo, NULL, &layout));
    VkDescriptorPoolSize poolSize = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 3};
    VkDescriptorPoolCreateInfo descriptorPoolInfo = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, 0, 1, 1, &poolSize};
    VkDescriptorPool descriptorPool;
    CHECK(vkCreateDescriptorPool(dev, &descriptorPoolInfo, NULL, &descriptorPool));
    VkDescriptorSetAllocateInfo setInfo = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, descriptorPool, 1, &setLayout};
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(dev, &setInfo, &set));
    VkDescriptorBufferInfo bufferInfos[3] = {{slots.buffer, 0, VK_WHOLE_SIZE}, {out.buffer, 0, VK_WHOLE_SIZE}, {stat.buffer, 0, VK_WHOLE_SIZE}};
    VkWriteDescriptorSet writes[3];
    for (uint32_t i = 0; i < 3; i++) writes[i] = (VkWriteDescriptorSet){VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, set, i, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &bufferInfos[i], NULL};
    vkUpdateDescriptorSets(dev, 3, writes, 0, NULL);

    Entry* got = malloc(sizeof(Entry) * (MAX_N + MAX_SLOTS));
    Entry* want = malloc(sizeof(Entry) * (MAX_N + MAX_SLOTS));
    uint64_t* expected = malloc(sizeof(uint64_t) * MAX_SLOTS);
    static const uint32_t slotCounts[3] = {1, 64, 4096};

    for (int bda = 0; bda < 2; bda++) {
        for (int op = 0; op < OP_COUNT; op++) {
            if (onlyOp && strcmp(onlyOp, opNames[op]) != 0) continue;
            if (!atomics && op != OP_PLAIN && op != OP_CONTROL) continue;
            if (op == OP_CONTROL && !bda) continue;
            char name[64];
            snprintf(name, sizeof(name), bda ? "op%d.bda" : "op%d", op);
            VkComputePipelineCreateInfo pipelineInfo = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
            pipelineInfo.stage = (VkPipelineShaderStageCreateInfo){VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_COMPUTE_BIT, load(name), "main"};
            pipelineInfo.layout = layout;
            VkPipeline pipeline;
            CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &pipelineInfo, NULL, &pipeline));
            for (int c = 0; c < 3; c++) {
                const uint32_t slotCount = slotCounts[c];
                if (onlySlots && onlySlots != slotCount) continue;
                cases++;
                // The lock emulation serialises the invocations on one word, and a contended hand-over of the driver's lock
                // costs about 0.5 ms on an M4 Pro: few invocations there, same contention. The plain case has no lock.
                uint32_t n = op == OP_PLAIN || op == OP_CONTROL ? MAX_N : op == OP_CASLOOP ? (slotCount == 1 ? 1u << 8 : 1u << 14) : slotCount == 1 ? 1u << 10 : 1u << 16;
                if (getenv("ATOMICS_N")) n = (uint32_t)strtoul(getenv("ATOMICS_N"), NULL, 0);   // debugging aid: multiple of 64, at most 262144
                uint64_t* slotWords = slots.map;
                for (uint32_t s = 0; s < MAX_SLOTS; s++) slotWords[s] = initial(op);
                memset(out.map, 0xa5, (size_t)n * 8);
                memset(stat.map, 0, 16);

                CHECK(vkResetCommandBuffer(cmd, 0));
                VkCommandBufferBeginInfo begin = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
                CHECK(vkBeginCommandBuffer(cmd, &begin));
                vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
                vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
                struct { uint64_t address; uint32_t slotCount; uint32_t pad; } push = {slots.address, slotCount, 0};
                vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, 16, &push);
                vkCmdDispatch(cmd, n / 64, 1, 1);
                CHECK(vkEndCommandBuffer(cmd));
                VkSubmitInfo submit = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cmd};
                struct timespec t0, t1;
                clock_gettime(CLOCK_MONOTONIC, &t0);
                CHECK(vkQueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
                CHECK(vkQueueWaitIdle(queue));
                clock_gettime(CLOCK_MONOTONIC, &t1);
                const double ms = (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;

                const uint64_t* olds = out.map;
                const uint32_t* stats = stat.map;
                int bad = 0;
                char detail[160] = "";
                if (op == OP_CONTROL) {
                    // Negative control: dword-wise stores must be seen torn on the contended word counts.
                    bad = slotCount <= 64 && stats[0] == 0;
                    snprintf(detail, sizeof(detail), "%u torn words seen in %u plain loads against dword-wise stores (%s)", stats[0], stats[1], slotCount <= 64 ? "must be > 0" : "informational");
                } else if (op == OP_LOADSTORE || op == OP_PLAIN) {
                    const uint32_t rounds = op == OP_PLAIN ? 64u : 16u;
                    uint32_t torn = stats[0];
                    for (uint32_t s = 0; s < slotCount; s++)
                        if ((uint32_t)(slotWords[s] >> 32) != ~(uint32_t)slotWords[s]) torn++;
                    bad = torn != 0 || stats[1] != (n / 2) * rounds;
                    snprintf(detail, sizeof(detail), "%u torn words in %u %s loads against %u stores", torn, stats[1], op == OP_PLAIN ? "plain" : "atomic", (n / 2) * rounds);
                } else {
                    for (uint32_t s = 0; s < slotCount; s++) expected[s] = initial(op);
                    for (uint32_t i = 0; i < n; i++) {
                        const uint32_t s = slotOf(i, slotCount);
                        expected[s] = apply(op, expected[s], operand(op, i));
                    }
                    uint32_t wrongFinal = 0, chainBreaks = 0;
                    uint64_t retries = 0;
                    if (orderFree(op))
                        for (uint32_t s = 0; s < slotCount; s++) wrongFinal += slotWords[s] != expected[s];
                    if (op == OP_CASLOOP) {
                        for (uint32_t i = 0; i < n; i++) {
                            if (olds[i] == ~0ull) wrongFinal++;   // gave up after 65536 attempts
                            else retries += olds[i];
                        }
                    } else {
                        // {returned} + {final} must equal {op(returned, operand)} + {initial}, per slot.
                        for (uint32_t i = 0; i < n; i++) {
                            const uint32_t s = slotOf(i, slotCount);
                            got[i] = (Entry){s, olds[i]};
                            want[i] = (Entry){s, apply(op, olds[i], operand(op, i))};
                        }
                        for (uint32_t s = 0; s < slotCount; s++) {
                            got[n + s] = (Entry){s, slotWords[s]};
                            want[n + s] = (Entry){s, initial(op)};
                        }
                        qsort(got, n + slotCount, sizeof(Entry), compareEntries);
                        qsort(want, n + slotCount, sizeof(Entry), compareEntries);
                        for (uint32_t i = 0; i < n + slotCount; i++) chainBreaks += got[i].slot != want[i].slot || got[i].value != want[i].value;
                    }
                    bad = wrongFinal != 0 || chainBreaks != 0;
                    if (op == OP_CASLOOP) snprintf(detail, sizeof(detail), "%u wrong sums, %llu failed compare-exchanges retried", wrongFinal, (unsigned long long)retries);
                    else if (orderFree(op)) snprintf(detail, sizeof(detail), "%u wrong final words, %u chain breaks", wrongFinal, chainBreaks);
                    else snprintf(detail, sizeof(detail), "%u chain breaks", chainBreaks);
                }
                failures += bad;
                printf("  %s %-9s %-7s %7u invocations on %4u words, %7.1f ms: %s\n", bad ? "FAIL" : "ok  ", opNames[op], bda ? "bda" : "binding", n, slotCount, ms, detail);
                fflush(stdout);
            }
            vkDestroyPipeline(dev, pipeline, NULL);
        }
    }
    printf("%s: %d failing cases of %d\n", failures ? "FAIL" : "PASS", failures, cases);
    return failures ? 1 : 0;
}
