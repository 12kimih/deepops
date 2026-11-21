# GPU graphics and video in jobs

The NVIDIA driver DeepOps installs from the Ubuntu archive is the server set without
DKMS: Canonical's signed, precompiled kernel modules for the running kernel
(`linux-modules-nvidia-<branch>-server-open-<flavour>`), the headless userspace
(`nvidia-headless-no-dkms-<branch>-server-open`) and the tools (`nvidia-utils-`). That
is CUDA and `nvidia-smi` only: NVIDIA calls it the *compute* and *utility*
capabilities. The full `nvidia-driver-<branch>-server-open` package is not used: it
depends on `nvidia-dkms-`, which compiles the kernel module on every node at every
kernel update (and needs a signing key under Secure Boot), and it pulls in an X server
driver a compute node has no use for.

Two more capabilities matter for research jobs, each a set of libraries at the
driver's exact version:

| Capability | What it is | Packages (Ubuntu archive) |
|---|---|---|
| `graphics` | OpenGL, EGL, Vulkan on the GPU without a display: 3D, simulators, renderers, offscreen OpenGL | `libnvidia-gl-<branch>`, plus the loaders `libvulkan1`, `libegl1`, `libgl1`, `libgles2`, `vulkan-tools` |
| `video` | NVENC and NVDEC: encoding and decoding video on the GPU (ffmpeg `h264_nvenc`, DALI, decord, torchaudio/torchvision GPU decoding) | `libnvidia-encode-<branch>`, `libnvidia-decode-<branch>` |

Without them a job does not fail cleanly: a Vulkan application finds only Mesa's CPU
renderer (lavapipe, from `mesa-vulkan-drivers`) and runs many times slower, an EGL one
finds no device, and video falls back to the CPU codecs.

## Turning them on

```yaml
# config/group_vars/slurm-node.yml (or the host_vars of the nodes that need them)
nvidia_driver_capabilities: [compute, utility, graphics, video]
```

The next driver deploy (`playbooks/nvidia-software/nvidia-driver.yml`, also part of
`slurm-cluster.yml`) installs them. They are installed apart from the driver and after
it: adding one asks for no reboot (they are libraries), and a branch switch, which
removes every NVIDIA library first, gets them back at the new branch, so they never
fall out of step with the kernel module. Taking one out of the list removes its NVIDIA
libraries (the graphics loaders stay; other software uses them). NVIDIA's own
repositories (`nvidia_driver_install_method: nvidia_repo`, and RHEL) install the full
driver, every capability included.

Turn them on for every node such a job may land on: Slurm does not know which nodes
have them. `display` (an X server on the GPU) is not offered: render offscreen with
`graphics`.

Do not install `mesa-vulkan-drivers` on GPU nodes: its CPU renderer is a second Vulkan
device an application may pick instead of the GPU. The retired nvidia-vulkan playbook
installed it, with `libvulkan-dev` (headers belong in the user's environment or
image); list both in `host_baseline_absent_packages` to remove them.

## Checking from a job

```bash
srun --gres=gpu:<type>:1 vulkaninfo --summary   # GPU0 deviceName must be the NVIDIA GPU
srun --gres=gpu:<type>:1 ffmpeg -hide_banner -encoders | grep nvenc
```

A `llvmpipe` Vulkan device means Vulkan fell back to the CPU.

## Rendering without a display

Use EGL or Vulkan directly; nothing reads `DISPLAY`. Common switches:

- PyOpenGL, pyrender: `PYOPENGL_PLATFORM=egl`
- MuJoCo: `MUJOCO_GL=egl`
- With several GPUs in the job, EGL and Vulkan enumerate only the job's GPUs; device
  indices count from those.

## Containers

The NVIDIA container runtime (enroot under pyxis, Docker with the NVIDIA Container
Toolkit) mounts only the driver capabilities a container asks for, `compute,utility`
unless told otherwise; the names are the same as `nvidia_driver_capabilities`. Ask in
the image (`ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,video`) or in the
job's environment:

```bash
export NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,video
srun --gres=gpu:<type>:1 --container-image=<image> vulkaninfo --summary
```

The host must offer the capability: the runtime mounts the host driver's libraries, it
brings none of its own. The image still needs the graphics loaders (`libvulkan1`,
`libegl1`).
