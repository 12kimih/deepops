-- Regression tests for the job_submit.lua site policy.
--
--   lua roles/slurm/tests/job_submit_test.lua config.example/files/slurm/job_submit.lua
--   lua roles/slurm/tests/job_submit_test.lua config/files/slurm/job_submit.lua
--
-- The plugin under test is loaded as a string with its [1] site block swapped for the
-- one each scenario declares, so these tests exercise the plugin BODY and stay valid
-- for any cluster's copy of the file. Nothing here talks to Slurm: the slurm.* table
-- is stubbed, exactly as the plugin sees it inside slurmctld.
--
-- The case that matters most is section A. A GrpTRES limit written against a typed
-- TRES (gres/gpu:<type>) is checked against the job's REQUESTED TRES, so an untyped
-- "--gres=gpu:4" requests zero of it and starts however full the QoS already is. Every
-- request must therefore leave this plugin carrying its GPU type. If section A ever
-- fails, per-type GPU quotas are silently unenforced.

local plugin_path = arg[1] or "config.example/files/slurm/job_submit.lua"
local fh = assert(io.open(plugin_path, "r"), "cannot open " .. plugin_path)
local PLUGIN = fh:read("*a")
fh:close()

local load_chunk = loadstring or load   -- 5.1 vs 5.2+
local LOG = {}

