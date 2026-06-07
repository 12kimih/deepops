-- job_submit.lua -- Slurm submit-time site policy: default partition routing by
-- GPU type. Deployed only when slurm.conf has JobSubmitPlugins=lua
-- (set slurm_job_submit_plugins: "lua"). All site config comes from Ansible vars
-- (config/group_vars/slurm-cluster.yml); with the defaults (empty) this plugin is
-- a safe no-op. Runs inside slurmctld holding locks -- keep it pure string
-- parsing, no I/O.
-- https://slurm.schedmd.com/archive/slurm-25.11.6/job_submit_plugins.html

-- [1] Site configuration (templated from Ansible vars)
local CPU_PARTITIONS        = "{{ slurm_job_submit_cpu_partitions | default('') }}"
local DEFAULT_GPU_TYPE      = "{{ slurm_default_gpu_type | default('') }}"
local DEFAULT_GPU_PARTITION = "{{ slurm_default_gpu_partition | default('') }}"
local GPU_TYPE_TO_PARTITION = {
{% for gtype, part in (slurm_gpu_type_partition_map | default({})).items() %}
    ["{{ gtype }}"] = "{{ part }}",
{% endfor %}
}

-- [2] Detect a GPU request from --gres=gpu[:type][:count] and the modern
-- --gpus / --gpus-per-node (tres_per_job / tres_per_node = "gres/gpu[:type]=count").
local function detect_gpu(job_desc)
    local sources = { job_desc.gres, job_desc.tres_per_node, job_desc.tres_per_job }
    local want, gtype, count = false, nil, 0
    for _, s in ipairs(sources) do
        if s ~= nil and s ~= "" then
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
        -- Unspecified/unknown GPU type: route to the default partition and pin the
        -- default type. Write a BARE gpu:<type>:<count> gres (NOT gres/gpu:... which
        -- is the TRES-billing name and is invalid in a --gres string).
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
