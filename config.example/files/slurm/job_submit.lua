-- job_submit.lua -- EXAMPLE Slurm submit-time site policy (default partition
-- routing by GPU type). DeepOps does NOT generate this file -- copy it into your
-- config/ (config/files/slurm/job_submit.lua), EDIT the [1] block for your
-- cluster, then enable it with:
--     slurm_job_submit_plugins: "lua"
--     slurm_job_submit_template: "{{ inventory_dir }}/files/slurm/job_submit.lua"
-- It is copied verbatim and runs inside slurmctld holding locks -- keep it pure
-- string parsing, no I/O. https://slurm.schedmd.com/job_submit_plugins.html

-- [1] Site configuration -- EDIT THESE for your cluster (or set "" to disable a rule).
local CPU_PARTITIONS        = "cpu"     -- partition(s) for CPU-only jobs ("" = leave unset)
local DEFAULT_GPU_TYPE      = "b200"    -- GPU type assumed when a GPU job omits the type
local DEFAULT_GPU_PARTITION = "b200"    -- partition for GPU jobs of unknown/default type
local GPU_TYPE_TO_PARTITION = {         -- map each GPU type to its partition (add your own)
    ["b200"] = "b200",
    ["h100"] = "h100",
    ["h200"] = "h200",
}

-- [2] Detect a GPU request. GPUs can be asked for five ways, each landing in a
-- different job_desc field (job_submit plugin API):
--   --gres            -> gres            (per node,  "gpu[:type]:count")
--   --gpus / -G       -> tres_per_job    (job total, "gres/gpu[:type]=count")
--   --gpus-per-node   -> tres_per_node   (per node)
--   --gpus-per-task   -> tres_per_task   (per task)   [also --tres-per-task=gres/gpu...]
--   --gpus-per-socket -> tres_per_socket (per socket)
-- We scan all five so a GPU job is never mistaken for a CPU job. "multi" flags a
-- request naming two different GPU types (e.g. --gpus-per-node=a100:1,h100:1),
-- which no single partition can satisfy when each partition holds one GPU type.
local function detect_gpu(job_desc)
    -- Build a dense list: a table literal with a nil at index 1 (e.g. gres unset,
    -- only --gpus given) stops ipairs early, so collect non-empty fields first.
    local sources = {}
    for _, key in ipairs({ "gres", "tres_per_node", "tres_per_job",
                           "tres_per_task", "tres_per_socket" }) do
        local s = job_desc[key]
        if s ~= nil and s ~= "" then
            sources[#sources + 1] = s
        end
    end
    local want, gtype, count, multi = false, nil, 0, false
    for _, s in ipairs(sources) do
        for token in string.gmatch(s, "[^,]+") do
            -- Match "gpu" only as a whole gres name (gpu:, gres/gpu=, or bare gpu),
            -- never as a substring of some other name like a "mygpu" license/gres.
            if string.find(token, "%f[%a]gpu%f[%A]") then
                want = true
                local c = string.match(token, "[:=](%d+)$")
                count = c and tonumber(c) or (count > 0 and count or 1)
                local t = string.match(token, "gpu:([%a%d_]+)[:=]%d+$") or string.match(token, "gpu:([%a%d_]+)$")
                if t and not string.match(t, "^%d+$") then
                    if gtype ~= nil and gtype ~= t then multi = true end
                    gtype = t
                end
            end
        end
    end
    return want, gtype, count, multi
end

-- [3] Submit hook
function slurm_job_submit(job_desc, part_list, submit_uid)
    -- Respect an explicit --partition; only set a default when none was given.
    if job_desc.partition ~= nil and job_desc.partition ~= "" then
        return slurm.SUCCESS
    end

    local want_gpu, gpu_type, gpu_count, gpu_multi = detect_gpu(job_desc)

    if not want_gpu then
        if CPU_PARTITIONS ~= "" then
            job_desc.partition = CPU_PARTITIONS
        end
        return slurm.SUCCESS
    end

    -- Two or more distinct GPU types in one job: if each partition holds a single
    -- type, none can satisfy it. Reject rather than let the job pend forever.
    if gpu_multi then
        slurm.log_user("Error: multiple GPU types requested in one job; submit separate " ..
                       "jobs (each partition here has a single GPU type).")
        return slurm.ERROR
    end

    -- An explicitly named GPU type must be one we know. Reject an unknown type instead
    -- of silently downgrading it to the default -- that surprises --gres jobs (silent
    -- swap) and makes --gpus jobs pend forever against a partition lacking that type.
    if gpu_type ~= nil then
        local part = GPU_TYPE_TO_PARTITION[gpu_type]
        if part == nil then
            local valid = {}
            for t in pairs(GPU_TYPE_TO_PARTITION) do valid[#valid + 1] = t end
            table.sort(valid)
            slurm.log_user("Error: unknown GPU type '%s'. Valid types: %s.",
                           gpu_type, table.concat(valid, ", "))
            return slurm.ERROR
        end
        job_desc.partition = part
        return slurm.SUCCESS
    end

    -- No GPU type given: route to the default partition. For --gres requests also pin
    -- the default type by rewriting to a BARE gpu:<type>:<count> (NOT gres/gpu:... which
    -- is the TRES-billing name, invalid in --gres). For the --gpus/--gpus-per-* forms the
    -- partition itself constrains the type.
    if DEFAULT_GPU_PARTITION ~= "" then
        job_desc.partition = DEFAULT_GPU_PARTITION
        if DEFAULT_GPU_TYPE ~= "" and job_desc.gres ~= nil and job_desc.gres ~= "" then
            local rebuilt = {}
            for token in string.gmatch(job_desc.gres, "[^,]+") do
                if string.find(token, "%f[%a]gpu%f[%A]") then
                    table.insert(rebuilt, "gpu:" .. DEFAULT_GPU_TYPE .. ":" .. tostring(gpu_count))
                else
                    table.insert(rebuilt, token)
                end
            end
            job_desc.gres = table.concat(rebuilt, ",")
            slurm.log_user("Note: GPU type unspecified; defaulting to %s. Use --gres=gpu:<type>:N to be explicit.", DEFAULT_GPU_TYPE)
        end
    end
    return slurm.SUCCESS
end

-- [4] Modify hook (scontrol update)
function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
    return slurm.SUCCESS
end
