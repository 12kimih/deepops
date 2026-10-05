-- job_submit.lua -- EXAMPLE Slurm submit-time site policy (partition routing by GPU
-- type, GPU-type stamping on every GPU request, and default-only sizing of GPU jobs).
-- DeepOps does NOT generate this file -- copy it into your config/
-- (config/files/slurm/job_submit.lua), EDIT the [1] block for your cluster, then
-- enable it with:
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
--   CPU_PARTITIONS         partition(s) for CPU-only jobs ("" = leave unset).
--   GPU_TYPE_TO_PARTITION  each GPU type (from Gres=gpu:<type>:N) to the partition(s)
--                          holding it. Give a list where one type is split across
--                          several -- per-partition defaults, short/long queues -- and
--                          a job routed by type is offered all of them, for Slurm to
--                          start wherever it fits first.
--   DEFAULT_GPU_TYPE       type assumed when a GPU job names none and its partition
--                          implies none. It routes through the map above like any
--                          other type, so the partitions of a type are declared once.
--                          "" = no default (see STRICT_GPU_TYPE).
--   STRICT_GPU_TYPE        what to do when a GPU job's type cannot be pinned down:
--                          its partition holds several GPU types, or holds none, or is
--                          not listed here, and no default applies.
--                          true  -- reject at submit, naming the usable partitions.
--                          false -- let it through untyped.
--                          Keep this true if any QoS or association carries a typed
--                          GPU limit: Slurm checks those against the REQUESTED TRES,
--                          so an untyped request is invisible to them (see header).
--   FORCE_GPU_PARTITION    true  -- a GPU job always goes to every partition of its
--                          type, whatever partition it named. A job that must land on
--                          one node still can, with -w/--nodelist.
--                          false -- a named partition is kept.
--   GPU_JOBS_USE_DEFAULTS  true  -- GPU jobs are sized by the partition defaults
--                          (DefCpuPerGPU, DefMemPerCPU/DefMemPerGPU) only: CPU options
--                          are dropped, memory options rejected, and tasks may not
--                          outnumber GPUs. CPU-only jobs keep every option.
--                          false -- GPU jobs size themselves.
local CPU_PARTITIONS        = "cpu"
local GPU_TYPE_TO_PARTITION = {
    ["b200"] = "b200",
    ["h100"] = { "h100", "h100-short" },
    ["h200"] = "h200",
}
local DEFAULT_GPU_TYPE      = "b200"
local STRICT_GPU_TYPE       = true
local FORCE_GPU_PARTITION   = true
local GPU_JOBS_USE_DEFAULTS = true

-- Derived once at load, from the [1] block above.
--   GPU_TYPE_PARTITION    type      -> the partition list to route that type to
--   PARTITION_TO_GPU_TYPE partition -> the single GPU type it holds
-- A lone partition is normalised to a one-entry list so both forms take one code path.
local GPU_TYPE_PARTITION, PARTITION_TO_GPU_TYPE = {}, {}
for gtype, parts in pairs(GPU_TYPE_TO_PARTITION) do
    if type(parts) ~= "table" then parts = { parts } end
    GPU_TYPE_PARTITION[gtype] = table.concat(parts, ",")
    for _, part in ipairs(parts) do PARTITION_TO_GPU_TYPE[part] = gtype end
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

