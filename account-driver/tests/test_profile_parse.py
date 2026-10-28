import unittest
from unittest.mock import MagicMock

from app.browser import (
    AppleFlow, extract_greeting_name, extract_profile_fields, normalize_profile_date,
    profile_matches, split_display_name,
    profile_option_matches,
)


class ProfileParseTest(unittest.TestCase):
    def test_name_punctuation_is_preserved_and_does_not_trigger_restore(self):
        for name in ("Fixture.", "O’Connor", "·Fixture·"):
            for text in (f"姓名 {name}\n", f"Name\n{name}\n"):
                self.assertEqual(name, extract_profile_fields(text)['name'])
        flow = AppleFlow()
        values = extract_profile_fields('姓名\nFixture.\n出生日期\n2000-01-01\n国家或地区\n美国\n语言\nEnglish')
        flow._read_account_profile = MagicMock(return_value=values)
        flow._restore_profile_name = MagicMock()
        result = flow._manage_account_profile(object(), values, 'fixture')
        flow._restore_profile_name.assert_not_called()
        self.assertEqual('unchanged', result['profile_results']['name']['status'])

    def test_extract_profile_same_line(self):
        text = (
            "个人信息\n"
            "姓名 张三\n"
            "国家或地区 中国\n"
            "语言 简体中文\n"
            "出生日期 2000-01-01\n"
        )
        fields = extract_profile_fields(text)
        self.assertEqual("张三", fields["name"])
        self.assertEqual("中国", fields["country"])
        self.assertEqual("简体中文", fields["language"])
        self.assertEqual("2000-01-01", normalize_profile_date(fields["dob"]))

    def test_extract_profile_english(self):
        text = (
            "Personal Information\n"
            "Name  Sam Chen\n"
            "Country or Region  United States\n"
            "Language  English\n"
        )
        fields = extract_profile_fields(text)
        self.assertEqual("Sam Chen", fields["name"])
        self.assertEqual("United States", fields["country"])
        self.assertEqual("English", fields["language"])

    def test_extract_profile_value_on_next_line(self):
        text = (
            "国家或地区\n"
            "中国\n"
            "语言\n"
            "简体中文\n"
        )
        fields = extract_profile_fields(text)
        self.assertEqual("中国", fields["country"])
        self.assertEqual("简体中文", fields["language"])

    def test_extract_profile_skips_button_noise(self):
        text = (
            "姓名\n"
            "李四\n"
            "编辑 更改 保存\n"
            "国家/地区 美国 编辑\n"
        )
        fields = extract_profile_fields(text)
        self.assertEqual("李四", fields["name"])
        self.assertEqual("美国", fields["country"])

    def test_missing_fields_stay_empty(self):
        # 只出现姓名时，其余字段保持空，而不是串到错误标签下。
        self.assertEqual(
            {"name": "张三", "dob": "", "country": "", "language": ""},
            extract_profile_fields("个人信息\n姓名 张三\n国家或地区 编辑 更改"),
        )

    def test_greeting_name_zh(self):
        self.assertEqual("王小明", extract_greeting_name("你好，王小明！今天想做什么？"))
        self.assertEqual("Alice", extract_greeting_name("Hello, Alice! What would you like to do today?"))
        self.assertEqual("", extract_greeting_name("没有问候语的页面内容"))

    def test_split_display_name(self):
        self.assertEqual(("Sam", "Chen"), split_display_name("Sam Chen"))
        self.assertEqual(("Alice", "Bob Smith"), split_display_name("Alice Bob Smith"))
        self.assertEqual(("张三", ""), split_display_name("张三"))
        self.assertEqual(("", ""), split_display_name(""))

    def test_profile_matches_normalizes_space_case(self):
        self.assertTrue(profile_matches("Sam Chen", "sam  chen"))
        self.assertTrue(profile_matches("中国", "中国"))
        self.assertFalse(profile_matches("Sam Chen", "Sam Lee"))

    def test_profile_option_matches_language_subtitle_variants(self):
        self.assertTrue(profile_option_matches("简体中文", "简体中文 - 中文（简体）"))
        self.assertTrue(profile_option_matches("简体中文 - 中文（简体）", "简体中文"))
        self.assertFalse(profile_option_matches("繁體中文", "简体中文 - 中文（简体）"))

    def test_profile_dates_from_training_page_formats(self):
        self.assertEqual("1984-12-17", normalize_profile_date("1984年12月17日"))
        self.assertEqual("1984-12-17", normalize_profile_date("December 17, 1984"))
        self.assertEqual("1984-12-17", normalize_profile_date("1984-12-17"))

    def test_saved_profile_target_restores_without_confirmation(self):
        flow = AppleFlow()
        flow._read_account_profile = MagicMock(return_value={
            "name": "被修改姓名", "dob": "1984-12-16",
            "country": "克罗地亚", "language": "泰语",
        })
        order = []
        flow._restore_profile_name = MagicMock(side_effect=lambda *args: order.append("name") or "restored")
        flow._restore_profile_dob = MagicMock(side_effect=lambda *args: order.append("dob") or "restored")
        flow._restore_profile_choice = MagicMock(side_effect=lambda _d, key, *_args: order.append(key) or "restored")
        flow._screenshot = MagicMock()
        result = flow._manage_account_profile(object(), {
            "name": "原姓名", "family_name": "原", "given_name": "姓名",
            "dob": "1984-12-17", "country": "美国",
            "language": "简体中文 - 中文（简体）", "confirmed": False,
        }, "fixture")
        self.assertEqual(["name", "dob", "language", "country"], order)
        self.assertTrue(all(item["status"] == "restored" for item in result["profile_results"].values()))

    def test_confirmed_baseline_restores_in_safe_order(self):
        flow = AppleFlow()
        flow._read_account_profile = MagicMock(return_value={
            "name": "被修改姓名", "dob": "1984-12-16",
            "country": "克罗地亚", "language": "泰语",
        })
        order = []
        flow._restore_profile_name = MagicMock(side_effect=lambda *args: order.append("name") or "restored")
        flow._restore_profile_dob = MagicMock(side_effect=lambda *args: order.append("dob") or "restored")
        flow._restore_profile_choice = MagicMock(side_effect=lambda _d, key, *_args: order.append(key) or ("manual_required" if key == "country" else "restored"))
        flow._screenshot = MagicMock()
        result = flow._manage_account_profile(object(), {
            "name": "原姓名", "family_name": "原", "given_name": "姓名",
            "dob": "1984-12-17", "country": "美国",
            "language": "简体中文 - 中文（简体）", "confirmed": True,
        }, "fixture")
        self.assertEqual(["name", "dob", "language", "country"], order)
        self.assertEqual("restored", result["profile_results"]["language"]["status"])
        self.assertEqual("manual_required", result["profile_results"]["country"]["status"])

    def test_country_payment_requirement_is_never_automated(self):
        self.assertTrue(AppleFlow._profile_country_requires_manual("请添加付款方式和账单地址"))
        self.assertTrue(AppleFlow._profile_country_requires_manual("Update your payment method"))
        self.assertFalse(AppleFlow._profile_country_requires_manual("国家或地区 美国 继续"))


if __name__ == "__main__":
    unittest.main()
