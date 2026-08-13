-- job_submit.lua -- EXAMPLE Slurm submit-time site policy (default partition
-- routing by GPU type, and GPU-type stamping on every GPU request). DeepOps does
-- NOT generate this file -- copy it into your config/ (config/files/slurm/job_submit.lua),
-- EDIT the [1] block for your cluster, then enable it with:
--     slurm_job_submit_plugins: "lua"
--     slurm_job_submit_template: "{{ inventory_dir }}/files/slurm/job_submit.lua"
-- It is copied verbatim and runs inside slurmctld holding locks -- keep it pure
-- string parsing, no I/O. https://slurm.schedmd.com/job_submit_plugins.html
--
-- WHY THE TYPE STAMPING MATTERS -- do not "optimise" it away.
-- QoS/association GrpTRES limits written against a typed TRES (gres/gpu:a100=4) are
-- checked against the job's REQUESTED TRES. An untyped "--gres=gpu:4" requests zero
-- of gres/gpu:a100, so the check reads "in use 4 + requested 0 > 4" = false and the
-- job starts no matter how full the QoS already is. The type only appears afterwards,
-- in AllocTRES, far too late to deny anything. So every GPU request must carry its
-- type BEFORE it reaches the limit check -- including requests that named their own
-- partition, and including requests arriving later via "scontrol update".
-- Belt and braces: also give each QoS an untyped gres/gpu cap, so a regression here
-- cannot silently reopen the hole.

-- [1] Site configuration -- EDIT THESE for your cluster (or set "" to disable a rule).
local CPU_PARTITIONS        = "cpu"     -- partition(s) for CPU-only jobs ("" = leave unset)
local DEFAULT_GPU_TYPE      = "b200"    -- GPU type assumed when a GPU job omits the type
local DEFAULT_GPU_PARTITION = "b200"    -- partition for GPU jobs of unknown/default type
local GPU_TYPE_TO_PARTITION = {         -- map each GPU type to its partition (add your own)
    ["b200"] = "b200",
    -- Several partitions may hold one type (short/long/debug queues). List them all:
    -- the FIRST is where type-routed jobs are sent, the rest are only recognised on
    -- input. Without this, an untyped job naming "h100-short" could not be typed.
    ["h100"] = { "h100", "h100-short" },
    ["h200"] = "h200",
}
-- What to do when a GPU job omits the type AND its partition cannot pin one down --
-- because the partition holds several GPU types, or holds none, or is not listed above.
--   true  -- reject at submit, with a message telling the user to name the type.
--   false -- let it through untyped.
-- Keep this true if ANY QoS or association carries a typed GPU limit (gres/gpu:<type>).
-- Slurm checks those limits against the REQUESTED TRES, so an untyped request is
-- invisible to them and starts no matter how full the QoS is (see the header).
local STRICT_GPU_TYPE = true

-- Derived once at load, from the [1] block above.
--   GPU_TYPE_PARTITION  type      -> the partition to route that type to
--   PARTITION_TO_GPU_TYPE partition -> the single GPU type it holds
local GPU_TYPE_PARTITION, PARTITION_TO_GPU_TYPE = {}, {}
for gtype, parts in pairs(GPU_TYPE_TO_PARTITION) do
    if type(parts) == "table" then
        GPU_TYPE_PARTITION[gtype] = parts[1]
        for _, part in ipairs(parts) do PARTITION_TO_GPU_TYPE[part] = gtype end
    else
        GPU_TYPE_PARTITION[gtype] = parts
        PARTITION_TO_GPU_TYPE[parts] = gtype
    end
end