-- Replace the [1] site block, then load the result as a fresh plugin instance.
local function with_site(site_block)
    local body, n = PLUGIN:gsub("%-%- %[1%] Site configuration.-local GPU_JOBS_USE_DEFAULTS%s*=%s*%a+",
                                function() return site_block end, 1)
    assert(n == 1, "could not locate the [1] site block in " .. plugin_path)
    LOG = {}
    slurm = {
        SUCCESS = 0,
        ERROR = -1,
        NO_VAL = 4294967294,
        NO_VAL16 = 65534,
        log_error = function() end,
        log_user = function(fmt, ...)
            local ok, s = pcall(string.format, fmt, ...)
            LOG[#LOG + 1] = ok and s or fmt
        end,
    }
    assert(load_chunk(body, "job_submit"))()
end

local pass, fail = 0, 0
local function check(name, got, want)
    if got == want then
        pass = pass + 1
    else
        fail = fail + 1
        print(string.format("  FAIL %-56s got=%-26s want=%s",
                            name, tostring(got), tostring(want)))
    end
end
-- True when any message the user was shown matches pattern.
local function logged(pattern)
    for _, line in ipairs(LOG) do
        if string.find(line, pattern) then return true end
    end
    return false
end
local function submit(d) LOG = {} return slurm_job_submit(d, {}, 1000) end
local function modify(d, rec, uid) LOG = {} return slurm_job_modify(d, rec or {}, {}, uid or 1000) end

-- Two GPU types, one partition each: the most common cluster shape, and the one the
-- section A bypass applies to.
local TWO_TYPES = [[
local CPU_PARTITIONS        = "cpu,gpu-a100,gpu-h100"
local DEFAULT_GPU_TYPE      = "a100"
local GPU_TYPE_TO_PARTITION = { ["a100"] = "gpu-a100", ["h100"] = "gpu-h100" }
local STRICT_GPU_TYPE = true
local FORCE_GPU_PARTITION = false
local GPU_JOBS_USE_DEFAULTS = false]]

local t

print("== A. quota bypass: explicit partition + untyped GPU request ==")
with_site(TWO_TYPES)
t = {partition = "gpu-a100", gres = "gpu:4"}
check("A1 --partition + --gres=gpu:N", submit(t), slurm.SUCCESS)
check("A1 type stamped on", t.gres, "gpu:a100:4")
check("A1 partition respected", t.partition, "gpu-a100")
t = {partition = "gpu-h100", gres = "gpu:2"}
check("A2 other type's partition", submit(t), slurm.SUCCESS)
check("A2 stamped with that type", t.gres, "gpu:h100:2")
t = {partition = "gpu-a100", tres_per_node = "gres/gpu=4"}
check("A3 --gpus-per-node", submit(t), slurm.SUCCESS)
check("A3 stamped", t.tres_per_node, "gres/gpu:a100=4")
t = {partition = "gpu-a100", tres_per_job = "gres/gpu=8"}
check("A4 --gpus / -G", submit(t), slurm.SUCCESS)
check("A4 stamped", t.tres_per_job, "gres/gpu:a100=8")
t = {partition = "gpu-h100", tres_per_task = "gres/gpu=1"}
check("A5 --gpus-per-task", submit(t), slurm.SUCCESS)
check("A5 stamped", t.tres_per_task, "gres/gpu:h100=1")
t = {partition = "gpu-a100", tres_per_socket = "gres/gpu=2"}
check("A6 --gpus-per-socket", submit(t), slurm.SUCCESS)
check("A6 stamped", t.tres_per_socket, "gres/gpu:a100=2")

print("== B. default routing when no partition was given ==")
t = {gres = "gpu:4"}
check("B1 untyped gres", submit(t), slurm.SUCCESS)
check("B1 default partition", t.partition, "gpu-a100")
check("B1 default type stamped", t.gres, "gpu:a100:4")
t = {tres_per_job = "gres/gpu=4"}
check("B2 untyped --gpus", submit(t), slurm.SUCCESS)
check("B2 default partition", t.partition, "gpu-a100")
check("B2 stamped", t.tres_per_job, "gres/gpu:a100=4")
t = {gres = "gpu:h100:2"}
check("B3 typed request routes by type", submit(t), slurm.SUCCESS)
check("B3 partition from type", t.partition, "gpu-h100")
check("B3 request untouched", t.gres, "gpu:h100:2")
t = {cpus_per_task = 8}
check("B4 CPU-only job", submit(t), slurm.SUCCESS)
check("B4 gets CPU partitions", t.partition, "cpu,gpu-a100,gpu-h100")
t = {partition = "cpu", cpus_per_task = 8}
check("B5 CPU-only with own partition", submit(t), slurm.SUCCESS)
check("B5 untouched", t.partition, "cpu")

print("== C. already-typed requests are never rewritten ==")
t = {partition = "gpu-a100", gres = "gpu:a100:4"}
check("C1 typed gres", submit(t), slurm.SUCCESS)
check("C1 unchanged", t.gres, "gpu:a100:4")
t = {partition = "gpu-h100", tres_per_node = "gres/gpu:h100=2"}
check("C2 typed tres_per_node", submit(t), slurm.SUCCESS)
check("C2 unchanged", t.tres_per_node, "gres/gpu:h100=2")

print("== D. requests that cannot be satisfied are rejected at submit ==")
t = {partition = "gpu-a100", gres = "gpu:v100:4"}
check("D1 unknown GPU type", submit(t), slurm.ERROR)
check("D1 message lists valid types", LOG[1],
      "Unknown GPU type 'v100'. Valid types: a100, h100.")
t = {gres = "gpu:a100:1", tres_per_node = "gres/gpu:h100=1"}
check("D2 two GPU types in one job", submit(t), slurm.ERROR)
t = {partition = "cpu,gpu-a100,gpu-h100", gres = "gpu:1"}
check("D3 untyped across mixed partitions", submit(t), slurm.ERROR)
t = {partition = "cpu", gres = "gpu:1"}
check("D4 GPU request on a GPU-less partition", submit(t), slurm.ERROR)

print("== E. every GPU token keeps its own count ==")
t = {partition = "gpu-a100", gres = "gpu:4", tres_per_task = "gres/gpu=1"}
check("E1 two GPU flags on one job", submit(t), slurm.SUCCESS)
check("E1 gres count preserved", t.gres, "gpu:a100:4")
check("E1 per-task count preserved", t.tres_per_task, "gres/gpu:a100=1")
t = {partition = "gpu-a100", tres_per_task = "cpu=4,gres/gpu=2"}
check("E2 --tres-per-task with cpu", submit(t), slurm.SUCCESS)
check("E2 only the gpu token touched", t.tres_per_task, "cpu=4,gres/gpu:a100=2")
t = {partition = "gpu-a100", gres = "gpu:2,shard:8"}
check("E3 gres with another resource", submit(t), slurm.SUCCESS)
check("E3 other resource untouched", t.gres, "gpu:a100:2,shard:8")
t = {partition = "gpu-a100", gres = "gpu"}
check("E4 bare 'gpu' means one", submit(t), slurm.SUCCESS)
check("E4 stamped as 1", t.gres, "gpu:a100:1")

print("== F. resources whose name merely contains 'gpu' are not GPU requests ==")
t = {partition = "cpu", gres = "mygpu:2"}
check("F1 mygpu", submit(t), slurm.SUCCESS)
check("F1 untouched", t.gres, "mygpu:2")
check("F1 partition untouched", t.partition, "cpu")
t = {gres = "gpufoo:1"}
check("F2 gpufoo", submit(t), slurm.SUCCESS)
check("F2 treated as a CPU job", t.partition, "cpu,gpu-a100,gpu-h100")

print("== G. scontrol update cannot strip the type back off ==")
t = {tres_per_node = "gres/gpu=4"}
check("G1 update to an untyped request", modify(t, {partition = "gpu-a100"}), slurm.SUCCESS)
check("G1 re-stamped from the job's partition", t.tres_per_node, "gres/gpu:a100=4")
t = {gres = "gpu:2"}
check("G2 update on an h100 job", modify(t, {partition = "gpu-h100"}), slurm.SUCCESS)
check("G2 stamped", t.gres, "gpu:h100:2")
t = {partition = "gpu-h100", gres = "gpu:2"}
check("G3 update moves partition too", modify(t, {partition = "gpu-a100"}), slurm.SUCCESS)
check("G3 uses the new partition", t.gres, "gpu:h100:2")
t = {gres = "gpu:a100:4"}
check("G4 already typed", modify(t, {partition = "gpu-a100"}), slurm.SUCCESS)
check("G4 unchanged", t.gres, "gpu:a100:4")
t = {time_limit = 120}
check("G5 non-GPU update is a no-op", modify(t, {partition = "gpu-a100"}), slurm.SUCCESS)
t = {gres = "gpu:4"}
check("G6 untypeable partition", modify(t, {partition = "cpu"}), slurm.ERROR)
t = {gres = "gpu:v100:4"}
check("G7 unknown type", modify(t, {partition = "gpu-a100"}), slurm.ERROR)

print("== H. several partitions may hold the same GPU type ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "h100"
local GPU_TYPE_TO_PARTITION = {
    ["h100"] = { "h100-long", "h100-short", "h100-debug" },
    ["a100"] = "a100",
}
local STRICT_GPU_TYPE = true
local FORCE_GPU_PARTITION = false
local GPU_JOBS_USE_DEFAULTS = false]])
t = {partition = "h100-short", gres = "gpu:2"}
check("H1 untyped on a secondary partition", submit(t), slurm.SUCCESS)
check("H1 stamped from it", t.gres, "gpu:h100:2")
t = {partition = "h100-debug", tres_per_job = "gres/gpu=1"}
check("H2 third partition of the type", submit(t), slurm.SUCCESS)
check("H2 stamped", t.tres_per_job, "gres/gpu:h100=1")
t = {gres = "gpu:h100:4"}
check("H3 typed, no partition", submit(t), slurm.SUCCESS)
check("H3 routes to every partition of the type", t.partition,
      "h100-long,h100-short,h100-debug")
