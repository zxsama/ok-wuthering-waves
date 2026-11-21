"""Select the input backend that can hold a piece while sending rotation keys."""

from src.task.rahai_blocks import board_visible, canonical_frame


class BoardHidden(Exception):
    def __init__(self, *, reward_visible):
        super().__init__("The Rahai board is no longer visible")
        self.reward_visible = reward_visible


def placement_frame(task):
    frame = task.next_frame()
    canonical = canonical_frame(frame)
    if not board_visible(canonical):
        # Use the same frame for the board check and reward OCR. A normal
        # drag can hide a tray piece while the icon and grid remain visible.
        raise BoardHidden(reward_visible=frame is not None and bool(task._reward(frame=frame)))
    return canonical


def drag_interaction(task):
    manager = task.executor.device_manager
    interaction = getattr(manager, 'drag_interaction', None)
    if interaction is not None:
        return interaction

    window = manager.hwnd_window
    if not window or not window.exists:
        raise RuntimeError("当前游戏画面未连接到可拖拽的输入设备。")

    from ok.device.interaction_methods.pynput import PynputInteraction

    class DesktopDrag(PynputInteraction):
        def swipe(self, x1, y1, x2, y2, duration, settle_time=0):
            from pynput import mouse

            def require_focus():
                if not self.clickable():
                    raise RuntimeError("游戏画面在拖拽时失去焦点。")

            controller = mouse.Controller()
            start_x, start_y = self.capture.get_abs_cords(x1, y1)
            end_x, end_y = self.capture.get_abs_cords(x2, y2)
            steps = max(1, round(duration / 20))
            require_focus()
            controller.position = (start_x, start_y)
            task.sleep(.1)
            require_focus()
            controller.press(mouse.Button.left)
            try:
                for step in range(1, steps + 1):
                    task.sleep(duration / (1000 * steps))
                    require_focus()
                    controller.position = (
                        round(start_x + (end_x - start_x) * step / steps),
                        round(start_y + (end_y - start_y) * step / steps),
                    )
                if settle_time:
                    task.sleep(settle_time)
            finally:
                controller.release(mouse.Button.left)

        def mouse_up(self, key="left"):
            # A press made before a focus change must still be released.
            from pynput import mouse
            mouse.Controller().release(self.get_mouse_button(key))

    interaction = DesktopDrag(manager.capture_method, window)
    if not interaction.clickable():
        window.bring_to_front()
        if not task.wait_until(interaction.clickable, time_out=3):
            raise RuntimeError("请将鸣潮窗口切到前台后再次启动任务。")
    return interaction
