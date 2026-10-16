# -*- coding: utf-8 -*-
"""test_updater — 更新执行逻辑单测（mock 宿主 install 流程）。"""

import asyncio
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checker  # noqa: E402
import updater  # noqa: E402
from checker import InstalledPlugin, UpdateItem  # noqa: E402


def make_item(pid="tp-old", mismatch=True):
    return UpdateItem(
        plugin_id=pid,
        name=pid,
        local_version="1.0.0",
        local_author="erick",
        market_id="@erickcharles/tp-old",
        market_owner="erickcharles",
        market_version="1.5.0",
        market_author="ErickCharles",
        source="https://platform.agentscope.io/plugins/erickcharles/tp-old/archive/zip/master",
        detail_url="https://platform.agentscope.io/plugins/tp-old",
        author_mismatch=mismatch,
    )


def make_report(items):
    return checker.CheckReport(
        checked_at=__import__("time").time(),
        total_installed=3,
        official_count=1,
        third_party_count=3,
        updates=items,
    )


class TestApplyUpdates(unittest.TestCase):
    def test_skip_mismatch_by_default(self):
        report = make_report([make_item("a", mismatch=True), make_item("b", mismatch=False)])

        async def fake_install(item, request=None):
            return {"ok": True, "message": "done"}

        with patch.object(
            updater, "_ensure_report", return_value=(report, False)
        ), patch.object(
            updater, "_install_one", side_effect=fake_install
        ):
            result = asyncio.run(updater.apply_updates())

        self.assertEqual([x["plugin_id"] for x in result["updated"]], ["b"])
        self.assertEqual(len(result["skipped_mismatch"]), 1)
        skip = result["skipped_mismatch"][0]
        self.assertEqual(skip["local_author"], "erick")
        self.assertEqual(skip["market_author"], "ErickCharles")
        self.assertIn("作者不一致", skip["hint"])
        self.assertIn("人工确认", skip["hint"])
        self.assertEqual(result["failed"], [])

    def test_confirm_mismatch_forces_update(self):
        report = make_report([make_item("a", mismatch=True)])

        async def fake_install(item, request=None):
            return {"ok": True, "message": "1.0.0 → 1.5.0"}

        with patch.object(updater, "_ensure_report", return_value=(report, False)), patch.object(
            updater, "_install_one", side_effect=fake_install
        ):
            result = asyncio.run(updater.apply_updates(confirm_mismatch=True))

        self.assertEqual([x["plugin_id"] for x in result["updated"]], ["a"])
        self.assertEqual(result["skipped_mismatch"], [])

    def test_plugin_ids_filter_and_unknown(self):
        report = make_report([make_item("a", mismatch=False), make_item("b", mismatch=False)])

        async def fake_install(item, request=None):
            return {"ok": True, "message": "ok"}

        with patch.object(updater, "_ensure_report", return_value=(report, False)), patch.object(
            updater, "_install_one", side_effect=fake_install
        ):
            result = asyncio.run(updater.apply_updates(plugin_ids=["a", "zzz"]))

        self.assertEqual([x["plugin_id"] for x in result["updated"]], ["a"])
        self.assertEqual(result["unknown_ids"], ["zzz"])

    def test_install_failure_collected(self):
        report = make_report([make_item("a", mismatch=False)])

        async def fake_install(item, request=None):
            return {"ok": False, "message": "下载失败"}

        with patch.object(updater, "_ensure_report", return_value=(report, False)), patch.object(
            updater, "_install_one", side_effect=fake_install
        ):
            result = asyncio.run(updater.apply_updates())

        self.assertEqual(result["updated"], [])
        self.assertEqual(result["failed"][0]["plugin_id"], "a")
        self.assertIn("下载失败", result["failed"][0]["message"])

    def test_resolve_request_shim(self):
        fake_app = MagicMock()
        fake_app.state.plugin_loader = MagicMock()
        with patch(
            "qwenpaw.plugins.registry.PluginRegistry"
        ) as MockReg:
            MockReg.return_value.get_plugin_http_app.return_value = fake_app
            req = updater._resolve_request(None)
            self.assertIs(req.app, fake_app)

    def test_resolve_request_passthrough(self):
        sentinel = object()
        self.assertIs(updater._resolve_request(sentinel), sentinel)


