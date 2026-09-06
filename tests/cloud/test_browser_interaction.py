from __future__ import annotations

import pytest

from extensions.cloud.browser_interaction import PlaywrightBrowserInteraction


class FakeKeyboard:
    def __init__(self, events):
        self.events = events

    def press(self, key, *, delay):
        self.events.append(("key_press", key, delay))

    def down(self, key):
        self.events.append(("key_down", key))

    def up(self, key):
        self.events.append(("key_up", key))

    def type(self, text):
        self.events.append(("key_type", text))


class FakeMouse:
    def __init__(self, events):
        self.events = events

    def move(self, x, y):
        self.events.append(("mouse_move", x, y))

    def down(self, *, button):
        self.events.append(("mouse_down", button))

    def up(self, *, button):
        self.events.append(("mouse_up", button))

    def click(self, x, y, *, button, delay):
        self.events.append(("mouse_click", x, y, button, delay))

    def wheel(self, x, y):
        self.events.append(("mouse_wheel", x, y))


class FakePage:
    def __init__(self):
        self.events = []
        self.keyboard = FakeKeyboard(self.events)
        self.mouse = FakeMouse(self.events)

    def wait_for_timeout(self, milliseconds):
        self.events.append(("wait", milliseconds))


class PageCaller:
    def __init__(self, page):
        self.page = page
        self.calls = 0

    def __call__(self, callback):
        self.calls += 1
        return callback(self.page)


def make_interaction(*, coordinate_size=(1920, 1080), viewport=(1280, 720)):
    page = FakePage()
    page_call = PageCaller(page)
    interaction = PlaywrightBrowserInteraction(
        page_call,
        coordinate_size=coordinate_size,
        viewport=viewport,
    )
    return page, page_call, interaction


def test_keyboard_actions_are_submitted_through_page_call():
    page, page_call, interaction = make_interaction()

    interaction.send_key("esc", down_time=0.025)
    interaction.send_key("f2")
    interaction.send_key_down("CTRL_L")
    interaction.send_key_up("ctrl_l")

    assert page_call.calls == 4
    assert page.events == [
        ("key_press", "Escape", 25.0),
        ("key_press", "F2", 20.0),
        ("key_down", "ControlLeft"),
        ("key_up", "ControlLeft"),
    ]


def test_release_all_keys_uses_reverse_press_order_and_clears_state():
    page, _, interaction = make_interaction()
    interaction.send_key_down("shift")
    interaction.send_key_down("w")

    interaction.release_all_keys()
    interaction.release_all_keys()

    assert page.events[-2:] == [("key_up", "w"), ("key_up", "Shift")]


def test_mouse_coordinates_are_scaled_and_clamped_to_fixed_viewport():
    page, _, interaction = make_interaction()

    interaction.move(960, 540)
    interaction.mouse_down(1920, 1080, key="right")
    interaction.mouse_up(key="right")
    interaction.click(-100, 2000, key="middle", down_time=0.03)

    assert page.events == [
        ("mouse_move", 640, 360),
        ("mouse_move", 1279, 719),
        ("mouse_down", "right"),
        ("mouse_up", "right"),
        ("mouse_click", 0, 719, "middle", 30.0),
    ]


def test_click_without_coordinates_uses_current_pointer_and_move_false_uses_coordinates():
    page, _, interaction = make_interaction()

    interaction.click(down_time=0.02)
    interaction.click(200, 100, move=False, down_time=0)

    assert page.events == [
        ("mouse_down", "left"),
        ("wait", 20.0),
        ("mouse_up", "left"),
        ("mouse_click", 133, 67, "left", 0.0),
    ]


def test_click_can_restore_previous_pointer_position():
    page, _, interaction = make_interaction()
    interaction.move(300, 300)

    interaction.click(900, 600, move_back=True)

    assert page.events[-2:] == [
        ("mouse_click", 600, 400, "left", 10.0),
        ("mouse_move", 200, 200),
    ]


@pytest.mark.parametrize(
    ("x", "name", "expected_x"),
    [
        (1215, "boss_proceed", 1119),
        (946, "confirm_btn_hcenter_vcenter", 850),
        (1195, "team_start_challenge", 1099),
        (317, "cancel_button_hcenter_vcenter", 413),
        (303, "claim_cancel_button_hcenter_vcenter", 399),
        (369, "skip_dialog_confirm", 465),
    ],
)
def test_named_edge_action_clicks_inside_streamed_button_body(x, name, expected_x):
    page, _, interaction = make_interaction(coordinate_size=(1280, 720))

    interaction.click(x, 238, name=name, move=False)

    assert page.events == [("mouse_click", expected_x, 238, "left", 10.0)]


def test_coordinate_click_tracks_pointer_even_when_move_is_false():
    _, _, interaction = make_interaction()

    interaction.click(200, 100, move=False, down_time=0)

    assert interaction._cursor_position == (133, 67)


def test_scroll_maps_optional_position_and_preserves_framework_direction():
    page, _, interaction = make_interaction()

    interaction.scroll(960, 540, 3)
    interaction.scroll(-1, -1, -2)

    assert page.events == [
        ("mouse_move", 640, 360),
        ("mouse_wheel", 0, -300.0),
        ("mouse_wheel", 0, 200.0),
    ]


def test_framework_helpers_type_text_and_swipe_through_page_call():
    page, _, interaction = make_interaction(coordinate_size=(1280, 720))

    interaction.input_text("hello")
    interaction.swipe(100, 200, 300, 400, duration=100, settle_time=0.05)

    assert page.events[0] == ("key_type", "hello")
    assert page.events[1:3] == [
        ("mouse_move", 100, 200),
        ("mouse_down", "left"),
    ]
    assert page.events[-2:] == [("wait", 50.0), ("mouse_up", "left")]
    assert interaction.should_capture()


def test_release_all_releases_buttons_and_keys_before_shutdown():
    page, _, interaction = make_interaction()
    interaction.send_key_down("w")
    interaction.send_key_down("shift")
    interaction.mouse_down(key="right")

    interaction.on_destroy()
    interaction.on_destroy()

    assert page.events[-3:] == [
        ("mouse_up", "right"),
        ("key_up", "Shift"),
        ("key_up", "w"),
    ]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"coordinate_size": (0, 720)}, "coordinate_size"),
        ({"viewport": (1280, -1)}, "viewport"),
    ],
)
def test_invalid_sizes_are_rejected(args, message):
    with pytest.raises(ValueError, match=message):
        PlaywrightBrowserInteraction(lambda callback: None, **args)


def test_invalid_input_is_rejected_before_page_call():
    _, page_call, interaction = make_interaction()

    with pytest.raises(ValueError, match="both"):
        interaction.move(1, 1)
        interaction.mouse_down(10, -1)
    with pytest.raises(ValueError, match="unsupported"):
        interaction.mouse_up(key="side")
    with pytest.raises(ValueError, match="non-negative"):
        interaction.send_key("w", down_time=-1)

    assert page_call.calls == 1
