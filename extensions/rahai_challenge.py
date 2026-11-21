"""Recognize the activity's in-game instructions, including delayed first-visit guides."""

import re


GUIDE_TITLE = re.compile(r"积分模式|奖章模式|[\u4e00-\u9fff0-9０-９]{1,10}方块$")


def configure_stage_mechanics(task, title, difficulty):
    # Returning players skip the first-visit instructions.
    name = "".join(title.split())
    task._rahai_blast_stage = name in ("与你的黄昏", "虚诞之地")
    task._rahai_fault_stage = name == "虚诞之地" and difficulty == "困难"


def dismiss_rahai_guide(task):
    titles = task.ocr(.035, .28, .32, .35, match=GUIDE_TITLE)
    if not titles:
        return False
    if any("爆破方块" in title.name for title in titles):
        task._rahai_blast_stage = True
    if any("故障方块" in title.name for title in titles):
        task._rahai_fault_stage = True
    confirm = task.ocr(.41, .88, .59, .95, match="确认")
    if confirm:
        task.click(confirm[0], after_sleep=.5)
    elif task.ocr(.38, .90, .62, .96, match=re.compile(r"切换至最后一页")):
        task.click(.75, .805, after_sleep=.5)
    return True


def return_to_stage_list(task):
    closed_details = False
    opened_event = False

    def observe():
        nonlocal closed_details, opened_event
        if task._stage_list():
            # A single list frame can flash between the details and overview.
            task.sleep(.3)
            task.next_frame()
            return task._stage_list()
        if not opened_event and task.ocr(.74, .11, .99, .20, match=re.compile(r"拉海洛方块.*逆转")):
            enter = task.ocr(.76, .88, .98, .96, match="立即前往")
            if enter:
                opened_event = True
                task.click(enter[0], after_sleep=.6)
                return False
        if not closed_details and task._stage_details():
            closed_details = True
            task.click(.942, .06, after_sleep=.45)
        return False

    return task.wait_until(observe, time_out=10)
