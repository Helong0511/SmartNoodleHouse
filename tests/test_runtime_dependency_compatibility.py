"""关键视觉依赖必须在同一 Python 运行时中可共同导入。"""
from importlib.metadata import version

from packaging.version import Version


def test_numpy_opencv_onnxruntime_insightface_runtime_compatibility():
    import cv2
    import numpy as np
    import onnxruntime as ort
    from insightface.app import FaceAnalysis

    # 只验证运行时 ABI/模块可用，不在 CI 下载大体积人脸模型文件。
    assert Version(version("numpy")) >= Version("2.2.6")
    assert Version(version("onnxruntime")) >= Version("1.31.1")
    assert np.zeros((2, 2), dtype=np.float32).shape == (2, 2)
    assert cv2.getVersionString()
    assert "CPUExecutionProvider" in ort.get_available_providers()
    assert FaceAnalysis is not None