local function sorted_keys(t)
    local keys = {}
    for k in pairs(t) do keys[#keys + 1] = k end
    table.sort(keys)
    return table.concat(keys, ", ")
end

-- [2] Detect a GPU request. GPUs can be asked for five ways, each landing in a
-- different job_desc field (job_submit plugin API):
--   --gres            -> gres            (per node,  "gpu[:type]:count")
--   --gpus / -G       -> tres_per_job    (job total, "gres/gpu[:type]=count")
--   --gpus-per-node   -> tres_per_node   (per node)
--   --gpus-per-task   -> tres_per_task   (per task)   [also --tres-per-task=gres/gpu...]
--   --gpus-per-socket -> tres_per_socket (per socket)
-- We scan all five so a GPU job is never mistaken for a CPU job, and so no field is
-- left untyped. "multi" flags a request naming two different GPU types (e.g.
-- --gpus-per-node=a100:1,h100:1), which no single partition can satisfy when each
-- partition holds one GPU type.
local GPU_REQUEST_FIELDS = { "gres", "tres_per_node", "tres_per_job",
                             "tres_per_task", "tres_per_socket" }

-- GPU type names come from gres.conf Type= -- NVIDIA autodetect emits things like
-- "a100_80gb", and hand-written configs use hyphens and dots too.
local GPU_TYPE_CHARS = "[%w_%-%.]"

-- Match "gpu" only as a whole gres name (gpu:, gres/gpu=, or bare gpu), never as a
-- substring of some other name like a "mygpu" license/gres.
local function is_gpu_token(token)
    return string.find(token, "%f[%a]gpu%f[%A]") ~= nil
end

-- The type named by a GPU token, or nil when the token is untyped. "gpu:4" has to read
-- as untyped rather than as a type literally called "4".
local function gpu_type_of_token(token)
    local t = string.match(token, "gpu:(" .. GPU_TYPE_CHARS .. "+)[:=]%d+$")
        or string.match(token, "gpu:(" .. GPU_TYPE_CHARS .. "+)$")
    if t == nil or string.match(t, "^%d+$") then
        return nil
    end
    return t
end

-- Count carried by a GPU token; a bare "gpu"/"gres/gpu" means one.
local function gpu_count_of_token(token)
    return tonumber(string.match(token, "[:=](%d+)$") or "1")
end

local function detect_gpu(job_desc)
    local want, gtype, count, multi = false, nil, 0, false
    for _, key in ipairs(GPU_REQUEST_FIELDS) do
        local s = job_desc[key]
        if s ~= nil and s ~= "" then
            for token in string.gmatch(s, "[^,]+") do
                if is_gpu_token(token) then
                    want = true
                    local c = gpu_count_of_token(token)
                    if c > count then count = c end
                    local t = gpu_type_of_token(token)
                    if t ~= nil then
                        if gtype ~= nil and gtype ~= t then multi = true end
                        gtype = t
                    end
                end
            end
        end
    end
    return want, gtype, count, multi
end

-- [2b] Stamp <gtype> onto every untyped GPU token, in every request field. Each token
-- keeps its OWN count -- one job can carry --gres=gpu:4 and --gpus-per-task=1 at once,
-- and collapsing both to a single number would silently change what was asked for.
-- Two spellings exist and both must be handled:
--   gres        "gpu:4"      -> "gpu:<type>:4"       (bare gres name; "gres/gpu" is invalid here)
--   tres_per_*  "gres/gpu=4" -> "gres/gpu:<type>=4"  (TRES-billing name)
-- Tokens that already name a type, and non-GPU tokens, pass through untouched.
local function stamp_gpu_type(job_desc, gtype)
    for _, key in ipairs(GPU_REQUEST_FIELDS) do
        local s = job_desc[key]
        if s ~= nil and s ~= "" then
            local rebuilt, changed = {}, false
            for token in string.gmatch(s, "[^,]+") do
                if is_gpu_token(token) and gpu_type_of_token(token) == nil then
                    local n = tostring(gpu_count_of_token(token))
                    if string.find(token, "gres/gpu") then
                        rebuilt[#rebuilt + 1] = "gres/gpu:" .. gtype .. "=" .. n
                    else
                        rebuilt[#rebuilt + 1] = "gpu:" .. gtype .. ":" .. n
                    end
                    changed = true
                else
                    rebuilt[#rebuilt + 1] = token
                end
            end
            if changed then
                job_desc[key] = table.concat(rebuilt, ",")
            end
        end
    end
end

-- The GPU type implied by an explicitly requested partition. nil when the request
-- names no GPU partition, or names several holding different types -- there is no
-- single right answer then, and guessing would defeat the typed limits.
local function gpu_type_of_partition(partition)
    local found = nil
    for name in string.gmatch(partition, "[^,]+") do
        local t = PARTITION_TO_GPU_TYPE[name]
        if t ~= nil then
            if found ~= nil and found ~= t then return nil end
            found = t
        end
    end
    return found
end

-- Untyped request whose partition pins no single GPU type. Under STRICT_GPU_TYPE this
-- is a submit-time error; otherwise the request is left untyped and merely noted.
local function handle_untypeable(partition, count)
    if not STRICT_GPU_TYPE then
        slurm.log_user("Note: GPU type unspecified and partition '%s' does not pin one; " ..
                       "leaving the request untyped.", partition)
        return slurm.SUCCESS
    end
    slurm.log_user("Error: cannot infer the GPU type for partition '%s'. Name the type " ..
                   "explicitly (e.g. --gres=gpu:%s:%d), or submit to one of: %s.",
                   partition, DEFAULT_GPU_TYPE, count > 0 and count or 1,
                   sorted_keys(PARTITION_TO_GPU_TYPE))
    return slurm.ERROR
end

local function reject_unknown_type(gpu_type)
    slurm.log_user("Error: unknown GPU type '%s'. Valid types: %s.",
                   gpu_type, sorted_keys(GPU_TYPE_TO_PARTITION))
    return slurm.ERROR
end

local function reject_multi_type()
    slurm.log_user("Error: multiple GPU types requested in one job; submit separate " ..
                   "jobs (each partition here has a single GPU type).")
    return slurm.ERROR
end

-- [3] Submit hook
function slurm_job_submit(job_desc, part_list, submit_uid)
    local has_partition = job_desc.partition ~= nil and job_desc.partition ~= ""
    local want_gpu, gpu_type, gpu_count, gpu_multi = detect_gpu(job_desc)

    if not want_gpu then
        -- Respect an explicit --partition; only set a default when none was given.
        if not has_partition and CPU_PARTITIONS ~= "" then
            job_desc.partition = CPU_PARTITIONS
        end
        return slurm.SUCCESS
    end

    -- Two or more distinct GPU types in one job: if each partition holds a single
    -- type, none can satisfy it. Reject rather than let the job pend forever.
    if gpu_multi then
        return reject_multi_type()
    end

    -- An explicitly named GPU type must be one we know. Reject an unknown type instead
    -- of silently downgrading it to the default -- that surprises --gres jobs (silent
    -- swap) and makes --gpus jobs pend forever against a partition lacking that type.
    if gpu_type ~= nil then
        local part = GPU_TYPE_PARTITION[gpu_type]
        if part == nil then
            return reject_unknown_type(gpu_type)
        end
        if not has_partition then
            job_desc.partition = part
        end
        return slurm.SUCCESS
    end

    -- No GPU type given. Every path from here must end with the type stamped on, the
    -- explicit-partition path included: an untyped request is invisible to the typed
    -- GrpTRES limits (see the header). Returning early here is exactly the bug this
    -- structure exists to prevent.
    if has_partition then
        local implied = gpu_type_of_partition(job_desc.partition)
        if implied == nil then
            return handle_untypeable(job_desc.partition, gpu_count)
        end
        stamp_gpu_type(job_desc, implied)
        return slurm.SUCCESS
    end

    if DEFAULT_GPU_PARTITION ~= "" and DEFAULT_GPU_TYPE ~= "" then
        job_desc.partition = DEFAULT_GPU_PARTITION
        stamp_gpu_type(job_desc, DEFAULT_GPU_TYPE)
        slurm.log_user("Note: GPU type unspecified; defaulting to %s. Use --gres=gpu:<type>:N to be explicit.",
                       DEFAULT_GPU_TYPE)
    end
    return slurm.SUCCESS
end

-- [4] Modify hook (scontrol update). A pending job's GPU request can be rewritten
-- after submission, so the same normalisation has to run here -- otherwise
-- "scontrol update job=N TresPerNode=gres/gpu:4" strips the type back off and walks
-- straight past the limits the submit hook just enforced. Partition is left alone:
-- this hook only ensures whatever GPU request survives is typed.
function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
    local want_gpu, gpu_type, gpu_count, gpu_multi = detect_gpu(job_desc)
    if not want_gpu then
        return slurm.SUCCESS
    end
    if gpu_multi then
        return reject_multi_type()
    end
    if gpu_type ~= nil then
        if GPU_TYPE_PARTITION[gpu_type] == nil then
            return reject_unknown_type(gpu_type)
        end
        return slurm.SUCCESS
    end

    -- Fall back to the job's current partition when the update does not change it.
    local partition = job_desc.partition
    if partition == nil or partition == "" then
        partition = job_rec ~= nil and job_rec.partition or nil
    end
    if partition == nil or partition == "" then
        return handle_untypeable("(unset)", gpu_count)
    end
    local implied = gpu_type_of_partition(partition)
    if implied == nil then
        return handle_untypeable(partition, gpu_count)
    end
    stamp_gpu_type(job_desc, implied)
    return slurm.SUCCESS
end