t = {partition = "h100-short,h100-long", gres = "gpu:1"}
check("H4 two partitions, same type", submit(t), slurm.SUCCESS)
check("H4 resolvable, stamped", t.gres, "gpu:h100:1")
t = {partition = "h100-short,a100", gres = "gpu:1"}
check("H5 two partitions, different types", submit(t), slurm.ERROR)

print("== I. clusters without typed limits can opt out of strictness ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "v100"
local GPU_TYPE_TO_PARTITION = { ["v100"] = "gpu" }
local STRICT_GPU_TYPE = false
local FORCE_GPU_PARTITION = false
local GPU_JOBS_USE_DEFAULTS = false]])
t = {partition = "mixed-gpu", gres = "gpu:2"}
check("I1 unresolvable partition allowed", submit(t), slurm.SUCCESS)
check("I1 left untyped by design", t.gres, "gpu:2")
check("I1 user is told", logged("^Note:"), true)
check("I1 partition untouched", t.partition, "mixed-gpu")

print("== J. each rule can be disabled with \"\" ==")
with_site([[
local CPU_PARTITIONS        = ""
local DEFAULT_GPU_TYPE      = ""
local GPU_TYPE_TO_PARTITION = { ["a40"] = "gpu" }
local STRICT_GPU_TYPE = true
local FORCE_GPU_PARTITION = false
local GPU_JOBS_USE_DEFAULTS = false]])
t = {cpus_per_task = 4}
check("J1 CPU job with CPU_PARTITIONS off", submit(t), slurm.SUCCESS)
check("J1 no partition assigned", t.partition, nil)
t = {gres = "gpu:a40:2"}
check("J2 typed request still routes", submit(t), slurm.SUCCESS)
check("J2 partition from type", t.partition, "gpu")
t = {gres = "gpu:2"}
check("J3 untyped, no default, strict: rejected", submit(t), slurm.ERROR)
check("J3 left untyped", t.gres, "gpu:2")

