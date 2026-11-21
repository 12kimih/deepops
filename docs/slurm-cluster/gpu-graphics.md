# GPU graphics and video in jobs

The NVIDIA driver from the Ubuntu archive is installed as `ubuntu-drivers` installs
it: Canonical's signed, precompiled kernel modules for the running kernel
(`linux-modules-nvidia-<branch>-server-open-<flavour>`, which follow kernel updates)
and the full driver (`nvidia-driver-<branch>-server-open`). The modules package
provides the driver's `nvidia-dkms-` dependency, so nothing is compiled on the node and
Secure Boot needs no key of its own. NVIDIA's own repositories
(`nvidia_driver_install_method: nvidia_repo`, and RHEL) install their full driver too.

The full driver is every capability NVIDIA names, at the driver's own version:

| Capability | What it is |
|---|---|
| compute, utility | CUDA, `nvidia-smi` |
| graphics | OpenGL, EGL and Vulkan on the GPU without a display: 3D, simulators, renderers, offscreen OpenGL |
| video | NVENC and NVDEC: encoding and decoding video on the GPU (ffmpeg `h264_nvenc`, DALI, decord, torchvision GPU decoding) |

It also brings an X server driver and Mesa's libraries, which nothing starts on a node
booted into `multi-user.target`. `nvidia_driver_headless: true` installs only compute
and utility (`nvidia-headless-no-dkms-`, `nvidia-utils-`), for nodes that never render
or code video. Without graphics a job does not fail cleanly: a Vulkan application
finds only Mesa's CPU renderer (lavapipe, from `mesa-vulkan-drivers`) and runs many
times slower, an EGL one finds no device.

Do not install `mesa-vulkan-drivers` on GPU nodes: its CPU renderer is a second Vulkan
device an application may pick instead of the GPU. The retired nvidia-vulkan playbook
installed it, with `libvulkan-dev` (headers belong in the user's environment or
image); list both in `host_baseline_absent_packages` to remove them.

## Checking from a job

```bash
srun --gres=gpu:<type>:1 nvidia-smi --query-gpu=name --format=csv
srun --gres=gpu:<type>:1 ls /usr/share/vulkan/icd.d/   # nvidia_icd.json: Vulkan on the GPU
```

In a container with `vulkan-tools`, `vulkaninfo --summary` must name the NVIDIA GPU; a
`llvmpipe` device means Vulkan fell back to the CPU.

## Rendering without a display

Use EGL or Vulkan directly; nothing reads `DISPLAY`. Common switches:

- PyOpenGL, pyrender: `PYOPENGL_PLATFORM=egl`
- MuJoCo: `MUJOCO_GL=egl`
- With several GPUs in the job, EGL and Vulkan enumerate only the job's GPUs; device
  indices count from those.

## Containers

The NVIDIA container runtime (enroot under pyxis, Docker with the NVIDIA Container
Toolkit) mounts only the driver capabilities a container asks for, `compute,utility`
unless told otherwise. Ask in the image (`ENV
NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,video`, or `all`) or in the job's
environment:

```bash
export NVIDIA_DRIVER_CAPABILITIES=all
srun --gres=gpu:<type>:1 --container-image=<image> vulkaninfo --summary
```

The runtime mounts the host driver's libraries, it brings none of its own, so the host
must have the capability (`nvidia_driver_headless: true` has only compute and
utility). The image still needs the loaders (`libvulkan1`, `libegl1`).
