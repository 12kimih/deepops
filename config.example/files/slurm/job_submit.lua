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

-- [2] Detect a GPU request from --gres=gpu[:type][:count] and the modern
-- --gpus / --gpus-per-node (tres_per_job / tres_per_node = "gres/gpu[:type]=count").
local function detect_gpu(job_desc)
    -- Build a dense list: a table literal with a nil at index 1 (e.g. gres unset,
    -- only --gpus given) stops ipairs early, so collect non-empty fields first.
    local sources = {}
    for _, key in ipairs({ "gres", "tres_per_node", "tres_per_job" }) do
        local s = job_desc[key]
        if s ~= nil and s ~= "" then
            sources[#sources + 1] = s
        end
    end
    local want, gtype, count = false, nil, 0
    for _, s in ipairs(sources) do
        for token in string.gmatch(s, "[^,]+") do
            if string.find(token, "gpu") then
                want = true
                local c = string.match(token, "[:=](%d+)$")
                count = c and tonumber(c) or (count > 0 and count or 1)
                local t = string.match(token, "gpu:([%a%d_]+)[:=]%d+$") or string.match(token, "gpu:([%a%d_]+)$")
                if t and not string.match(t, "^%d+$") then gtype = t end
            end
        end
    end
    return want, gtype, count
end

-- [3] Submit hook
function slurm_job_submit(job_desc, part_list, submit_uid)
    -- Respect an explicit --partition; only set a default when none was given.
    if job_desc.partition ~= nil and job_desc.partition ~= "" then
        return slurm.SUCCESS
    end

    local want_gpu, gpu_type, gpu_count = detect_gpu(job_desc)

    if not want_gpu then
        if CPU_PARTITIONS ~= "" then
            job_desc.partition = CPU_PARTITIONS
        end
        return slurm.SUCCESS
    end

    local part = gpu_type and GPU_TYPE_TO_PARTITION[gpu_type] or nil
    if part ~= nil then
        job_desc.partition = part
    elseif DEFAULT_GPU_PARTITION ~= "" then
        -- Unspecified/unknown GPU type: route to the default partition. For --gres
        -- requests also pin the default type by rewriting to a BARE gpu:<type>:<count>
        -- (NOT gres/gpu:... which is the TRES-billing name, invalid in --gres). For
        -- the --gpus form the partition itself constrains the type.
        job_desc.partition = DEFAULT_GPU_PARTITION
        if DEFAULT_GPU_TYPE ~= "" and job_desc.gres ~= nil and job_desc.gres ~= "" then
            local rebuilt = {}
            for token in string.gmatch(job_desc.gres, "[^,]+") do
                if string.find(token, "gpu") then
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