print("== K. GPU type names as NVIDIA autodetect emits them ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "a100_80gb"
local GPU_TYPE_TO_PARTITION = {
    ["a100_80gb"] = "a100", ["rtx-a6000"] = "rtx", ["gh200.1"] = "gh",
}
local STRICT_GPU_TYPE = true
local FORCE_GPU_PARTITION = false
local GPU_JOBS_USE_DEFAULTS = false]])
t = {partition = "a100", gres = "gpu:8"}
check("K1 underscore type", submit(t), slurm.SUCCESS)
check("K1 stamped", t.gres, "gpu:a100_80gb:8")
t = {gres = "gpu:rtx-a6000:1"}
check("K2 hyphen type", submit(t), slurm.SUCCESS)
check("K2 routed", t.partition, "rtx")
t = {gres = "gpu:gh200.1:1"}
check("K3 dot type", submit(t), slurm.SUCCESS)
check("K3 routed", t.partition, "gh")

-- The shape this policy was written for: one GPU type split over two partitions only
-- for their different per-node defaults, routed by type and sized by those defaults.
local SPLIT_POOL = [[
local CPU_PARTITIONS        = "l40-1,l40-2,pro6000-1"
local GPU_TYPE_TO_PARTITION = {
    ["l40"]     = { "l40-1", "l40-2" },
    ["pro6000"] = "pro6000-1",
}
local DEFAULT_GPU_TYPE      = "l40"
local STRICT_GPU_TYPE       = true
local FORCE_GPU_PARTITION   = true
local GPU_JOBS_USE_DEFAULTS = true]]
local NO_VAL, NO_VAL16 = 4294967294, 65534
local L40 = "l40-1,l40-2"

