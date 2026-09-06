class WWOneTimeTask:

    def run(self):
        if not self.is_browser():
            # MouseResetTask and PostMessageInteraction are Windows-only.  A
            # cloud task neither needs the cursor reset loop nor a native
            # window activation, so keep those modules out of the headless
            # import and execution path.
            from ok import PostMessageInteraction
            from src.task.MouseResetTask import MouseResetTask

            mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
            mouse_reset_task.run()
            if isinstance(self.executor.interaction, PostMessageInteraction):
                self.executor.interaction.activate()
        self.sleep(0.5)