-- An untyped request no partition and no site default can pin a type onto. Under
-- STRICT_GPU_TYPE this is a submit-time error; otherwise it is left untyped and noted.
local function handle_untypeable(partition, count)
    local why = (partition ~= nil and partition ~= "")
        and string.format("partition '%s' does not pin one", partition)
        or "no partition was named and no default type is set"
    if not STRICT_GPU_TYPE then
        slurm.log_user("Note: GPU type unspecified and %s; leaving the request untyped.", why)
        return slurm.SUCCESS
    end
    slurm.log_user("Error: GPU type unspecified and %s. Name it explicitly " ..
                   "(e.g. --gres=gpu:<type>:%d, types: %s), or submit to one of: %s.",
                   why, count > 0 and count or 1, sorted_keys(GPU_TYPE_TO_PARTITION),
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

-- [2c] Default-only sizing of GPU jobs (GPU_JOBS_USE_DEFAULTS). Slurm applies
-- DefCpuPerGPU only while the job sets no --cpus-per-task/--cpus-per-gpu, and
-- DefMemPerCPU/DefMemPerGPU only while it sets no memory, re-deriving both for each
-- partition it is tried in. So the policy is "leave those fields unset":
--   CPU    -- reset to unset. Every CPU field is 16/32-bit, so its NO_VAL fits a
--             Lua number exactly.
--   memory -- rejected, never reset. Unset memory is NO_VAL64, which a Lua double
--             cannot hold: it rounds to 2^64 and reaches slurmctld as 0, i.e.
--             --mem=0, the whole node.
--   tasks  -- each task takes at least one CPU, so -n above the GPU count would
--             outgrow DefCpuPerGPU; capped at one task per GPU.

-- JOB_CPUS_SET (slurm.h SLURM_BIT(15)): "-c was given". Not exported to Lua.
local JOB_CPUS_SET = 32768

local function is_set(v, unset)
    return v ~= nil and v ~= unset
end

local function is_nonempty(s)
    return s ~= nil and s ~= ""
end

-- GPU count in one request field, or nil when that field asks for no GPU.
local function gpu_count_in(s)
    if not is_nonempty(s) then return nil end
    for token in string.gmatch(s, "[^,]+") do
        if is_gpu_token(token) then return gpu_count_of_token(token) end
    end
    return nil
end

local function reject_memory()
    slurm.log_user("Error: GPU jobs take memory from the partition default " ..
                   "(DefMemPerCPU/DefMemPerGPU). Remove --mem, --mem-per-cpu " ..
                   "and --mem-per-gpu and submit again.")
    return slurm.ERROR
end

local function wants_memory(job_desc)
    return job_desc.min_mem_per_node ~= nil or job_desc.min_mem_per_cpu ~= nil
        or is_nonempty(job_desc.mem_per_tres)
end

-- Numbers from slurmctld arrive as floats under Lua 5.3+, hence %d, not "..".
local function reject_tasks(opt, value, cap, scope)
    local limit = cap and string.format("%d %s", cap, scope) or "unknown " .. scope
    slurm.log_user("Error: %s=%d exceeds the GPU count (%s); GPU jobs run at most " ..
                   "one task per GPU. Request more GPUs, or more nodes with -N.",
                   opt, value, limit)
    return slurm.ERROR
end

-- Tasks may not outnumber GPUs, per job and per node. Unset node count means one
-- node, the same assumption Slurm makes when it estimates CPUs at submit.
local function check_tasks(job_desc)
    local nodes = 1
    if is_set(job_desc.min_nodes, slurm.NO_VAL) and job_desc.min_nodes > 1 then
        nodes = job_desc.min_nodes
    end
    local per_node = gpu_count_in(job_desc.gres) or gpu_count_in(job_desc.tres_per_node)
    local per_job = gpu_count_in(job_desc.tres_per_job)
    local per_socket = gpu_count_in(job_desc.tres_per_socket)
    local total = per_job or (per_node and per_node * nodes)

    if is_set(job_desc.ntasks_per_tres, slurm.NO_VAL16) and job_desc.ntasks_per_tres > 1 then
        return reject_tasks("--ntasks-per-gpu", job_desc.ntasks_per_tres, 1, "per GPU")
    end
    if is_set(job_desc.ntasks_per_socket, slurm.NO_VAL16) then
        if per_socket == nil or job_desc.ntasks_per_socket > per_socket then
            return reject_tasks("--ntasks-per-socket", job_desc.ntasks_per_socket,
                                per_socket or 0, "per socket")
        end
    end
    -- --gpus-per-task fixes the GPU count at tasks x N, so tasks cannot outnumber it.
    if gpu_count_in(job_desc.tres_per_task) ~= nil then
        return slurm.SUCCESS
    end
    local per_node_cap = per_node or total
    if is_set(job_desc.ntasks_per_node, slurm.NO_VAL16) then
        if per_node_cap == nil or job_desc.ntasks_per_node > per_node_cap then
            return reject_tasks("--ntasks-per-node", job_desc.ntasks_per_node,
                                per_node_cap, "per node")
        end
    end
    if is_set(job_desc.num_tasks, slurm.NO_VAL) then
        if total == nil or job_desc.num_tasks > total then
            return reject_tasks("--ntasks", job_desc.num_tasks, total, "in total")
        end
    end
    return slurm.SUCCESS
end

-- Remove "cpu=N" (-c, --tres-per-task=cpu=N) from tres_per_task, keeping the rest.
local function drop_task_cpus(job_desc)
    local s = job_desc.tres_per_task
    if not is_nonempty(s) then return false end
    local kept, changed = {}, false
    for token in string.gmatch(s, "[^,]+") do
        if string.match(token, "^cpu[:=]") then
            changed = true
        else
            kept[#kept + 1] = token
        end
    end
    if changed then job_desc.tres_per_task = table.concat(kept, ",") end
    return changed
end

-- Reset every CPU-sizing field to unset and tell the user which options were ignored.
-- min_cpus is always filled in by sbatch (tasks x cpus-per-task) and is recomputed
-- from what is left, so it is only reported when the user set it on its own (modify).
local function drop_cpu_sizing(job_desc, report_min_cpus)
    local dropped = {}
    local function note(opt)
        for _, o in ipairs(dropped) do if o == opt then return end end
        dropped[#dropped + 1] = opt
    end
    if is_set(job_desc.cpus_per_task, slurm.NO_VAL16) then
        job_desc.cpus_per_task = slurm.NO_VAL16
        note("--cpus-per-task")
    end
    if drop_task_cpus(job_desc) then note("--cpus-per-task") end
    if job_desc.bitflags ~= nil and
       math.floor(job_desc.bitflags / JOB_CPUS_SET) % 2 == 1 then
        job_desc.bitflags = job_desc.bitflags - JOB_CPUS_SET
    end
    if is_nonempty(job_desc.cpus_per_tres) then
        job_desc.cpus_per_tres = ""
        note("--cpus-per-gpu")
    end
    if is_set(job_desc.pn_min_cpus, slurm.NO_VAL16) then
        job_desc.pn_min_cpus = slurm.NO_VAL16
        note("--mincpus")
    end
    if is_set(job_desc.max_cpus, slurm.NO_VAL) then
        job_desc.max_cpus = slurm.NO_VAL
    end
    if is_set(job_desc.min_cpus, slurm.NO_VAL) then
        job_desc.min_cpus = slurm.NO_VAL
        if report_min_cpus then note("NumCPUs") end
    end
    -- --exclusive (shared=0, or =user/mcs/topo) allocates the whole node.
    if is_set(job_desc.shared, slurm.NO_VAL16) then
        job_desc.shared = slurm.NO_VAL16
        note("--exclusive/--oversubscribe")
    end
    if #dropped > 0 then
        slurm.log_user("Note: GPU jobs take CPUs from the partition default " ..
                       "(DefCpuPerGPU); ignored %s.", table.concat(dropped, ", "))
    end
end

-- Route a GPU job to every partition of its type. Under FORCE_GPU_PARTITION a named
-- partition is overridden (and the user told); otherwise only an empty one is filled.
local function route_gpu_job(job_desc, gpu_type)
    local partition = GPU_TYPE_PARTITION[gpu_type]
    local named = job_desc.partition
    if not is_nonempty(named) then
        job_desc.partition = partition
    elseif FORCE_GPU_PARTITION and named ~= partition then
        slurm.log_user("Note: GPU jobs are routed by GPU type; ignored partition " ..
                       "'%s', using %s.", named, partition)
        job_desc.partition = partition
    end
end

-- [3] Submit hook. Resolve the GPU type first, then route on it -- one lookup, so a
-- typed request, one typed by its partition and one falling back to the site default
-- all reach their partitions the same way.
local function job_submit(job_desc, part_list, submit_uid)
    local has_partition = is_nonempty(job_desc.partition)
    local want_gpu, gpu_type, gpu_count, gpu_multi = detect_gpu(job_desc)

    -- CPU-only work has no type to carry; it only needs a partition if it named none.
    if not want_gpu then
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

    -- Untyped request: take the type from the partition it named, else from the site
    -- default. Every path out of this block carries a type or has been rejected -- an
    -- untyped request is invisible to the typed GrpTRES limits (see the header), so
    -- letting one through is exactly the bug this structure exists to prevent.
    if gpu_type == nil then
        if has_partition then
            gpu_type = gpu_type_of_partition(job_desc.partition)
        end
        if gpu_type == nil and DEFAULT_GPU_TYPE ~= "" and
           (not has_partition or FORCE_GPU_PARTITION) then
            gpu_type = DEFAULT_GPU_TYPE
            slurm.log_user("Note: GPU type unspecified; defaulting to %s. " ..
                           "Use --gres=gpu:<type>:N to be explicit.", gpu_type)
        end
        if gpu_type == nil then
            return handle_untypeable(job_desc.partition, gpu_count)
        end
        stamp_gpu_type(job_desc, gpu_type)
    end

    -- A named type must be one we know. Reject an unknown one instead of silently
    -- downgrading it to the default -- that surprises --gres jobs (silent swap) and
    -- makes --gpus jobs pend forever against a partition lacking that type.
    if GPU_TYPE_PARTITION[gpu_type] == nil then
        return reject_unknown_type(gpu_type)
    end

    -- Validate before rewriting anything, so a rejected job is reported as submitted.
    if GPU_JOBS_USE_DEFAULTS then
        if wants_memory(job_desc) then
            return reject_memory()
        end
        local rc = check_tasks(job_desc)
        if rc ~= slurm.SUCCESS then return rc end
        drop_cpu_sizing(job_desc, false)
    end
    route_gpu_job(job_desc, gpu_type)
    return slurm.SUCCESS
end

-- [4] Modify hook (scontrol update). A pending job's GPU request can be rewritten
-- after submission, so the same normalisation has to run here -- otherwise
-- "scontrol update job=N TresPerNode=gres/gpu:4" strips the type back off and walks
-- straight past the limits the submit hook just enforced. job_desc holds only the
-- fields being changed (the rest unset); job_rec is the job as it stands.
-- root (sudo scontrol) is exempt from the sizing and partition rules, so an admin can
-- still move or resize a job by hand; the type stamping applies to everyone.
local function job_modify(job_desc, job_rec, part_list, modify_uid)
    local want_gpu, gpu_type, gpu_count, gpu_multi = detect_gpu(job_desc)
    if want_gpu then
        if gpu_multi then
            return reject_multi_type()
        end
        if gpu_type ~= nil then
            if GPU_TYPE_PARTITION[gpu_type] == nil then
                return reject_unknown_type(gpu_type)
            end
        else
            -- Fall back to the job's current partition when the update does not
            -- change it. No default-type fallback here: the submit hook already gave
            -- the job a partition, and stamping a type that partition does not hold
            -- would leave it pending forever.
            local partition = job_desc.partition
            if not is_nonempty(partition) then
                partition = job_rec ~= nil and job_rec.partition or nil
            end
            if is_nonempty(partition) then
                gpu_type = gpu_type_of_partition(partition)
            end
            if gpu_type == nil then
                return handle_untypeable(partition, gpu_count)
            end
            stamp_gpu_type(job_desc, gpu_type)
        end
    end

    if modify_uid == 0 then
        return slurm.SUCCESS
    end
    local rec_gpu, rec_type = false, nil
    if job_rec ~= nil then
        rec_gpu, rec_type = detect_gpu(job_rec)
    end
    if not want_gpu and not rec_gpu then
        return slurm.SUCCESS
    end

    if GPU_JOBS_USE_DEFAULTS then
        -- A CPU job may carry any sizing; adding GPUs later would keep it.
        if want_gpu and not rec_gpu then
            slurm.log_user("Error: GPUs cannot be added to a CPU job; submit a new " ..
                           "GPU job instead.")
            return slurm.ERROR
        end
        if wants_memory(job_desc) then
            return reject_memory()
        end
        if is_set(job_desc.num_tasks, slurm.NO_VAL) or
           is_set(job_desc.ntasks_per_node, slurm.NO_VAL16) or
           is_set(job_desc.ntasks_per_tres, slurm.NO_VAL16) or
           is_set(job_desc.ntasks_per_socket, slurm.NO_VAL16) then
            slurm.log_user("Error: the task layout of a GPU job cannot be changed; " ..
                           "submit it again instead.")
            return slurm.ERROR
        end
        drop_cpu_sizing(job_desc, true)
    end
    if FORCE_GPU_PARTITION and is_nonempty(job_desc.partition) then
        local t = gpu_type or rec_type or gpu_type_of_partition(job_desc.partition)
        if t ~= nil and GPU_TYPE_PARTITION[t] ~= nil then
            route_gpu_job(job_desc, t)
        end
    end
    return slurm.SUCCESS
end

-- [5] Entry points. slurmctld logs a Lua runtime error and then ACCEPTS the job or
-- update as it stands (fail-open), which would let a policy bug wave jobs through
-- half-checked. Catch it here and reject instead.
local function guarded(hook, ...)
    local ok, rc = pcall(hook, ...)
    if ok then return rc end
    slurm.log_error("job_submit.lua: %s", tostring(rc))
    slurm.log_user("Error: the submit policy failed internally; please report this " ..
                   "to the cluster administrators.")
    return slurm.ERROR
end

function slurm_job_submit(job_desc, part_list, submit_uid)
    return guarded(job_submit, job_desc, part_list, submit_uid)
end

function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
    return guarded(job_modify, job_desc, job_rec, part_list, modify_uid)
end