print("== L. FORCE_GPU_PARTITION: a GPU job runs on every partition of its type ==")
with_site(SPLIT_POOL)
t = {partition = "l40-1", gres = "gres/gpu:l40:4"}
check("L1 typed job pinned to one partition", submit(t), slurm.SUCCESS)
check("L1 widened to the whole type", t.partition, L40)
check("L1 user is told", logged("ignored partition 'l40%-1'"), true)
t = {partition = "l40-2", gres = "gpu:2"}
check("L2 untyped on one partition", submit(t), slurm.SUCCESS)
check("L2 typed from it", t.gres, "gpu:l40:2")
check("L2 widened", t.partition, L40)
t = {partition = "pro6000-1", gres = "gpu:l40:1"}
check("L3 type and partition disagree", submit(t), slurm.SUCCESS)
check("L3 the type wins", t.partition, L40)
t = {partition = "nosuch", gres = "gpu:1"}
check("L4 untyped on a GPU-less partition", submit(t), slurm.SUCCESS)
check("L4 falls back to the default type", t.gres, "gpu:l40:1")
check("L4 routed by it", t.partition, L40)
t = {partition = L40, gres = "gpu:pro6000:1"}
check("L5 typed, named the wrong type's partitions", submit(t), slurm.SUCCESS)
check("L5 rerouted", t.partition, "pro6000-1")
t = {partition = L40, gres = "gpu:l40:1"}
check("L6 already the full list", submit(t), slurm.SUCCESS)
check("L6 nothing to report", #LOG, 0)
t = {partition = "l40-1", cpus_per_task = 8}
check("L7 CPU job keeps its partition", submit(t), slurm.SUCCESS)
check("L7 untouched", t.partition, "l40-1")

print("== M. GPU_JOBS_USE_DEFAULTS at submit ==")
-- The script behind the incident: pinned partition, its own CPUs and memory.
t = {partition = "l40-1", gres = "gres/gpu:l40:4", cpus_per_task = 16,
     tres_per_task = "cpu=16", min_cpus = 16, min_mem_per_node = 131072,
     bitflags = 32768}
check("M1 --mem on a GPU job", submit(t), slurm.ERROR)
check("M1 tells the user why", logged("^GPU jobs take memory"), true)
check("M1 worded for a submission", logged("and submit again%.$"), true)
check("M1 nothing rewritten before rejecting", t.partition, "l40-1")
t = {partition = "l40-1", gres = "gres/gpu:l40:4", cpus_per_task = 16,
     tres_per_task = "cpu=16", min_cpus = 16, pn_min_cpus = 16, bitflags = 32768 + 16384}
check("M2 same job without --mem", submit(t), slurm.SUCCESS)
check("M2 -c dropped", t.cpus_per_task, NO_VAL16)
check("M2 -c dropped from tres_per_task", t.tres_per_task, "")
check("M2 min_cpus left to Slurm", t.min_cpus, NO_VAL)
check("M2 JOB_CPUS_SET cleared, others kept", t.bitflags, 16384)
check("M2 routed", t.partition, L40)
check("M2 user is told", logged("ignored %-%-cpus%-per%-task%.$"), true)
t = {gres = "gpu:l40:1", min_mem_per_cpu = 8000}
check("M3 --mem-per-cpu", submit(t), slurm.ERROR)
t = {gres = "gpu:l40:1", mem_per_tres = "gres/gpu:65536"}
check("M4 --mem-per-gpu", submit(t), slurm.ERROR)
t = {gres = "gpu:l40:1", min_mem_per_node = 0}
check("M5 --mem=0 (whole node)", submit(t), slurm.ERROR)
t = {gres = "gpu:l40:2", cpus_per_tres = "gres/gpu:12"}
check("M6 --cpus-per-gpu", submit(t), slurm.SUCCESS)
check("M6 dropped", t.cpus_per_tres, "")
t = {gres = "gpu:l40:1", shared = 0}
check("M7 --exclusive", submit(t), slurm.SUCCESS)
check("M7 dropped", t.shared, NO_VAL16)
t = {gres = "gpu:l40:1", pn_min_cpus = 40, max_cpus = 40}
check("M8 --mincpus", submit(t), slurm.SUCCESS)
check("M8 user is told", logged("ignored %-%-mincpus%.$"), true)
check("M8 dropped", t.pn_min_cpus, NO_VAL16)
check("M8 max_cpus dropped", t.max_cpus, NO_VAL)
t = {tres_per_task = "cpu=8,gres/gpu=1", num_tasks = 4}
check("M9 --tres-per-task=cpu=8,gres/gpu=1", submit(t), slurm.SUCCESS)
check("M9 GPU token typed, cpu token gone", t.tres_per_task, "gres/gpu:l40=1")
-- What sbatch sends for options left unset: the NO_VAL family, not nil.
t = {gres = "gres/gpu:l40:4", cpus_per_task = NO_VAL16, pn_min_cpus = NO_VAL16,
     shared = NO_VAL16, num_tasks = NO_VAL, ntasks_per_node = NO_VAL16,
     ntasks_per_tres = NO_VAL16, ntasks_per_socket = NO_VAL16, max_cpus = NO_VAL,
     min_nodes = NO_VAL, min_cpus = 1, bitflags = 0, cpus_per_tres = nil}
check("M10 plain GPU job", submit(t), slurm.SUCCESS)
check("M10 no notes beyond routing", #LOG, 0)
check("M10 min_cpus left to Slurm", t.min_cpus, NO_VAL)
t = {partition = "l40-1", cpus_per_task = 32, min_mem_per_node = 400000, shared = 0}
check("M11 CPU job sizes itself", submit(t), slurm.SUCCESS)
check("M11 -c kept", t.cpus_per_task, 32)
check("M11 --exclusive kept", t.shared, 0)

print("== N. tasks may not outnumber GPUs ==")
t = {gres = "gpu:l40:4", num_tasks = 4}
check("N1 -n = GPUs", submit(t), slurm.SUCCESS)
t = {gres = "gpu:l40:4", num_tasks = 8}
check("N2 -n > GPUs on one node", submit(t), slurm.ERROR)
check("N2 message", LOG[1], "--ntasks=8 exceeds the 4 GPUs requested in total; GPU jobs " ..
      "run at most one task per GPU. Request more GPUs, or more nodes with -N.")
t = {gres = "gpu:l40:4", num_tasks = 8, min_nodes = 2}
check("N3 -n 8 over -N 2", submit(t), slurm.SUCCESS)
t = {gres = "gpu:l40:4", ntasks_per_node = 4, min_nodes = 2}
check("N4 --ntasks-per-node = GPUs per node", submit(t), slurm.SUCCESS)
t = {gres = "gpu:l40:4", ntasks_per_node = 5}
check("N5 --ntasks-per-node > GPUs per node", submit(t), slurm.ERROR)
check("N5 message", LOG[1], "--ntasks-per-node=5 exceeds the 4 GPUs requested per node; " ..
      "GPU jobs run at most one task per GPU. Request more GPUs per node.")
t = {tres_per_job = "gres/gpu:l40=4", ntasks_per_node = 4}
check("N6 --gpus=4 with 4 tasks per node", submit(t), slurm.SUCCESS)
t = {tres_per_job = "gres/gpu:l40=4", num_tasks = 6}
check("N7 --gpus=4 with -n 6", submit(t), slurm.ERROR)
t = {gres = "gpu:l40:4", ntasks_per_tres = 1}
check("N8 --ntasks-per-gpu=1", submit(t), slurm.SUCCESS)
t = {gres = "gpu:l40:4", ntasks_per_tres = 2}
check("N9 --ntasks-per-gpu=2", submit(t), slurm.ERROR)
check("N9 message", LOG[1], "--ntasks-per-gpu=2 is not allowed; GPU jobs run at most " ..
      "one task per GPU.")
t = {tres_per_task = "gres/gpu:l40=1", num_tasks = 8}
check("N10 --gpus-per-task: tasks never outnumber GPUs", submit(t), slurm.SUCCESS)
t = {tres_per_socket = "gres/gpu:l40=2", ntasks_per_socket = 2}
check("N11 --gpus-per-socket with matching tasks", submit(t), slurm.SUCCESS)
t = {gres = "gpu:l40:4", ntasks_per_socket = 2}
check("N12 --ntasks-per-socket without --gpus-per-socket", submit(t), slurm.ERROR)
check("N12 message", LOG[1], "--ntasks-per-socket=2 cannot be checked against the GPU " ..
      "count per socket. Request GPUs with --gpus-per-socket.")
-- slurmctld hands Lua 5.3+ floats; messages must still print integers.
t = {gres = "gpu:l40:4", num_tasks = 8.0, min_nodes = 1.0}
check("N12b float inputs", submit(t), slurm.ERROR)
check("N12b printed as integers", logged("^%-%-ntasks=8 exceeds the 4 GPUs"), true)
t = {tres_per_socket = "gres/gpu:l40=2", num_tasks = 4}
check("N13 -n with only --gpus-per-socket: uncheckable", submit(t), slurm.ERROR)
t = {tres_per_job = "gres/gpu:l40=1", num_tasks = 2}
check("N13b singular GPU", submit(t), slurm.ERROR)
check("N13b message", LOG[1], "--ntasks=2 exceeds the 1 GPU requested in total; GPU jobs " ..
      "run at most one task per GPU. Request more GPUs, or more nodes with -N.")
t = {num_tasks = 64}
check("N14 CPU job: any task count", submit(t), slurm.SUCCESS)

print("== O. scontrol update cannot undo the policy ==")
local GPU_REC = {partition = L40, gres = "gres/gpu:l40:4", tres_per_node = "gres/gpu:l40:4"}
t = {partition = "l40-1"}
check("O1 user pins a GPU job to one partition", modify(t, GPU_REC), slurm.SUCCESS)
check("O1 widened back", t.partition, L40)
t = {partition = "l40-2"}
check("O2 root may pin it", modify(t, GPU_REC, 0), slurm.SUCCESS)
check("O2 kept", t.partition, "l40-2")
t = {min_mem_per_node = 200000}
check("O3 user sets memory", modify(t, GPU_REC), slurm.ERROR)
check("O3 worded for an update", logged("Their memory cannot be changed%.$"), true)
t = {cpus_per_task = 32}
check("O4 user sets -c", modify(t, GPU_REC), slurm.SUCCESS)
check("O4 dropped", t.cpus_per_task, NO_VAL16)
t = {min_cpus = 32}
check("O5 user sets NumCPUs", modify(t, GPU_REC), slurm.SUCCESS)
check("O5 dropped", t.min_cpus, NO_VAL)
check("O5 user is told", logged("NumCPUs"), true)
t = {num_tasks = 16}
check("O6 user changes the task count", modify(t, GPU_REC), slurm.ERROR)
t = {time_limit = 60}
check("O7 unrelated update", modify(t, GPU_REC), slurm.SUCCESS)
check("O7 nothing reported", #LOG, 0)
local CPU_REC = {partition = "l40-1"}
t = {gres = "gpu:l40:4"}
check("O8 user adds GPUs to a CPU job", modify(t, CPU_REC), slurm.ERROR)
t = {gres = "gpu:4"}
check("O9 root may, and it is still typed", modify(t, CPU_REC, 0), slurm.SUCCESS)
check("O9 stamped", t.gres, "gpu:l40:4")
t = {cpus_per_task = 64, min_mem_per_node = 300000}
check("O10 CPU job resizes freely", modify(t, CPU_REC), slurm.SUCCESS)
check("O10 kept", t.cpus_per_task, 64)
t = {gres = "gpu:2"}
check("O11 GPU count change on a GPU job", modify(t, GPU_REC), slurm.SUCCESS)
check("O11 stamped from the job's partitions", t.gres, "gpu:l40:2")

print("== P. a Lua runtime error rejects instead of failing open ==")
local broken = setmetatable({}, {__index = function() error("boom") end})
check("P1 submit", submit(broken), slurm.ERROR)
check("P1 user is told", logged("failed internally"), true)
check("P2 modify", modify(broken, GPU_REC), slurm.ERROR)

print(string.format("\n%s: %d passed, %d failed", plugin_path, pass, fail))
os.exit(fail == 0 and 0 or 1)
