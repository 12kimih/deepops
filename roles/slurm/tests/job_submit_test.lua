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
    local body, n = PLUGIN:gsub("%-%- %[1%] Site configuration.-local STRICT_GPU_TYPE = %a+",
                                function() return site_block end, 1)
    assert(n == 1, "could not locate the [1] site block in " .. plugin_path)
    LOG = {}
    slurm = {
        SUCCESS = 0,
        ERROR = -1,
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
local function submit(d) LOG = {} return slurm_job_submit(d, {}, 1000) end
local function modify(d, rec) LOG = {} return slurm_job_modify(d, rec or {}, {}, 1000) end

-- Two GPU types, one partition each: the most common shape, and the one the
-- reported quota bypass happened on.
local TWO_TYPES = [[
local CPU_PARTITIONS        = "cpu,gpu-l40,gpu-pro6000"
local DEFAULT_GPU_TYPE      = "l40"
local DEFAULT_GPU_PARTITION = "gpu-l40"
local GPU_TYPE_TO_PARTITION = { ["l40"] = "gpu-l40", ["pro6000"] = "gpu-pro6000" }
local STRICT_GPU_TYPE = true]]

local t

print("== A. quota bypass: explicit partition + untyped GPU request ==")
with_site(TWO_TYPES)
t = {partition = "gpu-l40", gres = "gpu:4"}
check("A1 --partition + --gres=gpu:N", submit(t), slurm.SUCCESS)
check("A1 type stamped on", t.gres, "gpu:l40:4")
check("A1 partition respected", t.partition, "gpu-l40")
t = {partition = "gpu-pro6000", gres = "gpu:2"}
check("A2 other type's partition", submit(t), slurm.SUCCESS)
check("A2 stamped with that type", t.gres, "gpu:pro6000:2")
t = {partition = "gpu-l40", tres_per_node = "gres/gpu=4"}
check("A3 --gpus-per-node", submit(t), slurm.SUCCESS)
check("A3 stamped", t.tres_per_node, "gres/gpu:l40=4")
t = {partition = "gpu-l40", tres_per_job = "gres/gpu=8"}
check("A4 --gpus / -G", submit(t), slurm.SUCCESS)
check("A4 stamped", t.tres_per_job, "gres/gpu:l40=8")
t = {partition = "gpu-pro6000", tres_per_task = "gres/gpu=1"}
check("A5 --gpus-per-task", submit(t), slurm.SUCCESS)
check("A5 stamped", t.tres_per_task, "gres/gpu:pro6000=1")
t = {partition = "gpu-l40", tres_per_socket = "gres/gpu=2"}
check("A6 --gpus-per-socket", submit(t), slurm.SUCCESS)
check("A6 stamped", t.tres_per_socket, "gres/gpu:l40=2")

print("== B. default routing when no partition was given ==")
t = {gres = "gpu:4"}
check("B1 untyped gres", submit(t), slurm.SUCCESS)
check("B1 default partition", t.partition, "gpu-l40")
check("B1 default type stamped", t.gres, "gpu:l40:4")
t = {tres_per_job = "gres/gpu=4"}
check("B2 untyped --gpus", submit(t), slurm.SUCCESS)
check("B2 default partition", t.partition, "gpu-l40")
check("B2 stamped", t.tres_per_job, "gres/gpu:l40=4")
t = {gres = "gpu:pro6000:2"}
check("B3 typed request routes by type", submit(t), slurm.SUCCESS)
check("B3 partition from type", t.partition, "gpu-pro6000")
check("B3 request untouched", t.gres, "gpu:pro6000:2")
t = {cpus_per_task = 8}
check("B4 CPU-only job", submit(t), slurm.SUCCESS)
check("B4 gets CPU partitions", t.partition, "cpu,gpu-l40,gpu-pro6000")
t = {partition = "cpu", cpus_per_task = 8}
check("B5 CPU-only with own partition", submit(t), slurm.SUCCESS)
check("B5 untouched", t.partition, "cpu")

print("== C. already-typed requests are never rewritten ==")
t = {partition = "gpu-l40", gres = "gpu:l40:4"}
check("C1 typed gres", submit(t), slurm.SUCCESS)
check("C1 unchanged", t.gres, "gpu:l40:4")
t = {partition = "gpu-pro6000", tres_per_node = "gres/gpu:pro6000=2"}
check("C2 typed tres_per_node", submit(t), slurm.SUCCESS)
check("C2 unchanged", t.tres_per_node, "gres/gpu:pro6000=2")

print("== D. requests that cannot be satisfied are rejected at submit ==")
t = {partition = "gpu-l40", gres = "gpu:h100:4"}
check("D1 unknown GPU type", submit(t), slurm.ERROR)
check("D1 message lists valid types", LOG[1],
      "Error: unknown GPU type 'h100'. Valid types: l40, pro6000.")
t = {gres = "gpu:l40:1", tres_per_node = "gres/gpu:pro6000=1"}
check("D2 two GPU types in one job", submit(t), slurm.ERROR)
t = {partition = "cpu,gpu-l40,gpu-pro6000", gres = "gpu:1"}
check("D3 untyped across mixed partitions", submit(t), slurm.ERROR)
t = {partition = "cpu", gres = "gpu:1"}
check("D4 GPU request on a GPU-less partition", submit(t), slurm.ERROR)

print("== E. every GPU token keeps its own count ==")
t = {partition = "gpu-l40", gres = "gpu:4", tres_per_task = "gres/gpu=1"}
check("E1 two GPU flags on one job", submit(t), slurm.SUCCESS)
check("E1 gres count preserved", t.gres, "gpu:l40:4")
check("E1 per-task count preserved", t.tres_per_task, "gres/gpu:l40=1")
t = {partition = "gpu-l40", tres_per_task = "cpu=4,gres/gpu=2"}
check("E2 --tres-per-task with cpu", submit(t), slurm.SUCCESS)
check("E2 only the gpu token touched", t.tres_per_task, "cpu=4,gres/gpu:l40=2")
t = {partition = "gpu-l40", gres = "gpu:2,shard:8"}
check("E3 gres with another resource", submit(t), slurm.SUCCESS)
check("E3 other resource untouched", t.gres, "gpu:l40:2,shard:8")
t = {partition = "gpu-l40", gres = "gpu"}
check("E4 bare 'gpu' means one", submit(t), slurm.SUCCESS)
check("E4 stamped as 1", t.gres, "gpu:l40:1")

print("== F. resources whose name merely contains 'gpu' are not GPU requests ==")
t = {partition = "cpu", gres = "mygpu:2"}
check("F1 mygpu", submit(t), slurm.SUCCESS)
check("F1 untouched", t.gres, "mygpu:2")
check("F1 partition untouched", t.partition, "cpu")
t = {gres = "gpufoo:1"}
check("F2 gpufoo", submit(t), slurm.SUCCESS)
check("F2 treated as a CPU job", t.partition, "cpu,gpu-l40,gpu-pro6000")

print("== G. scontrol update cannot strip the type back off ==")
t = {tres_per_node = "gres/gpu=4"}
check("G1 update to an untyped request", modify(t, {partition = "gpu-l40"}), slurm.SUCCESS)
check("G1 re-stamped from the job's partition", t.tres_per_node, "gres/gpu:l40=4")
t = {gres = "gpu:2"}
check("G2 update on a pro6000 job", modify(t, {partition = "gpu-pro6000"}), slurm.SUCCESS)
check("G2 stamped", t.gres, "gpu:pro6000:2")
t = {partition = "gpu-pro6000", gres = "gpu:2"}
check("G3 update moves partition too", modify(t, {partition = "gpu-l40"}), slurm.SUCCESS)
check("G3 uses the new partition", t.gres, "gpu:pro6000:2")
t = {gres = "gpu:l40:4"}
check("G4 already typed", modify(t, {partition = "gpu-l40"}), slurm.SUCCESS)
check("G4 unchanged", t.gres, "gpu:l40:4")
t = {time_limit = 120}
check("G5 non-GPU update is a no-op", modify(t, {partition = "gpu-l40"}), slurm.SUCCESS)
t = {gres = "gpu:4"}
check("G6 untypeable partition", modify(t, {partition = "cpu"}), slurm.ERROR)
t = {gres = "gpu:h100:4"}
check("G7 unknown type", modify(t, {partition = "gpu-l40"}), slurm.ERROR)

print("== H. several partitions may hold the same GPU type ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "h100"
local DEFAULT_GPU_PARTITION = "h100-long"
local GPU_TYPE_TO_PARTITION = {
    ["h100"] = { "h100-long", "h100-short", "h100-debug" },
    ["a100"] = "a100",
}
local STRICT_GPU_TYPE = true]])
t = {partition = "h100-short", gres = "gpu:2"}
check("H1 untyped on a secondary partition", submit(t), slurm.SUCCESS)
check("H1 stamped from it", t.gres, "gpu:h100:2")
t = {partition = "h100-debug", tres_per_job = "gres/gpu=1"}
check("H2 third partition of the type", submit(t), slurm.SUCCESS)
check("H2 stamped", t.tres_per_job, "gres/gpu:h100=1")
t = {gres = "gpu:h100:4"}
check("H3 typed, no partition", submit(t), slurm.SUCCESS)
check("H3 routes to the first listed", t.partition, "h100-long")
t = {partition = "h100-short,h100-long", gres = "gpu:1"}
check("H4 two partitions, same type", submit(t), slurm.SUCCESS)
check("H4 resolvable, stamped", t.gres, "gpu:h100:1")
t = {partition = "h100-short,a100", gres = "gpu:1"}
check("H5 two partitions, different types", submit(t), slurm.ERROR)

