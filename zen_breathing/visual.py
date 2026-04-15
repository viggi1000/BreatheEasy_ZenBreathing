"""
OpenGL visual engine -- QOpenGLWidget + moderngl with ping-pong FBO feedback.

Renders the bioluminescent ocean or aurora shader at ~60 fps,
reading uniform values from the shared BreathingState.
"""

import time
import numpy as np

from PyQt5.QtWidgets import QOpenGLWidget
from PyQt5.QtCore import QTimer

try:
    import moderngl
    HAS_MODERNGL = True
except ImportError:
    HAS_MODERNGL = False

from zen_breathing.shaders import (
    VERTEX_SHADER,
    OCEAN_FRAGMENT_SHADER,
    AURORA_FRAGMENT_SHADER,
    ORB_FRAGMENT_SHADER,
    BLIT_FRAGMENT_SHADER,
)


class ZenVisualWidget(QOpenGLWidget):
    """
    Full-screen GPU-accelerated breathing visual.

    Ping-pong FBO architecture:
        Frame N:
          1. Render main shader -> fbo_write  (reads fbo_read as feedback)
          2. Swap fbo_write <-> fbo_read
          3. Blit fbo_read -> Qt screen
    """

    THEMES = {
        "ocean":  OCEAN_FRAGMENT_SHADER,
        "aurora": AURORA_FRAGMENT_SHADER,
        "orb":    ORB_FRAGMENT_SHADER,
    }

    def __init__(self, state, theme="ocean", parent=None):
        super().__init__(parent)
        self.state = state
        self._theme_name = theme
        self._fragment_src = self.THEMES.get(theme, OCEAN_FRAGMENT_SHADER)
        self._start_time = time.perf_counter()
        self._ctx = None
        self._ready = False

        # 60 fps render timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(16)  # ~60 fps

    # ------------------------------------------------------------------ #
    #  OpenGL lifecycle
    # ------------------------------------------------------------------ #

    def initializeGL(self):
        try:
            self._ctx = moderngl.create_context()
        except Exception as e:
            print(f"[ZenVisual] Failed to create moderngl context: {e}")
            return

        # Fullscreen quad vertices  (TRIANGLE_STRIP: BL, BR, TL, TR)
        verts = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
        self._vbo = self._ctx.buffer(verts)

        # Main shader (ocean / aurora)
        try:
            self._main_prog = self._ctx.program(
                vertex_shader=VERTEX_SHADER,
                fragment_shader=self._fragment_src,
            )
        except Exception as e:
            print(f"[ZenVisual] Shader compile error:\n{e}")
            return

        self._main_vao = self._ctx.simple_vertex_array(
            self._main_prog, self._vbo, "in_position"
        )

        # Blit shader
        self._blit_prog = self._ctx.program(
            vertex_shader=VERTEX_SHADER,
            fragment_shader=BLIT_FRAGMENT_SHADER,
        )
        self._blit_vao = self._ctx.simple_vertex_array(
            self._blit_prog, self._vbo, "in_position"
        )

        # Ping-pong FBOs (created in resizeGL, which Qt calls after initializeGL)
        self._tex_read = None
        self._tex_write = None
        self._fbo_read = None
        self._fbo_write = None

        self._ready = True

    def resizeGL(self, w, h):
        if not self._ready or self._ctx is None:
            return
        w = max(w, 1)
        h = max(h, 1)

        # Release old FBOs
        for obj in (self._tex_read, self._tex_write, self._fbo_read, self._fbo_write):
            if obj is not None:
                obj.release()

        # Create new FBO pair
        self._tex_read = self._ctx.texture((w, h), 4)
        self._tex_read.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._tex_read.repeat_x = False
        self._tex_read.repeat_y = False

        self._tex_write = self._ctx.texture((w, h), 4)
        self._tex_write.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._tex_write.repeat_x = False
        self._tex_write.repeat_y = False

        self._fbo_read = self._ctx.framebuffer(color_attachments=[self._tex_read])
        self._fbo_write = self._ctx.framebuffer(color_attachments=[self._tex_write])

        # Clear to black
        self._fbo_read.clear(0.0, 0.0, 0.0, 1.0)
        self._fbo_write.clear(0.0, 0.0, 0.0, 1.0)

    def paintGL(self):
        if not self._ready or self._ctx is None:
            return
        if self._fbo_write is None:
            return

        t = time.perf_counter() - self._start_time
        s = self.state.get_smooth()

        # ---- Pass 1: Main shader -> fbo_write (reads fbo_read as feedback) ----
        self._fbo_write.use()
        self._tex_read.use(location=0)

        prog = self._main_prog
        self._set_uniform(prog, "u_time", t)
        self._set_uniform(prog, "u_resolution", (float(self.width()), float(self.height())))
        self._set_uniform(prog, "u_breath", s["breath"])
        self._set_uniform(prog, "u_coherence", s["coherence"])
        self._set_uniform(prog, "u_energy", s["energy"])
        self._set_uniform(prog, "u_target_phase", s["target"])
        self._set_uniform(prog, "u_sync", s["sync"])
        self._set_uniform(prog, "u_prev_frame", 0)

        self._main_vao.render(moderngl.TRIANGLE_STRIP)

        # ---- Swap FBOs ----
        self._fbo_read, self._fbo_write = self._fbo_write, self._fbo_read
        self._tex_read, self._tex_write = self._tex_write, self._tex_read

        # ---- Pass 2: Blit fbo_read -> Qt screen ----
        screen = self._ctx.detect_framebuffer(self.defaultFramebufferObject())
        screen.use()
        self._tex_read.use(location=0)
        self._set_uniform(self._blit_prog, "u_texture", 0)
        self._blit_vao.render(moderngl.TRIANGLE_STRIP)

    # ------------------------------------------------------------------ #
    #  Theme switching
    # ------------------------------------------------------------------ #

    def set_theme(self, theme_name: str):
        if theme_name not in self.THEMES:
            return
        self._theme_name = theme_name
        self._fragment_src = self.THEMES[theme_name]
        if self._ready and self._ctx is not None:
            self.makeCurrent()
            try:
                new_prog = self._ctx.program(
                    vertex_shader=VERTEX_SHADER,
                    fragment_shader=self._fragment_src,
                )
                self._main_prog.release()
                self._main_vao.release()
                self._main_prog = new_prog
                self._main_vao = self._ctx.simple_vertex_array(
                    self._main_prog, self._vbo, "in_position"
                )
                # Clear feedback to avoid old-theme artifacts
                if self._fbo_read:
                    self._fbo_read.clear(0.0, 0.0, 0.0, 1.0)
                if self._fbo_write:
                    self._fbo_write.clear(0.0, 0.0, 0.0, 1.0)
            except Exception as e:
                print(f"[ZenVisual] Theme switch error: {e}")
            self.doneCurrent()

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _set_uniform(prog, name, value):
        """Safely set a uniform (skip if the shader optimised it away)."""
        if name in prog:
            prog[name].value = value
