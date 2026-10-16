# -*- coding: utf-8 -*-
"""test_checker — 检查逻辑单测（纯逻辑 + mock 网络层）。"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checker  # noqa: E402
from checker import InstalledPlugin  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_normalize_id(self):
        self.assertEqual(checker.normalize_id("@Owner/Name "), "owner/name")
        self.assertEqual(checker.normalize_id("Plain-Id"), "plain-id")

    def test_short_name(self):
        self.assertEqual(checker.short_name("@onesixmix/file-tools"), "file-tools")
        self.assertEqual(checker.short_name("bare"), "bare")

    def test_normalize_author(self):
        self.assertEqual(checker.normalize_author(" @Erick "), "erick")


class TestCompareVersions(unittest.TestCase):
    def test_semver(self):
        self.assertEqual(checker.compare_versions("1.0.0", "1.1.0"), -1)
        self.assertEqual(checker.compare_versions("1.2.0", "1.1.0"), 1)
        self.assertEqual(checker.compare_versions("1.0.0", "1.0.0"), 0)

    def test_v_prefix_and_prerelease(self):
        self.assertEqual(checker.compare_versions("v1.0.0", "1.0.1"), -1)
        self.assertLess(checker.compare_versions("1.0.0rc1", "1.0.0"), 0)

    def test_invalid_fallback_official_semantics(self):
        """非 semver 退化路径对齐官方 _is_upgrade_available：不等即可更新。"""
        self.assertEqual(checker.compare_versions("abc", "abc"), 0)
        self.assertEqual(checker.compare_versions("abc", "abd"), -1)
        # 字符串序会错判的例子：非 semver 下不得判 local 更高
        self.assertEqual(checker.compare_versions("zzz", "aaa"), -1)

    def test_mixed_valid_invalid(self):
        """一侧可解析一侧不可解析：走退化路径（不等即可更新）。"""
        self.assertEqual(checker.compare_versions("1.0.0", "not-semver"), -1)
        self.assertEqual(checker.compare_versions("not-semver", "1.0.0"), -1)

    def test_empty(self):
        self.assertEqual(checker.compare_versions("", "1.0.0"), 0)
        self.assertEqual(checker.compare_versions("1.0.0", ""), 0)


class TestMatchMarketEntries(unittest.TestCase):
    def setUp(self):
        self.local = InstalledPlugin(
            id="file-tools", name="F", version="1.0.0", author="a", dir_name="f"
        )

    def test_unique_match(self):
        entries = [{"id": "@owner/other"}, {"id": "@any/file-tools"}]
        entry, multi = checker._match_market_entries(self.local, entries)
        self.assertIsNotNone(entry)
        self.assertEqual(multi, [])

    def test_no_match(self):
        entry, multi = checker._match_market_entries(
            self.local, [{"id": "@owner/other"}]
        )
        self.assertIsNone(entry)
        self.assertEqual(multi, [])

    def test_ambiguous(self):
        entries = [
            {"id": "@a/file-tools", "version": "1.1"},
            {"id": "@b/file-tools", "version": "1.2"},
        ]
        entry, multi = checker._match_market_entries(self.local, entries)
        self.assertIsNone(entry)
        self.assertEqual(len(multi), 2)


class TestAuthorMismatch(unittest.TestCase):
    def test_mismatch(self):
        local = InstalledPlugin(
            id="p", name="P", version="1", author="onesixth", dir_name="p"
        )
        entry = {"owner": "onesixmix", "developer": "ONESIXMIX"}
        self.assertTrue(checker.check_author_mismatch(local, entry))

    def test_owner_match(self):
        local = InstalledPlugin(
            id="p", name="P", version="1", author="@Archigram ", dir_name="p"
        )
        entry = {"owner": "archigram", "developer": ""}
        self.assertFalse(checker.check_author_mismatch(local, entry))

    def test_empty_local_author(self):
        local = InstalledPlugin(
            id="p", name="P", version="1", author="", dir_name="p"
        )
        self.assertFalse(checker.check_author_mismatch(local, {"owner": "x"}))


class TestCheckAll(unittest.TestCase):
    """check_all 主流程（mock 磁盘扫描 / 官方 catalog / 市场搜索）。"""

    def _installed(self):
        return [
            InstalledPlugin(
                id="official-a", name="A", version="1.0.0", author="q", dir_name="a"
            ),
            InstalledPlugin(
                id="tp-old", name="Old", version="1.0.0", author="erick", dir_name="o"
            ),
            InstalledPlugin(
                id="tp-new", name="New", version="2.0.0", author="x", dir_name="n"
            ),
            InstalledPlugin(
                id="tp-gone", name="Gone", version="0.1", author="y", dir_name="g"
            ),
        ]

    def test_check_all_report(self):
        installed = self._installed()
        market_results = {
            "tp-old": [
                {
                    "id": "@erickcharles/tp-old",
                    "owner": "erickcharles",
                    "developer": "ErickCharles",
                    "version": "1.5.0",
                }
            ],
            "tp-new": [
                {"id": "@x/tp-new", "owner": "x", "developer": "x", "version": "2.0.0"}
            ],
            "tp-gone": [{"id": "@someone/unrelated", "version": "9.9"}],
        }

        async def fake_search(term, page_size=20):
            return market_results.get(term, [])

        with patch.object(
            checker, "_read_installed_from_disk", return_value=installed
        ), patch.object(
            checker,
            "_fetch_official_ids",
            return_value={"official-a"},
        ), patch.object(
            checker, "_search_market", side_effect=fake_search
        ):
            import asyncio

            report = asyncio.run(checker.check_all())

        self.assertEqual(report.total_installed, 4)
        self.assertEqual(report.official_count, 1)
        self.assertEqual(report.third_party_count, 3)

        # tp-old：市场版本更高 + 作者不一致 → updates
        self.assertEqual(len(report.updates), 1)
        u = report.updates[0]
        self.assertEqual(u.plugin_id, "tp-old")
        self.assertEqual(u.local_version, "1.0.0")
        self.assertEqual(u.market_version, "1.5.0")
        self.assertTrue(u.author_mismatch)
        self.assertIn("/erickcharles/tp-old/", u.source)
        self.assertIn("https://platform.agentscope.io/plugins/tp-old", u.detail_url)

        # tp-new：同版本 → latest
        self.assertEqual(len(report.latest), 1)
        self.assertEqual(report.latest[0]["plugin_id"], "tp-new")

        # tp-gone：短名不匹配 → not_in_market
        self.assertEqual(len(report.not_in_market), 1)
        self.assertEqual(report.not_in_market[0]["plugin_id"], "tp-gone")

        # 全流程无错误
        self.assertEqual(report.errors, [])

    def test_check_all_search_error_collected(self):
        async def fake_search(term, page_size=20):
            raise RuntimeError("boom")

        with patch.object(
            checker, "_read_installed_from_disk", return_value=self._installed()
        ), patch.object(
            checker, "_fetch_official_ids", return_value={"official-a"}
        ), patch.object(
            checker, "_search_market", side_effect=fake_search
        ):
            import asyncio

            report = asyncio.run(checker.check_all())

        self.assertEqual(len(report.errors), 3)
        self.assertEqual(report.updates, [])

    def test_official_fetch_failure_aborts(self):
        async def raise_():
            raise RuntimeError("官方插件目录拉取失败：network down")

        with patch.object(
            checker, "_read_installed_from_disk", return_value=self._installed()
        ), patch.object(checker, "_fetch_official_ids", side_effect=raise_):
            import asyncio

            with self.assertRaises(RuntimeError):
                asyncio.run(checker.check_all())

    def test_report_cache_ttl(self):
        import asyncio
        import time

        async def fake_search(term, page_size=20):
            return []

        with patch.object(
            checker, "_read_installed_from_disk", return_value=self._installed()
        ), patch.object(
            checker, "_fetch_official_ids", return_value={"official-a"}
        ), patch.object(
            checker, "_search_market", side_effect=fake_search
        ):
            asyncio.run(checker.check_all())
            fresh = checker.last_report(max_age=600)
            self.assertIsNotNone(fresh)
            stale = checker.last_report(max_age=-1)
            self.assertIsNone(stale)

        # 时间推进后过期
        report = checker.last_report(max_age=600)
        if report is not None:
            report.checked_at = time.time() - 3600
            self.assertIsNone(checker.last_report(max_age=600))


class TestParseSearchBody(unittest.TestCase):
    """市场响应结构解析（m4：结构漂移抛错而非静默误报不在市场）。"""

    def test_normal(self):
        body = {"success": True, "data": {"total": 1, "plugins": [{"id": "@a/b"}]}}
        self.assertEqual(checker.parse_search_body(body), [{"id": "@a/b"}])

    def test_empty_plugins(self):
        body = {"success": True, "data": {"total": 0, "plugins": []}}
        self.assertEqual(checker.parse_search_body(body), [])

    def test_plugins_none_treated_as_empty(self):
        body = {"success": True, "data": {"total": 0}}
        self.assertEqual(checker.parse_search_body(body), [])

    def test_top_level_not_dict(self):
        with self.assertRaises(RuntimeError):
            checker.parse_search_body([1, 2])

    def test_data_not_dict(self):
        with self.assertRaises(RuntimeError):
            checker.parse_search_body({"data": "oops"})

    def test_plugins_not_list(self):
        with self.assertRaises(RuntimeError):
            checker.parse_search_body({"data": {"plugins": {"id": "x"}}})

    def test_success_false(self):
        with self.assertRaises(RuntimeError):
            checker.parse_search_body({"success": False, "message": "限流"})


class TestCheckBusy(unittest.TestCase):
    """幂等保护：同一时刻只允许一个 check 在跑。"""

    def test_concurrent_check_raises_busy(self):
        async def slow_search(term, page_size=20):
            await asyncio.sleep(0.05)
            return []

        async def scenario():
            with patch.object(
                checker,
                "_read_installed_from_disk",
                return_value=self._installed(),
            ), patch.object(
                checker, "_fetch_official_ids", return_value={"official-a"}
            ), patch.object(
                checker, "_search_market", side_effect=slow_search
            ):
                t1 = asyncio.create_task(checker.check_all())
                await asyncio.sleep(0.01)  # 让 t1 置位 busy 标志
                with self.assertRaises(checker.BusyError):
                    await checker.check_all()
                await t1  # 第一个正常完成，标志复位
                # 完成后可再次检查（不残留 busy）
                r = await checker.check_all()
                return r

        report = asyncio.run(scenario())
        self.assertEqual(report.third_party_count, 1)

    def _installed(self):
        return [
            InstalledPlugin(
                id="tp-a", name="A", version="1.0.0", author="x", dir_name="a"
            )
        ]

    def test_busy_message(self):
        err = checker.BusyError("正在检查插件更新")
        self.assertIn("正在检查", str(err))


if __name__ == "__main__":
    unittest.main()
