"""Image functional probe manifest generation from the administrator image catalog."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import (
    IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
    ImageProbeManifest,
    ImageProbeSpec,
    ProbeExecutionError,
    ProbeSpec,
    file_sha256,
    parse_image_digest,
)

# Registry of capability probe generators
# Each entry produces a Python code string that tests the capability within resource/time bounds.
# Successful execution prints a single JSON line starting with 'PROBE_META:' for metadata extraction.

_CAPABILITY_PROBE_TEMPLATES: dict[str, dict[str, Any]] = {
    "python": {
        "description": "Python startup, standard library, and basic calculation",
        "expected_metadata_keys": ("python_version",),
        "script": (
            "import sys, math, json, os\n"
            "assert math.sqrt(16.0) == 4.0\n"
            "assert json.loads(json.dumps({'k': 1}))['k'] == 1\n"
            "meta = {'python_version': sys.version.split()[0]}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "jupyterlab": {
        "description": "JupyterLab library and version accessibility",
        "expected_metadata_keys": ("jupyterlab_version",),
        "script": (
            "import json, jupyterlab\n"
            "meta = {'jupyterlab_version': str(getattr(jupyterlab, '__version__', 'unknown'))}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "numpy": {
        "description": "NumPy import and vector math operation",
        "expected_metadata_keys": ("numpy_version",),
        "script": (
            "import json, numpy as np\n"
            "arr = np.arange(10, dtype=np.float64)\n"
            "assert np.isclose(arr.mean(), 4.5)\n"
            "meta = {'numpy_version': str(np.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "pandas": {
        "description": "Pandas DataFrame creation and column aggregation",
        "expected_metadata_keys": ("pandas_version",),
        "script": (
            "import json, pandas as pd\n"
            "df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})\n"
            "assert int(df['a'].sum()) == 6\n"
            "meta = {'pandas_version': str(pd.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "scipy": {
        "description": "SciPy linear algebra operation",
        "expected_metadata_keys": ("scipy_version",),
        "script": (
            "import json, scipy, scipy.linalg\n"
            "inv = scipy.linalg.inv([[1.0, 0.0], [0.0, 1.0]])\n"
            "assert inv[0, 0] == 1.0\n"
            "meta = {'scipy_version': str(scipy.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "scikit-learn": {
        "description": "Scikit-Learn estimator fitting and prediction",
        "expected_metadata_keys": ("sklearn_version",),
        "script": (
            "import json, sklearn\n"
            "from sklearn.linear_model import Ridge\n"
            "model = Ridge().fit([[0.0], [1.0]], [0.0, 1.0])\n"
            "pred = float(model.predict([[2.0]])[0])\n"
            "assert 1.0 <= pred <= 2.5\n"
            "meta = {'sklearn_version': str(sklearn.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "visualization": {
        "description": "Headless Matplotlib chart rendering and figure close",
        "expected_metadata_keys": ("matplotlib_version",),
        "script": (
            "import json, matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.plot([1, 2], [3, 4])\n"
            "plt.close(fig)\n"
            "meta = {'matplotlib_version': str(matplotlib.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "pytorch": {
        "description": "PyTorch CPU tensor allocation, math, and verification",
        "expected_metadata_keys": ("torch_version",),
        "script": (
            "import json, torch\n"
            "x = torch.tensor([1.0, 2.0, 3.0])\n"
            "y = x * 2.0\n"
            "assert y.tolist() == [2.0, 4.0, 6.0]\n"
            "meta = {'torch_version': str(torch.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "tensorflow": {
        "description": "TensorFlow CPU constant tensor addition",
        "expected_metadata_keys": ("tensorflow_version",),
        "script": (
            "import os, json\n"
            "os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'\n"
            "import tensorflow as tf\n"
            "a = tf.constant([1.0, 2.0])\n"
            "b = a + a\n"
            "assert b.numpy().tolist() == [2.0, 4.0]\n"
            "meta = {'tensorflow_version': str(tf.__version__)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "keras": {
        "description": "Keras package accessibility and version query",
        "expected_metadata_keys": ("keras_version",),
        "script": (
            "import json\n"
            "try:\n"
            "    import keras\n"
            "    v = keras.__version__\n"
            "except ImportError:\n"
            "    from tensorflow import keras\n"
            "    v = keras.__version__\n"
            "meta = {'keras_version': str(v)}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
    "cuda-userspace": {
        "description": "Concrete CUDA userspace API observation through a supported framework",
        "expected_metadata_keys": ("cuda_probe_status", "cuda_api", "cuda_library"),
        "script": (
            "import json, sys\n"
            "details = {'cuda_probe_status': 'UNAVAILABLE', 'cuda_api': 'none', 'cuda_library': 'none'}\n"
            "api_failures = []\n"
            "supported_library_observed = False\n"
            "try:\n"
            "    import torch\n"
            "    supported_library_observed = True\n"
            "    details['torch_version'] = str(torch.__version__)\n"
            "    details['torch_cuda_version'] = str(torch.version.cuda or '')\n"
            "    if torch.version.cuda:\n"
            "        try:\n"
            "            details['torch_cuda_device_count'] = str(int(torch.cuda.device_count()))\n"
            "            details.update(cuda_probe_status='SUCCESS', cuda_api='torch.cuda.device_count', cuda_library='torch')\n"
            "        except Exception as exc:\n"
            "            api_failures.append('torch:' + type(exc).__name__)\n"
            "except ImportError:\n"
            "    pass\n"
            "if details['cuda_probe_status'] != 'SUCCESS':\n"
            "  try:\n"
            "    import tensorflow as tf\n"
            "    supported_library_observed = True\n"
            "    details['tensorflow_version'] = str(tf.__version__)\n"
            "    if bool(tf.test.is_built_with_cuda()):\n"
            "        try:\n"
            "            details['tensorflow_gpu_devices'] = str(len(tf.config.list_physical_devices('GPU')))\n"
            "            details.update(cuda_probe_status='SUCCESS', cuda_api='tf.config.list_physical_devices', cuda_library='tensorflow')\n"
            "        except Exception as exc:\n"
            "            api_failures.append('tensorflow:' + type(exc).__name__)\n"
            "  except (ImportError, AttributeError):\n"
            "    pass\n"
            "if details['cuda_probe_status'] != 'SUCCESS' and api_failures:\n"
            "    details['cuda_probe_status'] = 'FAILURE'\n"
            "    details['cuda_api_failures'] = ','.join(api_failures)\n"
            "elif details['cuda_probe_status'] != 'SUCCESS' and supported_library_observed:\n"
            "    details['unavailable_reason'] = 'supported_libraries_have_no_cuda_userspace_build'\n"
            "elif details['cuda_probe_status'] != 'SUCCESS':\n"
            "    details['unavailable_reason'] = 'no_supported_cuda_library_importable'\n"
            "print('PROBE_META:' + json.dumps(details))\n"
            "sys.exit(0 if details['cuda_probe_status'] == 'SUCCESS' else (4 if details['cuda_probe_status'] == 'FAILURE' else 3))\n"
        ),
    },
    "data-science": {
        "description": "Combined data science tabular calculation",
        "expected_metadata_keys": ("data_science_stack",),
        "script": (
            "import json, numpy as np, pandas as pd\n"
            "df = pd.DataFrame({'x': np.arange(5, dtype=float)})\n"
            "assert float(df['x'].mean()) == 2.0\n"
            "meta = {'data_science_stack': 'ok'}\n"
            "print('PROBE_META:' + json.dumps(meta))\n"
        ),
    },
}
CAPABILITY_PROBE_TEMPLATES: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        capability: MappingProxyType(dict(template))
        for capability, template in _CAPABILITY_PROBE_TEMPLATES.items()
    }
)
del _CAPABILITY_PROBE_TEMPLATES


MAX_PROBE_TIMEOUT_SECONDS = 120.0
MAX_PROBE_CPU_MILLICORES = 2000
MAX_PROBE_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
_CPU_LIMIT = re.compile(r"^([1-9][0-9]*)m$")
_MEMORY_LIMIT = re.compile(r"^([1-9][0-9]*)(Ki|Mi|Gi)$")
_MEMORY_MULTIPLIERS = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}


def validate_probe_bounds(probe: ProbeSpec) -> None:
    """Reject unbounded or excessive execution settings before a runtime call."""
    if (
        not math.isfinite(probe.timeout_seconds)
        or probe.timeout_seconds <= 0
        or probe.timeout_seconds > MAX_PROBE_TIMEOUT_SECONDS
    ):
        raise ProbeExecutionError(
            f"probe timeout must be in (0, {MAX_PROBE_TIMEOUT_SECONDS}] seconds"
        )
    cpu_match = _CPU_LIMIT.fullmatch(probe.cpu_limit)
    if not cpu_match or int(cpu_match.group(1)) > MAX_PROBE_CPU_MILLICORES:
        raise ProbeExecutionError(
            f"probe CPU limit must be 1-{MAX_PROBE_CPU_MILLICORES}m"
        )
    memory_match = _MEMORY_LIMIT.fullmatch(probe.memory_limit)
    memory_bytes = (
        int(memory_match.group(1)) * _MEMORY_MULTIPLIERS[memory_match.group(2)]
        if memory_match
        else 0
    )
    if not memory_match or memory_bytes > MAX_PROBE_MEMORY_BYTES:
        raise ProbeExecutionError("probe memory limit must be positive and at most 2Gi")


def validate_approved_probe_spec(image_spec: ImageProbeSpec, probe: ProbeSpec) -> None:
    """Require the exact registered probe program and bounded settings."""
    validate_probe_bounds(probe)
    expected = create_capability_probe(
        image_spec.image_id,
        probe.capability,
        timeout_seconds=probe.timeout_seconds,
        cpu_limit=probe.cpu_limit,
        memory_limit=probe.memory_limit,
    )
    if probe != expected or probe not in image_spec.probes:
        raise ProbeExecutionError(
            f"probe {probe.probe_id!r} is not the approved manifest probe for "
            f"image {image_spec.image_id!r}"
        )


def create_capability_probe(
    image_id: str,
    capability: str,
    *,
    timeout_seconds: float = 15.0,
    cpu_limit: str = "1000m",
    memory_limit: str = "1Gi",
) -> ProbeSpec:
    """Instantiate a bounded ProbeSpec for a documented capability."""
    norm_cap = capability.strip().lower()
    template = CAPABILITY_PROBE_TEMPLATES.get(norm_cap)
    if template is None:
        raise ValueError(
            f"Catalog capability {capability!r} on image {image_id!r} has no defined probe template "
            f"or documented non-probe policy. Supported capabilities: {sorted(CAPABILITY_PROBE_TEMPLATES.keys())}."
        )
    description = template["description"]
    script = template["script"]
    expected_keys = template["expected_metadata_keys"]

    probe_id = f"probe:{image_id}:{norm_cap}"
    probe = ProbeSpec(
        probe_id=probe_id,
        capability=norm_cap,
        description=description,
        script=script,
        timeout_seconds=timeout_seconds,
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
        expected_metadata_keys=tuple(expected_keys),
    )
    validate_probe_bounds(probe)
    return probe


def build_image_probes(
    image_id: str,
    image_reference: str,
    documented_capabilities: Sequence[str],
    *,
    timeout_seconds: float = 15.0,
    cpu_limit: str = "1000m",
    memory_limit: str = "1Gi",
) -> ImageProbeSpec:
    """Build the collection of probes for a single approved image."""
    digest = parse_image_digest(image_reference)
    probes: list[ProbeSpec] = []
    # Always include baseline python probe if not already present
    caps_to_probe = list(documented_capabilities)
    if "python" not in caps_to_probe:
        caps_to_probe.insert(0, "python")

    for cap in caps_to_probe:
        probes.append(
            create_capability_probe(
                image_id=image_id,
                capability=cap,
                timeout_seconds=timeout_seconds,
                cpu_limit=cpu_limit,
                memory_limit=memory_limit,
            )
        )

    return ImageProbeSpec(
        image_id=image_id,
        image_reference=image_reference,
        image_digest=digest,
        documented_capabilities=tuple(documented_capabilities),
        probes=tuple(probes),
    )


def build_image_probe_manifest(
    catalog: Mapping[str, Any],
    catalog_path: Path | str,
    *,
    timeout_seconds: float = 15.0,
    cpu_limit: str = "1000m",
    memory_limit: str = "1Gi",
) -> ImageProbeManifest:
    """Build the complete ImageProbeManifest for all approved catalog images."""
    catalog_version = str(catalog.get("catalog_version", "unknown"))
    cat_path = Path(catalog_path)
    cat_sha = file_sha256(cat_path) if cat_path.is_file() else "unknown"

    images_data = catalog.get("images", {})
    image_specs: list[ImageProbeSpec] = []

    for image_id in sorted(images_data.keys()):
        entry = images_data[image_id]
        if not isinstance(entry, Mapping):
            continue
        reference = str(entry.get("reference", ""))
        capabilities = list(entry.get("capabilities", []))
        image_spec = build_image_probes(
            image_id=image_id,
            image_reference=reference,
            documented_capabilities=capabilities,
            timeout_seconds=timeout_seconds,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
        )
        image_specs.append(image_spec)

    return ImageProbeManifest(
        schema_version=IMAGE_PROBE_MANIFEST_SCHEMA_VERSION,
        catalog_version=catalog_version,
        catalog_sha256=cat_sha,
        catalog_path=str(cat_path),
        images=tuple(image_specs),
    )
