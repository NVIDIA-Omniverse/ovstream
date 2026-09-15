# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Scene ownership: the ovstage stage, the streaming RenderProduct, and per-frame writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple

import numpy as np

import ovstage

RENDER_PRODUCT = "/Render/Stream"
LDR_VAR = RENDER_PRODUCT + "/LdrColor"

_ASSETS = Path(__file__).with_name("assets")


def write_wrapper_layer(scene: str, width: int, height: int, camera: str,
                        fg: bool, fg_interpolated: int) -> str:
    """Write a root layer that sublayers `scene` and defines the RenderProduct.

    The RenderProduct lives in the root layer on purpose: in ovrtx 0.5 a
    product added through an inline referenced layer does not resolve its
    camera relationship reliably. Returns the path of the written file.
    """
    template = (_ASSETS / "render_products.usda").read_text(encoding="utf-8")
    # The wrapper lives in the temporary directory, so a relative local path would resolve
    # against it; anchor local paths to the invocation directory and leave URLs alone.
    if "://" not in scene:
        scene = os.path.abspath(scene)
    usda = (template
            .replace("@SCENE@", scene.replace("\\", "/"))
            .replace("@WIDTH@", str(width)).replace("@HEIGHT@", str(height))
            .replace("@CAMERA@", camera)
            .replace("@FG@", "1" if fg else "0")
            .replace("@FG_COUNT@", str(fg_interpolated)))
    fd, path = tempfile.mkstemp(prefix="app_stream_", suffix=".usda")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(usda)
    return path


class SceneController:
    """Owns the ovstage Stage and publishes per-frame writes under fresh ordinals."""

    def __init__(self, scene: str, camera: str, size: Tuple[int, int], fg: bool, fg_interpolated: int):
        self.stage = ovstage.Stage("app_stream")
        self.ordinal = 1
        self._wrapper = write_wrapper_layer(scene, size[0], size[1], camera, fg, fg_interpolated)
        self._paths = None
        self._camera_query = None
        self._product_query = None
        self._camera = camera

    def populate(self) -> None:
        ovstage.population.open_usd(self.stage, self._wrapper, ordinal=self.ordinal)
        self.stage.advance_write_floor(self.ordinal, ovstage.Scope.ALL).wait()
        self._paths = ovstage.PathDictionary(self.stage)
        self._camera_list = self._paths.create_path_list_from_strings([self._camera])
        self._camera_query = self.stage.query_from_path_list(self._camera_list)
        self._product_list = self._paths.create_path_list_from_strings([RENDER_PRODUCT])
        self._product_query = self.stage.query_from_path_list(self._product_list)
        self._xform = self._paths.intern_token("omni:xform")
        self._xform_dtype = ovstage.numpy_to_dldatatype(np.dtype(np.float64), lanes=16)

    def next_ordinal(self) -> int:
        self.ordinal += 1
        return self.ordinal

    def write_camera(self, matrix: np.ndarray, ordinal: int) -> None:
        tensor = ovstage.make_dltensor(matrix.reshape(1, 4, 4), dtype=self._xform_dtype, shape=[1], ndim=1)
        self.stage.write_attribute(self._camera_query, self._xform, ordinal=ordinal,
                                   tensors=tensor, is_array=False).wait()
        self.stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()

    def _write_product_attr(self, name: str, value: np.ndarray, ordinal: int) -> None:
        self.stage.write_attribute(self._product_query, self._paths.intern_token(name), ordinal=ordinal,
                                   tensors=value, is_array=False).wait()
        self.stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()

    # [snippet:runtime-product-writes]
    def set_resolution(self, width: int, height: int) -> int:
        """Change the RenderProduct resolution at runtime; the next step renders at the new size."""
        ordinal = self.next_ordinal()
        dtype = ovstage.numpy_to_dldatatype(np.dtype(np.int32), lanes=2)
        tensor = ovstage.make_dltensor(np.array([[width, height]], dtype=np.int32), dtype=dtype, shape=[1], ndim=1)
        self.stage.write_attribute(self._product_query, self._paths.intern_token("resolution"), ordinal=ordinal,
                                   tensors=tensor, is_array=False).wait()
        self.stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()
        return ordinal

    def set_frame_generation(self, enabled: bool, interpolated: int) -> int:
        """Toggle frame generation at runtime. Returns the ordinal the change is published under."""
        ordinal = self.next_ordinal()
        self._write_product_attr("omni:rtx:dlss:frameGeneration", np.array([enabled]), ordinal)
        ordinal = self.next_ordinal()
        self._write_product_attr("omni:rtx:dlss:frameGenerationInterpolatedFrameCount",
                                 np.array([interpolated], dtype=np.int32), ordinal)
        return ordinal
    # [/snippet:runtime-product-writes]

    def close(self) -> None:
        for q in (self._camera_query, self._product_query):
            if q is not None:
                q.release().wait()
        if self._paths is not None:
            self._paths.destroy_path_list(self._camera_list)
            self._paths.destroy_path_list(self._product_list)
            self._paths.destroy()
        self.stage.destroy()
        try:
            os.unlink(self._wrapper)
        except OSError:
            pass
