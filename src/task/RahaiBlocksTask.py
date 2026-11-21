import re

from ok import BaseTask

from extensions.rahai_blast import blast_observation
from extensions.rahai_challenge import configure_stage_mechanics, dismiss_rahai_guide, return_to_stage_list
from extensions.rahai_fault import fault_mask
from extensions.rahai_input import BoardHidden, drag_interaction, placement_frame
from src.task.rahai_blocks import HEIGHT, WIDTH, canonical_frame, choose_move, clear_lines, preview_matches, read_board
from src.task.task_icons import task_icon


STAGE_PROGRESS = re.compile(r"进行中.*?(\d+)\s*[/／]\s*(\d+)")
STAGE_STATUS = re.compile(r"进行中|已(?:通关|完成)|(?:\d+\s*(?:天|小时|分钟))")


class ChallengeFailed(RuntimeError):
    pass


class RahaiBlocksTask(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "3.8 拉海洛方块·逆转"
        self.description = "从简体中文的活动关卡列表开始，挑战所有已解锁且未通关的关卡。"
        self.group_name = "Daily Activities"
        self.group_icon = task_icon("category-daily")
        self.supported_languages = ["zh_CN"]

    def _event_title(self):
        return self.ocr(.045, .04, .2, .09, match=re.compile(r"拉海洛方块"))

    def _stage_list(self):
        if not self._event_title():
            return []
        boxes = self.ocr(.045, .18, .965, .79)
        stages = []
        for status in boxes:
            y = status.center()[1] / self.height
            if not (.39 < y < .44 or .74 < y < .79) or not STAGE_STATUS.search(status.name):
                continue
            titles = [box for box in boxes if .18 < (status.y - box.y) / self.height < .23
                      and abs(status.x - box.x) < .025 * self.width]
            if titles:
                title = min(titles, key=lambda box: abs(status.x - box.x))
                stages.append((title, status))
        return sorted(stages, key=lambda stage: stage[1].x)

    def _challenge_button(self):
        boxes = self.ocr(.69, .89, .96, .95, match="开始挑战")
        return boxes[0] if boxes else None

    def _stage_details(self):
        return self._event_title() and self.ocr(.68, .43, .82, .48, match="关卡目标")

    def _scroll_stages(self, interaction, direction, stages):
        if not interaction.clickable():
            raise RuntimeError("游戏画面暂时无法接收拖拽操作。")
        left = round(self.width * .34)
        right = round(self.width * .81)
        y = round(self.height * .49)
        start, end = (right, left) if direction > 0 else (left, right)
        interaction.swipe(start, y, end, y, 650)
        self.sleep(.55)
        stages_after = self.wait_until(self._stage_list, time_out=5)
        if not stages_after:
            raise RuntimeError("滑动后无法识别拉海洛方块关卡列表。")
        moved = len(stages_after) != len(stages) or any(
            abs(before.x - after.x) > .005 * self.width
            for (_, before), (_, after) in zip(stages, stages_after)
        )
        if not moved:
            def titles(page):
                return sorted("".join(title.name.split()) for title, _ in page)

            # A whole page can snap to the same card positions. Confirm new
            # titles on a second frame so a transient OCR change is ignored.
            new_titles = titles(stages_after)
            if new_titles != titles(stages):
                self.next_frame()
                confirmed = self._stage_list()
                if confirmed:
                    moved = titles(confirmed) == new_titles
                    stages_after = confirmed
        return stages_after, moved

    def _first_page(self, interaction, stages):
        for _ in range(12):
            stages, moved = self._scroll_stages(interaction, -1, stages)
            if not moved:
                return stages
        raise RuntimeError("无法定位到拉海洛方块关卡列表起点。")

    def _return_to_list(self):
        stages = return_to_stage_list(self)
        if not stages:
            raise RuntimeError("挑战结束后无法返回关卡列表。")
        return stages

    def _leave_button(self):
        if self.ocr(.33, .45, .67, .52, match=re.compile(r"退出挑战|关卡进度")):
            return self.ocr(.60, .59, .71, .67, match="离开")
        return []

    def _leave_challenge(self):
        if self._stage_details():
            return
        leave = self._leave_button()
        if not leave and self._event_title() and (self._read_board() or self._score()):
            self.click(.942, .06, after_sleep=.45)
            leave = self.wait_until(self._leave_button, time_out=5)
        if not leave:
            raise RuntimeError("无法确认退出挑战界面。")
        self.click(leave[0], after_sleep=.45)
        if not self.wait_until(self._stage_details, time_out=5):
            raise RuntimeError("退出挑战后无法返回关卡详情。")

    def _challenge(self, interaction, title, status, completed):
        self.click(status, after_sleep=.45)
        if not self.wait_until(self._stage_details, time_out=5):
            raise RuntimeError(f"无法打开关卡「{title.name}」。")

        difficulty = ("简单", "困难")[completed]
        configure_stage_mechanics(self, title.name, difficulty)

        def selected():
            return self.ocr(.68, .125, .96, .181, match=re.compile(difficulty + r"$"))

        if not selected():
            self.click(.085, (.189, .276)[completed], after_sleep=.35)
            if not self.wait_until(selected, time_out=4):
                raise ChallengeFailed(f"无法选择{difficulty}难度。")
        if not self.ocr(.68, .48, .88, .55, match=re.compile(r"积分|奖章")):
            raise ChallengeFailed("无法识别关卡目标。")
        start = self._challenge_button()
        if not start:
            raise ChallengeFailed("无法识别开始挑战按钮。")

        self.log_info(f"开始挑战：{title.name} · {difficulty}")
        for _ in range(2):
            self.click(start, down_time=.09, after_sleep=1.3)
            self.next_frame()
            start = self._challenge_button()
            if not start:
                break
        else:
            raise ChallengeFailed("开始挑战按钮未响应。")
        try:
            if not self.wait_until(self._ready_board, time_out=15):
                raise ChallengeFailed("无法识别棋盘。")
            self._play_board(interaction)
        except ChallengeFailed as exc:
            try:
                self._leave_challenge()
            except RuntimeError as leave_error:
                raise RuntimeError(f"{exc} {leave_error}") from leave_error
            raise
        self.click(.5, .88, after_sleep=.6)
        if not self.wait_until(self._stage_details, time_out=5):
            raise RuntimeError("领取奖励后无法识别关卡详情。")
        self.log_info(f"已通关：{title.name} · {difficulty}")

    def run(self):
        self.next_frame()
        stages = self.wait_until(self._stage_list, time_out=5)
        if not stages:
            raise RuntimeError("请先打开简体中文的「拉海洛方块·逆转」活动关卡列表。")
        interaction = drag_interaction(self)

        stages = self._first_page(interaction, stages)
        attempted = set()
        failures = []
        cleared = 0
        for _ in range(32):
            for title, status in stages:
                progress = STAGE_PROGRESS.search(status.name)
                if not progress:
                    continue
                completed, total = map(int, progress.groups())
                attempt = (title.name, completed)
                if total != 2 or completed >= total or attempt in attempted:
                    continue
                attempted.add(attempt)
                try:
                    self._challenge(interaction, title, status, completed)
                    cleared += 1
                except ChallengeFailed as exc:
                    failures.append(title.name)
                    self.log_warning(f"{title.name}：{exc}")
                stages = self._return_to_list()
                break
            else:
                stages, moved = self._scroll_stages(interaction, 1, stages)
                if not moved:
                    self._first_page(interaction, stages)
                    if failures:
                        raise RuntimeError(f"已通关 {cleared} 个难度，未完成：{'、'.join(failures)}")
                    self.log_info(f"已通关 {cleared} 个难度，所有已解锁关卡已检查。", notify=True)
                    return
        raise RuntimeError("扫描关卡列表达到上限。")

    def _read_board(self):
        return read_board(canonical_frame(self.frame))

    def _playable_board(self):
        board = self._read_board()
        return board if board and board.pieces else None

    def _score(self):
        texts = self.ocr(x=.09, y=.125, to_x=.235, to_y=.181)
        for box in texts:
            if match := re.search(r"(\d+)\s*[/／]\s*(\d+)", box.name):
                score, goal = map(int, match.groups())
                if goal > 0:
                    return score, goal
        return None

    def _reward(self, frame=None):
        return self.ocr(.36, .216, .64, .29, match=re.compile(r"^获\s*得$"), frame=frame)

    def _ready_board(self):
        # The first score challenge includes a manual and three guided clicks.
        if dismiss_rahai_guide(self):
            return False

        for region, hint, target in (
            ((.54, .39, .81, .47), "选择右侧方块", (.863, .444)),
            ((.28, .28, .72, .37), "方块旋转后填入空格", (.50, .48)),
            ((.035, .23, .37, .30), "方块消除后将获得积分", (.16, .16)),
        ):
            if self.ocr(*region, match=re.compile(hint)):
                self.click(*target, after_sleep=.5)
                return False

        return self._playable_board()

    def _place(self, interaction, board, move):
        piece = next(p for p in board.pieces if p.slot == move.slot)
        height, width = self.frame.shape[:2]

        def point(xy):
            return round(xy[0] * width / WIDTH), round(xy[1] * height / HEIGHT)

        def move_cursor(xy):
            if not interaction.clickable():
                raise RuntimeError("Game lost foreground focus during a drag")
            interaction.move(*point(xy))

        source = piece.pickup
        dx, dy = move.drop
        placement_frame(self)
        move_cursor(source)
        self.sleep(.12)
        placement_frame(self)
        interaction.mouse_down(*point(source))
        return_to_tray = True
        try:
            # Trigger the drag in the tray, but rotate over the board where
            # the game reliably accepts R even for pieces near the tray edge.
            self.sleep(.10)
            placement_frame(self)
            move_cursor((source[0] - 16, source[1]))
            self.sleep(.12)
            for i in range(1, 9):
                move_cursor((source[0] + (dx - source[0]) * i / 8, source[1] + (dy - source[1]) * i / 8))
                self.sleep(.025)
            self.sleep(.15)

            def rotate():
                placement_frame(self)
                if not interaction.clickable():
                    raise RuntimeError("Game lost foreground focus during a drag")
                interaction.send_key('r', down_time=.08)
                move_cursor((dx + 5, dy))
                self.sleep(.13)

            for _ in range(move.turns):
                rotate()
            # Retry a full rotation cycle if a key press was missed.
            for attempt in range(4):
                if attempt:
                    rotate()
                for ox, oy in ((0, 0), (0, -12), (-12, 0), (12, 0), (0, 12)):
                    move_cursor((dx + ox, dy + oy))
                    self.sleep(.16)
                    if interaction.clickable() and preview_matches(placement_frame(self), board, move):
                        return_to_tray = False
                        return True
            self.log_warning(f"Placement preview did not match: slot={move.slot}, turns={move.turns}, "
                             f"row={move.row}, col={move.col}")
            self.screenshot("rahai_preview_mismatch")
            return False
        except BoardHidden:
            return_to_tray = False
            raise
        finally:
            try:
                if return_to_tray and interaction.clickable():
                    # No task sleep here: cancellation must still release the button.
                    interaction.move(*point(source))
            finally:
                interaction.mouse_up()

    def _play_board(self, interaction):
        misses = 0
        rejected = set()
        for _ in range(160):
            self.next_frame()
            board = self._playable_board()
            if not board:
                if self._reward():
                    return
                # A first-visit guide can cover a board that was already ready.
                board = self.wait_until(self._ready_board, time_out=6)
                if not board:
                    if self._reward():
                        return
                    raise ChallengeFailed("棋盘消失，未确认通关。")
            if not interaction.clickable():
                raise RuntimeError("Game lost foreground focus; no further moves were sent.")
            move = choose_move(board, excluded=rejected)
            if move is None:
                raise ChallengeFailed("剩余方块无合法落点。")
            expected, lines = clear_lines(board.mask | move.mask, board.fixed)
            faults = 0
            if lines and getattr(self, "_rahai_fault_stage", False) is True:
                faults = fault_mask(canonical_frame(self.frame)) & board.mask & ~board.fixed
            try:
                placed = self._place(interaction, board, move)
            except BoardHidden as interrupted:
                if interrupted.reward_visible:
                    return
                continue
            if not placed:
                misses += 1
                rejected.add((move.slot, move.mask))
                if misses >= 3:
                    raise ChallengeFailed("连续三次未能匹配落点预览。")
                self.sleep(.35)
                continue
            misses = 0
            rejected.clear()
            self.log_info(f"Rahai Blocks: slot={move.slot + 1}, rotation={move.turns * 90}, "
                          f"cell=({move.row + 1},{move.col + 1}), lines={lines}")
            self.sleep(.95 if lines else .38)
            previous_blast = None
            for _ in range(4):
                self.next_frame()
                actual = self._read_board()
                if actual and actual.mask == expected and actual.fixed == board.fixed and not (faults & ~expected):
                    break
                if lines and (faults or getattr(self, "_rahai_blast_stage", False)):
                    blast = blast_observation(board, move, expected, actual, fault_mask=faults)
                    if blast is not None and blast == previous_blast:
                        self.log_info(f"Rahai Blocks: extra cleared={(expected & ~actual.mask).bit_count()}, "
                                      f"fault tiles retained={(actual.mask & ~expected).bit_count()}")
                        break
                    previous_blast = blast
                self.sleep(.23)
            else:
                if lines and self.wait_until(self._reward, time_out=3):
                    return
                self.log_warning(f"Rahai Blocks: expected={expected:#018x}, "
                                 f"actual={hex(actual.mask) if actual else 'unrecognized'}")
                raise ChallengeFailed("落块后棋盘与预期状态不符。")
        raise ChallengeFailed("达到落块次数上限，未确认通关。")
