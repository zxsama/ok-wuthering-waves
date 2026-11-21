"""Select the input backend that can hold a piece while sending rotation keys."""


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
