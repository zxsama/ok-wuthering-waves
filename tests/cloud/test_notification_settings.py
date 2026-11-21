import asyncio
import json
from types import SimpleNamespace

import httpx
from ok.util.config import Config
from ok.util.GlobalConfig import create_notification_options

from extensions.cloud.management_web.app import create_web_app


def test_notification_credentials_are_write_only_across_reads_writes_and_resets(tmp_path):
    option = create_notification_options()
    providers = {
        "Discord Webhook": "Discord Notification",
        "Telegram Bot Token": "Telegram Notification",
        "Enterprise WeChat Webhook URL": "Enterprise WeChat Webhook Notification",
        "QQ Bot API Token": "QQ Bot API Notification",
        "SMTP Password": "SMTP Notification",
    }
    originals = {key: f"synthetic-original-{i}" for i, key in enumerate(providers)}
    replacement = "synthetic-replacement"
    config = Config(option.name, option.default_config, folder=str(tmp_path))
    for key, parent in providers.items():
        config[parent] = True
        config[key] = originals[key]
    global_config = SimpleNamespace(
        get_config=lambda name: config,
        get_all_visible_configs=lambda: [(option.name, config, option)],
    )
    executor = SimpleNamespace(global_config=global_config, trigger_tasks=[], onetime_tasks=[],
                               get_all_tasks=lambda: [])
    instance = SimpleNamespace(config={}, task_executor=executor, task_manager=SimpleNamespace())
    app = create_web_app({}, ok_instance=instance)

    def stored(key):
        return json.loads((tmp_path / "Notification.json").read_text(encoding="utf8"))[key]

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            async def request(method, path, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert all(secret not in response.text for secret in [*originals.values(), replacement])
                return response

            async def save(key, value):
                response = await request("POST", "/api/settings/Notification/config", json={"key": key, "value": value})
                response.raise_for_status()
                return {field["key"]: field for field in response.json()["fields"]}

            for path in ("/", "/api/navigation"):
                (await request("GET", path)).raise_for_status()
            settings = (await request("GET", "/api/settings")).json()[0]
            fields = {field["key"]: field for field in settings["fields"]}
            for key in providers:
                assert fields[key]["kind"] == "secret" and fields[key]["configured"] is True
                assert "value" not in fields[key] and "default" not in fields[key]
                assert (await save(key, ""))[key]["configured"] and stored(key) == originals[key]
                assert (await save(key, replacement))[key]["configured"] and stored(key) == replacement
                assert (await save(key, {"clear": True}))[key]["configured"] is False and stored(key) == ""
            bad = await request("POST", "/api/settings/Notification/config",
                                json={"key": "SMTP Password", "value": {"clear": replacement}})
            assert bad.status_code == 400
            assert (await save("SMTP Port", 465))["SMTP Port"]["value"] == stored("SMTP Port") == 465
            await save("SMTP Password", replacement)
            assert "SMTP Password" not in await save("SMTP Notification", False)
            assert (await save("SMTP Notification", True))["SMTP Password"]["configured"]
            (await request("POST", "/api/settings/Notification/reset")).raise_for_status()
            assert stored("SMTP Password") == "" and stored("SMTP Port") == 587

    asyncio.run(exercise())
