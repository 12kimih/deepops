# NVIDIA GPU Tests Role

This role is meant to be a quick tool for system validation or simple system burn in. It should not be used as a comprehensive performance test.

Running this will perform the following:

* Optionally install the distribution's `nvidia-cuda-toolkit` package (`gpu_test_install_toolkit`, off by default)
* Download and build cuda-samples
* Run the p2pBandwidthLatencyTest and matrixMul samples
* Run the DCGM diagnostics (`dcgmi diag -r`, level `gpu_test_dcgm_level`, 3 by default)
* Run a basic TensorFlow ResNet job in the `nvcr.io/nvidia/tensorflow:18.07-py3` container

The sample build expects the old flat `Samples/<name>` layout of cuda-samples. Current releases moved
the samples into numbered category directories and build them with CMake, so those steps fail against a
fresh clone.


# Requirements

This role can be applied to a heterogeneous cluster of GPU nodes.

The following should be installed on the system prior to running this role (these come standard in the DGX Operating System):

* CUDA toolkit
* dcgmi
* Docker with the NVIDIA runtime (NVIDIA Container Toolkit)