class TestApplyBusy(unittest.TestCase):
    """幂等保护：同一时刻只允许一个 apply 批次在跑。"""

    def test_concurrent_apply_raises_busy(self):
        async def slow_ensure(force_refresh):
            await asyncio.sleep(0.05)
            return make_report([]), True

        async def scenario():
            with patch.object(
                updater, "_ensure_report", side_effect=slow_ensure
            ):
                t1 = asyncio.create_task(updater.apply_updates())
                await asyncio.sleep(0.01)  # 让 t1 置位 busy 标志
                with self.assertRaises(updater.BusyError):
                    await updater.apply_updates()
                await t1  # 第一个正常完成，标志复位
                # 完成后可再次执行（不残留 busy）
                return await updater.apply_updates()

        result = asyncio.run(scenario())
        self.assertEqual(result["updated"], [])

    def test_busy_message(self):
        err = updater.BusyError("正在更新插件，请等待当前更新完成后再试")
        self.assertIn("正在更新", str(err))
        self.assertIn("等待", str(err))

    def test_busy_flag_reset_after_failure(self):
        async def boom(force_refresh):
            raise RuntimeError("boom")

        with patch.object(updater, "_ensure_report", side_effect=boom):
            with self.assertRaises(RuntimeError):
                asyncio.run(updater.apply_updates())
        # 失败后标志必须复位（finally 保证）
        self.assertFalse(updater._APPLYING)


class TestInstallOne(unittest.TestCase):
    """_install_one 直测：必须走官方 install_plugin_source 全流程（M1）。"""

    def _item(self):
        return make_item("tp-a", mismatch=False)

    def test_uses_official_install_plugin_source(self):
        fake_record = MagicMock()
        fake_record.manifest.version = "1.5.0"
        fake_app = MagicMock()
        fake_app.state.plugin_loader = MagicMock()
        fake_request = SimpleNamespace(app=fake_app)

        async def fake_install_source(source, *, app, force, **kw):
            self.assertIs(app, fake_app)
            self.assertEqual(source, self._item().source)
            self.assertTrue(force)
            return fake_record

        with patch(
            "qwenpaw.app.routers.plugins.install_plugin_source",
            side_effect=fake_install_source,
        ):
            result = asyncio.run(updater._install_one(self._item(), fake_request))
        self.assertTrue(result["ok"])
        self.assertIn("1.0.0 → 1.5.0", result["message"])

    def test_install_source_failure_collected(self):
        async def boom(source, *, app, force, **kw):
            raise RuntimeError("下载失败")

        fake_app = MagicMock()
        fake_app.state.plugin_loader = MagicMock()
        fake_request = SimpleNamespace(app=fake_app)
        with patch(
            "qwenpaw.app.routers.plugins.install_plugin_source",
            side_effect=boom,
        ):
            result = asyncio.run(updater._install_one(self._item(), fake_request))
        self.assertFalse(result["ok"])
        self.assertIn("下载失败", result["message"])

    def test_apply_invalidates_report_cache(self):
        """批次结束后必须作废报告缓存（m2：防已更新项被重复消费）。"""
        report = make_report([make_item("a", mismatch=False)])

        async def fake_install(item, request=None):
            return {"ok": True, "message": "ok"}

        with patch.object(
            updater, "_ensure_report", return_value=(report, False)
        ), patch.object(
            updater, "_install_one", side_effect=fake_install
        ), patch.object(
            updater, "invalidate_report", wraps=updater.invalidate_report
        ) as inv:
            asyncio.run(updater.apply_updates())
        inv.assert_called_once()

    def test_report_stale_reflects_cache_age(self):
        """report_stale 真实化：缓存超半程才提示（m1：旧版恒 False 死字段）。"""
        import time as _time
        report = make_report([])
        report.checked_at = _time.time() - updater.APPLY_CACHE_TTL * 0.75

        with patch.object(
            updater, "_ensure_report", return_value=(report, True)
        ), patch.object(updater, "_install_one"):
            result = asyncio.run(updater.apply_updates())
        self.assertTrue(result["report_stale"])


if __name__ == "__main__":
    unittest.main()