print("== I. clusters without typed limits can opt out of strictness ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "v100"
local DEFAULT_GPU_PARTITION = "gpu"
local GPU_TYPE_TO_PARTITION = { ["v100"] = "gpu" }
local STRICT_GPU_TYPE = false]])
t = {partition = "mixed-gpu", gres = "gpu:2"}
check("I1 unresolvable partition allowed", submit(t), slurm.SUCCESS)
check("I1 left untyped by design", t.gres, "gpu:2")
check("I1 user is told", LOG[1] ~= nil and LOG[1]:match("^Note:") ~= nil, true)
check("I1 partition untouched", t.partition, "mixed-gpu")

print("== J. each rule can be disabled with \"\" ==")
with_site([[
local CPU_PARTITIONS        = ""
local DEFAULT_GPU_TYPE      = ""
local DEFAULT_GPU_PARTITION = ""
local GPU_TYPE_TO_PARTITION = { ["a40"] = "gpu" }
local STRICT_GPU_TYPE = true]])
t = {cpus_per_task = 4}
check("J1 CPU job with CPU_PARTITIONS off", submit(t), slurm.SUCCESS)
check("J1 no partition assigned", t.partition, nil)
t = {gres = "gpu:a40:2"}
check("J2 typed request still routes", submit(t), slurm.SUCCESS)
check("J2 partition from type", t.partition, "gpu")
t = {gres = "gpu:2"}
check("J3 untyped with no default configured", submit(t), slurm.SUCCESS)
check("J3 left alone", t.gres, "gpu:2")

print("== K. GPU type names as NVIDIA autodetect emits them ==")
with_site([[
local CPU_PARTITIONS        = "cpu"
local DEFAULT_GPU_TYPE      = "a100_80gb"
local DEFAULT_GPU_PARTITION = "a100"
local GPU_TYPE_TO_PARTITION = {
    ["a100_80gb"] = "a100", ["rtx-a6000"] = "rtx", ["gh200.1"] = "gh",
}
local STRICT_GPU_TYPE = true]])
t = {partition = "a100", gres = "gpu:8"}
check("K1 underscore type", submit(t), slurm.SUCCESS)
check("K1 stamped", t.gres, "gpu:a100_80gb:8")
t = {gres = "gpu:rtx-a6000:1"}
check("K2 hyphen type", submit(t), slurm.SUCCESS)
check("K2 routed", t.partition, "rtx")
t = {gres = "gpu:gh200.1:1"}
check("K3 dot type", submit(t), slurm.SUCCESS)
check("K3 routed", t.partition, "gh")

print(string.format("\n%s: %d passed, %d failed", plugin_path, pass, fail))
os.exit(fail == 0 and 0 or 1)
